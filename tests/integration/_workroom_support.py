"""Shared support for the live workroom evidence tests (ENG-12325).

The tests run on a shared deployment, so what a failed test leaves behind is
somebody else's problem. Each test removes what it created and proves it gone
in its own body; ``WorkroomLedger`` covers the failure path. It records a
workroom's name, or a dataset's name and deterministic URN, before the call
that creates it, so a create that fails after the server acted is still removed
by name or URN -- or, when no listing shows it, named in an
``UnconfirmedResource`` rather than passed over -- and forgets a
resource only once the test has proven it gone. A dataset is
deleted by URN and proven gone by a by-URN read that must 404, never by a
search; the tests prove their own deletions the same way before forgetting.
That read runs in the workroom the dataset was recorded for; a dataset it does
not find there is swept against the unscoped (Global) view as well, which is
where an unbound or mis-scoped write lands. A write that landed in some third
workroom is still invisible to this ledger. ``attempt_all`` runs
every cleanup step even when an earlier one fails, and reports every failure.

Two consistency windows are waited out rather than raced: a new workroom's
owner authority can still be projecting when the first guarded call arrives
(``when_authority_projected``, the rule the SDK's own
``enter_projected_workroom`` applies), and a catalog listing can trail a write
where the catalog is indexed asynchronously (the DataHub implementation), so
the tests poll a dataset's presence or absence in a listing
(``await_condition``). The 1.2.1 evidence deployment runs the SQL-backed
``kamiwaza`` catalog, where the first poll already sees the write. Neither
helper retries anything else. Their bounds limit the
number of attempts, not the length of one call: SDK requests set no HTTP
timeout, so a single hung request is not interrupted, and the client has its
own in-request retry budget for other 503 shapes that this module's budget does
not count.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import TypeVar

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, KamiwazaError, NotFoundError
from kamiwaza_sdk.schemas.catalog import DatasetCreate
from kamiwaza_sdk.schemas.workrooms import Workroom

Step = tuple[str, Callable[[], object]]
T = TypeVar("T")

AUTHORITY_PROJECTION_SECONDS = 30.0
LISTING_POLL_ATTEMPTS = 8
LISTING_POLL_DELAY_SECONDS = 2.0
PROJECTION_RETRIES_PER_RUN = 4
"""Twice for session scoping's two enters, twice for its two dataset creates."""
LISTING_POLLS_PER_RUN = 8
"""Session scoping polls four times before its deletion loop and twice per pass."""
# Both counts are derived from the live tests' own source by a unit test, which
# multiplies the calls inside a loop, so no wait written directly in a live test
# body can go uncounted. A wait added inside a helper the tests call, or inside
# a fixture, is not seen: it has to be registered in that unit test's
# _HELPER_WAITS to be counted.
# The disposable user's token cannot be refreshed, so its lifetime must cover
# every bounded wait a run can make; a unit test pins this budget at or below
# the lifetime the helper requires. Each poll sleeps between attempts but not
# after the last one.
BOUNDED_WAIT_BUDGET_SECONDS = (
    PROJECTION_RETRIES_PER_RUN * AUTHORITY_PROJECTION_SECONDS
    + LISTING_POLLS_PER_RUN * (LISTING_POLL_ATTEMPTS - 1) * LISTING_POLL_DELAY_SECONDS
)
ADMIN_PAGE_SIZE = 1000
ADMIN_MAX_PAGES = 50


class CleanupError(RuntimeError):
    """One or more cleanup steps failed; the message names each of them."""


class UnconfirmedResource(RuntimeError):
    """A resource whose create call failed and which no listing shows."""


def refuse_unconfirmed(what: str) -> None:
    """Report a resource a failed create may have left; absence here is no proof.

    Raised, not warned: a warning is suppressible (``--disable-warnings``,
    ``-p no:warnings``), and this names a resource that may be on a shared
    deployment under a name only this run knows. ``attempt_all`` collects it,
    so the steps after it still run and its summary carries the name. It can
    arise only where a create call already failed, so it adds a report to a
    run that is failing anyway.
    """
    raise UnconfirmedResource(
        f"{what} is not in the listing; if its create call reached the "
        "server, this run could not find it"
    )


def expected_dataset_urn(name: str) -> str:
    """The URN v1.2.1 assigns an s3 dataset, as create_dataset returns it.

    Every run checks this against the URN the server actually answers, so a
    scheme change fails the create rather than silently missing the cleanup.
    """
    return f"urn:li:dataset:(urn:li:dataPlatform:s3,{name},PROD)"


NAME_SUFFIX_HEX = 16
"""64 random bits. Cleanup can delete by name, so a collision with a concurrent
run matters: the owner's own listing already scopes the search to this run's
identity wherever the owner is the disposable user, and this bounds the rest."""


def unique_name(prefix: str) -> str:
    return f"sdk-evidence-{prefix}-{uuid.uuid4().hex[:NAME_SUFFIX_HEX]}"


def dataset_urns(client: KamiwazaClient, query: str | None = None) -> set[str]:
    return {dataset.urn for dataset in client.catalog.list_datasets(query=query)}


def dataset_payload(name: str) -> DatasetCreate:
    """The catalog dataset these tests write: s3 metadata with no payload behind it."""
    return DatasetCreate(
        name=name,
        platform="s3",
        properties={"path": f"s3://sdk-evidence/{name}.json"},
    )


def _admin_pages(
    admin: KamiwazaClient,
    *,
    include_deleted: bool,
    page_size: int,
    max_pages: int,
) -> Iterator[list[Workroom]]:
    """Yield each page of the platform-wide listing until the listing ends.

    A page shorter than ``page_size`` ends the listing. Offset paging over a
    listing other runs are changing can skip a row once it spans more than one
    page; that bounds how much an absent result proves.

    Both callers traverse through this. A lookup and the control it is read
    against have to agree about what one traversal saw, and two copies of the
    loop could be fixed one at a time and stop agreeing.
    """
    for page_number in range(max_pages):
        page = admin.workrooms.admin_list(
            include_deleted=include_deleted,
            skip=page_number * page_size,
            limit=page_size,
        )
        yield page
        if len(page) < page_size:
            return
    raise AssertionError(f"the admin listing did not end within {max_pages} pages")


def find_admin_workroom(
    admin: KamiwazaClient,
    workroom_id: str,
    *,
    include_deleted: bool,
    page_size: int = ADMIN_PAGE_SIZE,
    max_pages: int = ADMIN_MAX_PAGES,
) -> Workroom | None:
    """Page the platform-wide listing until the workroom or the listing's end."""
    for page in _admin_pages(
        admin,
        include_deleted=include_deleted,
        page_size=page_size,
        max_pages=max_pages,
    ):
        for workroom in page:
            if str(workroom.id) == workroom_id:
                return workroom
    return None


def declined_by_the_server(error: BaseException) -> bool:
    """A 4xx: the server acted on the request and refused it, creating nothing.

    Both attribute spellings are read because more than one exception family
    reaches this predicate: ``KamiwazaError`` and its subclasses carry
    ``status_code``, and others carry ``status``. One predicate reading both
    keeps a caller from being fixed without its sibling.

    ``status`` is read first and ``status_code`` second; the first of them
    holding an ``int`` decides on its own value, and a spelling holding
    anything else is passed over rather than ending the search. Passing over
    matters: a present but unusable attribute would otherwise mask a usable
    one, and a 4xx this run did decline would read as an unknown outcome.

    Two spellings holding *different* ints are not reconciled -- position
    alone decides, so which one wins depends on the order above rather than on
    the severity. No exception the SDK raises carries both, so that case is
    unreached; it is called out because the ordering, not a rule, is what
    settles it.
    """
    for name in ("status", "status_code"):
        status = getattr(error, name, None)
        if isinstance(status, int):
            return 400 <= status < 500
    return False


def admin_workroom_ids(
    admin: KamiwazaClient,
    *,
    include_deleted: bool,
    page_size: int = ADMIN_PAGE_SIZE,
    max_pages: int = ADMIN_MAX_PAGES,
) -> set[str]:
    """Every workroom id one traversal of the platform-wide listing saw.

    An absence check and its control have to come from the same traversal: two
    lookups are two responses, and the SDK answers a payload without an
    ``items`` key with an empty list.
    """
    seen: set[str] = set()
    for page in _admin_pages(
        admin,
        include_deleted=include_deleted,
        page_size=page_size,
        max_pages=max_pages,
    ):
        seen.update(str(workroom.id) for workroom in page)
    return seen


def refusal(error: APIError) -> tuple[int | None, object]:
    """The status code and ``detail`` of a refused request."""
    payload = error.response_data
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return error.status_code, detail


def when_authority_projected(
    call: Callable[[], T],
    *,
    timeout: float = AUTHORITY_PROJECTION_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], object] = time.sleep,
) -> T:
    """Retry ``call`` only while it answers 503 ``authorization_unavailable``.

    That response means a new workroom's owner relation is still projecting.
    Every other error, including denials and other 503s, propagates at once,
    and the pending response itself propagates once ``timeout`` has passed.
    """
    deadline = clock() + timeout
    while True:
        try:
            return call()
        except APIError as error:
            if refusal(error) != (503, "authorization_unavailable"):
                raise
            remaining = deadline - clock()
            if remaining <= 0:
                raise
            sleep(min(1.0, remaining))


def await_condition(
    check: Callable[[], bool],
    failure: str,
    *,
    attempts: int = LISTING_POLL_ATTEMPTS,
    delay: float = LISTING_POLL_DELAY_SECONDS,
    sleep: Callable[[float], object] = time.sleep,
) -> None:
    """Poll ``check`` until it holds; fail with ``failure`` after the last try."""
    for attempt in range(attempts):
        if check():
            return
        if attempt < attempts - 1:
            sleep(delay)
    raise AssertionError(failure)


def rendered_with_summary(exc: BaseException, carried: BaseException | None) -> str:
    """``exc`` as text, with ``carried``'s ``CleanupError`` summary folded in.

    ``carried`` is passed separately rather than read off ``exc`` because the
    two callers pair them differently: naming a failed step folds in that
    step's own cause, while replacing a stop signal's cause folds the summary
    that signal carried into the *setup* failure now taking its place.

    A stop signal raised out of an ``attempt_all`` carries that call's summary
    as its cause, and that summary is the only place the resources it never
    reached are named -- ``str(KeyboardInterrupt())`` is empty, which is also
    why the type name is rendered when the text is. Anything re-raising such a
    signal with a different cause has to fold the old text in rather than
    replace it. Both places that do so call this, so the rule is written once
    instead of paraphrased twice.
    """
    detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    if isinstance(carried, CleanupError):
        detail = f"{detail} ({carried})"
    return detail


def _describe(description: str, exc: BaseException) -> str:
    """Name a failed step, folding in a nested cleanup's own summary.

    A step can itself be an ``attempt_all``: the ledger's removals run as one
    step of the disposable user's teardown.
    """
    return f"{description}: {rendered_with_summary(exc, exc.__cause__)}"


def attempt_all(steps: Iterable[Step]) -> None:
    """Run every step, then raise one error naming each step that failed.

    Catching ``BaseException`` is the point: a failed step must not stop the
    ones after it, and a Ctrl-C during one cleanup call must not skip the
    deletions and proofs that follow it. Nothing is swallowed: a
    ``KeyboardInterrupt`` or ``SystemExit`` is re-raised once the remaining
    steps have run, carrying the summary of every failed step -- including the
    steps of a nested ``attempt_all`` -- as its cause, and every other failure
    -- a ``pytest.skip`` or ``pytest.fail`` inside a cleanup step included,
    which must not decide the test's own outcome -- is reported in a
    ``CleanupError`` whose cause is the first of them.

    The cost is that cleanup cannot be interrupted: once it starts, every step
    runs, and a step that hangs cannot be escaped with a second Ctrl-C, because
    SDK requests set no HTTP timeout.
    """
    failures: list[tuple[str, BaseException]] = []
    for description, step in steps:
        try:
            step()
        except BaseException as exc:  # noqa: BLE001 - reported below, never swallowed
            failures.append((description, exc))
    if not failures:
        return
    summary = "; ".join(_describe(description, exc) for description, exc in failures)
    interrupt = next(
        (
            exc
            for _description, exc in failures
            if isinstance(exc, KeyboardInterrupt | SystemExit)
        ),
        None,
    )
    if interrupt is not None:
        # A Ctrl-C still stops the run, now that the later steps have run too.
        # It carries the summary as its cause, so a resource left behind by
        # another step is still named on the way out.
        raise interrupt from CleanupError(f"cleanup failed: {summary}")
    raise CleanupError(f"cleanup failed: {summary}") from failures[0][1]


def expect_not_found(read: Callable[[], object], what: str) -> None:
    """Prove a resource is gone: its read must answer 404.

    The SDK client raises ``NotFoundError`` for every JSON 404
    (``exceptions.error_for_response``), the catalog dataset client included.
    """
    try:
        read()
    except NotFoundError:
        return
    raise AssertionError(f"{what} is still readable")


@dataclass
class _RecordedDataset:
    name: str
    workroom_id: str
    urn: str


@dataclass
class WorkroomLedger:
    """Workrooms and datasets a test created and has not yet proven gone.

    ``owner`` creates everything. With ``binds_session`` the owner is a
    session that writes to a workroom by entering it; without, it writes
    through the ``X-Workroom-Id`` header. ``admin`` removes leftover
    workrooms: the admin delete route accepts any workroom, including one it
    has already deleted (observed on the 1.2.1 evidence deployment; the owner's
    own delete is not retry-safe, which is why cleanup uses the admin route).
    """

    admin: KamiwazaClient
    owner: KamiwazaClient
    binds_session: bool
    _workrooms: dict[str, str | None] = field(default_factory=dict)
    _datasets: list[_RecordedDataset] = field(default_factory=list)
    _registered: list[Step] = field(default_factory=list)
    _unaccounted: list[_RecordedDataset] = field(default_factory=list)
    _declined: set[str] = field(default_factory=set)
    _unbound: bool = False

    def create_workroom(self, prefix: str) -> str:
        name = unique_name(prefix)
        self._workrooms[name] = None
        try:
            workroom_id = str(self.owner.workrooms.create(name, "persistent").id)
        except KamiwazaError as error:
            # The server acted on the request and declined it, so nothing was
            # created and cleanup has nothing to look for. The catch is the base
            # class: a refusal whose body carries a known ``detail.reason``
            # raises a typed subclass that is not an ``APIError``, and narrowing
            # here would report a workroom the server never created.
            if declined_by_the_server(error):
                self._declined.add(name)
            raise
        self._workrooms[name] = workroom_id
        return workroom_id

    def record_dataset(self, prefix: str, workroom_id: str) -> str:
        """Record a dataset before a write that may or may not create it."""
        name = unique_name(prefix)
        self._datasets.append(
            _RecordedDataset(name, workroom_id, expected_dataset_urn(name))
        )
        return name

    def declined(self, name: str) -> None:
        """Record that the server refused the write the test made for ``name``.

        ``record_dataset`` names a write the test performs itself, so only the
        test knows how it ended. Without this, a failure between the refusal
        and ``forget_dataset`` would have cleanup report a dataset that the
        server never created.
        """
        self._declined.add(name)

    def dataset_urn(self, name: str) -> str:
        return next(d.urn for d in self._datasets if d.name == name)

    def create_dataset(self, prefix: str, workroom_id: str) -> tuple[str, str]:
        """Create a dataset in ``workroom_id`` and record its URN.

        A session owner writes through whatever workroom it has entered; this
        never enters on its behalf, because the binding is what those tests
        prove. The write waits out owner-authority projection on a new workroom.
        It uses the POST-only dataset client, which returns the URN.
        """
        name = self.record_dataset(prefix, workroom_id)
        payload = dataset_payload(name)
        with ExitStack() as scopes:
            writer = (
                self.owner
                if self.binds_session
                else scopes.enter_context(self.owner.workroom_scope(workroom_id))
            )
            datasets = writer.catalog.datasets
            # The POST alone is retried: create_dataset would re-send an accepted
            # POST if only its follow-up read answered 503.
            try:
                urn = when_authority_projected(lambda: datasets.create(payload))
            except KamiwazaError as error:
                # Declined, so nothing was written and the sweep has nothing
                # to report about this name. Base class for the same reason as
                # create_workroom: a typed refusal is not an ``APIError``.
                if declined_by_the_server(error):
                    self._declined.add(name)
                raise
        expected = self.dataset_urn(name)
        if urn != expected:
            # Record the dataset that exists, and drop the URN the server did
            # not use: cleanup would otherwise hunt a URN that never existed.
            self._datasets = [d for d in self._datasets if d.name != name]
            self._datasets.append(_RecordedDataset(name, workroom_id, urn))
            raise AssertionError(
                f"dataset URN {urn} differs from the expected {expected}; "
                "the v1.2.1 URN scheme this ledger relies on has changed"
            )
        return name, urn

    def forget_dataset(self, name: str) -> None:
        self._datasets = [d for d in self._datasets if d.name != name]

    def proven_gone(self, read: Callable[[], object], name: str) -> None:
        """Prove a dataset is gone and stop tracking it, in one step.

        A test that proves the deletion itself and then forgets separately
        leaves a window: an interrupt between the two has cleanup report a
        dataset the test had already proven absent.
        """
        expect_not_found(read, f"dataset {name}")
        self.forget_dataset(name)

    def forget_workroom(self, workroom_id: str) -> None:
        self._workrooms = {
            name: known
            for name, known in self._workrooms.items()
            if known != workroom_id
        }

    def register_cleanup(self, description: str, call: Callable[[], object]) -> None:
        """Take over a step another fixture would otherwise finalize itself.

        Pytest stops calling the remaining finalizers once one raises a
        ``BaseException``, so a separate finalizer closing clients could abort
        this ledger's removals. Registered steps run first.
        """
        self._registered.append((description, call))

    def remove_remaining(self) -> None:
        steps: list[Step] = [
            *self._registered,
            *(
                (f"delete dataset {d.name}", partial(self._remove_dataset, d))
                for d in self._datasets
            ),
        ]
        if self.binds_session:
            # Unconditional: the test enters on its own (that binding is what
            # these tests prove), so the ledger cannot know whether a session
            # is bound, and a failure between enter and the next step would
            # leave it bound. 1.2.1 accepts this step both from Global (the
            # session-scoping and retirement tests leave before teardown) and
            # on a session that never entered at all (the oversight test never
            # enters); every passing run has taken one of those two paths. A
            # ledger that only scopes by header never binds anything, and must
            # not unbind the client it shares. A leave that fails is reported
            # like any other step, and the removals after it still run.
            steps.append(("leave the workroom", self._leave))
        # After the leave, so the owner's own view is the unscoped one.
        steps.append(("sweep datasets absent from their scope", self._sweep_unscoped))
        steps.extend(
            (f"delete workroom {name}", partial(self._remove_workroom, name, known))
            for name, known in self._workrooms.items()
        )
        attempt_all(steps)

    @contextmanager
    def _writer(self, workroom_id: str) -> Iterator[KamiwazaClient]:
        """The owner entered into the workroom, or a scoped client closed after use."""
        if self.binds_session:
            self.owner.workrooms.enter(workroom_id)
            yield self.owner
            return
        with self.owner.workroom_scope(workroom_id) as scoped:
            yield scoped

    def _leave(self) -> None:
        """Leave, and record that the owner's own view is now the unscoped one."""
        self.owner.workrooms.leave()
        self._unbound = True

    def _remove_dataset(self, dataset: _RecordedDataset) -> None:
        try:
            with self._writer(dataset.workroom_id) as writer:
                datasets = writer.catalog.datasets
                try:
                    datasets.delete(dataset.urn)
                except NotFoundError:
                    # Never created, already deleted, or written into some other
                    # scope. The unscoped sweep decides between those.
                    self._unaccounted.append(dataset)
                expect_not_found(
                    partial(datasets.get, dataset.urn), f"dataset {dataset.name}"
                )
        except NotFoundError:
            # Reaching the recorded scope failed because the workroom is gone:
            # a test that deleted it before teardown, which the retirement flow
            # does and the session-scoping flow does for both of its own. That
            # is the same answer as a dataset the scope does not hold -- not
            # reachable here -- so it goes to the sweep. Raising instead would
            # fail this step and stop the sweep from running at all, which is
            # the one thing that could still find a mis-scoped copy.
            self._unaccounted.append(dataset)

    def _sweep_unscoped(self) -> None:
        """Remove a dataset that was not in the scope it was recorded for.

        The read in ``_remove_dataset`` runs in that scope, so it cannot see a
        dataset a scoping regression put elsewhere. Global is where an unbound
        or mis-scoped write lands, and is the one other place this ledger can
        look: a write that landed in a third workroom stays invisible to it.
        The URN carries this run's own name, whose suffix is 64 random bits.

        This only reads Global if the session was actually unbound, so a leave
        that failed turns the sweep into a report rather than a proof: a 404
        from a still-bound client says nothing about Global. A dataset absent
        from both views is reported too, unless the server declined its create.

        Every recorded dataset is attempted, and every one that cannot be
        accounted for is named: a report on the first would otherwise leave the
        rest neither swept nor mentioned.
        """
        if self.binds_session and not self._unbound:
            attempt_all(
                [
                    (
                        f"account for dataset {dataset.name}",
                        partial(
                            refuse_unconfirmed, f"dataset {dataset.name}, still bound"
                        ),
                    )
                    for dataset in self._unaccounted
                    # A decline means the server created nothing, whether or not
                    # the sweep could reach the unscoped view.
                    if dataset.name not in self._declined
                ]
            )
            return
        attempt_all(
            [
                (f"sweep dataset {dataset.name}", partial(self._sweep_one, dataset))
                for dataset in self._unaccounted
            ]
        )

    def _sweep_one(self, dataset: _RecordedDataset) -> None:
        """Remove one dataset from the unscoped view, or say it is unaccounted for."""
        datasets = self.owner.catalog.datasets
        try:
            datasets.delete(dataset.urn)
        except NotFoundError:
            if dataset.name not in self._declined:
                refuse_unconfirmed(f"dataset {dataset.name}")
            return
        expect_not_found(
            partial(datasets.get, dataset.urn),
            f"dataset {dataset.name}, swept from the unscoped view",
        )

    def _remove_workroom(self, name: str, known_id: str | None) -> None:
        ids = (
            [known_id]
            if known_id is not None
            else [
                str(w.id)
                for w in self.owner.workrooms.list(include_archived=True)
                if w.name == name
            ]
        )
        if not ids and name not in self._declined:
            # The create call failed and no listing shows the name. Unless the
            # server declined it outright, this cannot tell "never created"
            # from "not listed", so say so rather than pass silently.
            refuse_unconfirmed(f"workroom {name}")
        for workroom_id in ids:
            self.admin.workrooms.admin_delete(workroom_id)
            expect_not_found(
                partial(self.owner.workrooms.get, workroom_id), f"workroom {name}"
            )
