"""Shared support for the ENG-12325 live workroom evidence tests.

The live tests run on a shared deployment, so a failure at any line must not
leave workrooms or datasets behind unnoticed. These tests pin, without a
deployment, that every cleanup step runs and every failure is reported, that a
workroom or dataset whose create call failed is still found and removed, that
removal is proven by an authoritative read rather than a search, and that the
projection retry and listing poll are bounded and narrow.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from collections.abc import Callable
from types import SimpleNamespace
from uuid import uuid4

import pytest
from kamiwaza_sdk.exceptions import APIError, NotFoundError
from tests.integration import _workroom_support as support

pytestmark = pytest.mark.unit


def test_attempt_all_runs_every_step_and_reports_each_failure() -> None:
    ran: list[str] = []

    def fail(label: str) -> None:
        ran.append(label)
        raise APIError(f"{label} broke", status_code=500)

    with pytest.raises(support.CleanupError) as raised:
        support.attempt_all(
            [
                ("first step", lambda: fail("first")),
                ("second step", lambda: ran.append("second")),
                ("third step", lambda: fail("third")),
            ]
        )

    assert ran == ["first", "second", "third"]
    message = str(raised.value)
    assert "first step" in message and "third step" in message
    assert "second step" not in message
    assert str(raised.value.__cause__) == "first broke"


def test_attempt_all_is_silent_when_every_step_succeeds() -> None:
    ran: list[str] = []

    support.attempt_all([("only step", lambda: ran.append("only"))])

    assert ran == ["only"], "the step was never run"


def test_attempt_all_runs_the_steps_after_an_interrupt_and_re_raises_it() -> None:
    """A Ctrl-C during one cleanup call must not skip the deletions after it."""
    ran: list[str] = []

    def interrupted() -> None:
        raise KeyboardInterrupt

    def broken() -> None:
        raise APIError("delete failed", status_code=500)

    with pytest.raises(KeyboardInterrupt) as stopped:
        support.attempt_all(
            [
                ("interrupted step", interrupted),
                ("failed step", broken),
                ("later step", lambda: ran.append("later")),
            ]
        )

    assert ran == ["later"], "cleanup stopped at the interrupt"
    # The interrupt wins the raise, so the failed step is named in its cause.
    assert "failed step" in str(stopped.value.__cause__)


def test_a_pytest_outcome_in_a_cleanup_step_is_reported_not_re_raised() -> None:
    """A skip inside cleanup must not decide the test's own outcome."""
    ran: list[str] = []

    def skipping() -> None:
        pytest.skip("a cleanup step should not skip the test")

    steps = [("skipping step", skipping), ("later step", lambda: ran.append("later"))]
    try:
        support.attempt_all(steps)
    except support.CleanupError as reported:
        assert "skipping step" in str(reported)
    except BaseException as escaped:  # noqa: BLE001 - the defect this test pins
        raise AssertionError(
            f"a cleanup step's {type(escaped).__name__} escaped attempt_all; "
            "raised here as a failure, since a skip would read as green"
        ) from None
    else:
        raise AssertionError("attempt_all reported nothing")

    assert ran == ["later"]


def test_a_system_exit_in_a_cleanup_step_still_runs_the_rest() -> None:
    ran: list[str] = []

    def exiting() -> None:
        raise SystemExit(3)

    with pytest.raises(SystemExit) as stopped:
        support.attempt_all(
            [("exiting step", exiting), ("later step", lambda: ran.append("later"))]
        )

    assert ran == ["later"] and stopped.value.code == 3


def test_a_nested_cleanups_summary_survives_an_interrupt() -> None:
    """The ledger's removals run as one step of the disposable user's teardown.

    Re-raising the inner interrupt would overwrite its cause, so what the inner
    call could not remove has to be folded into the outer summary.
    """

    def refused() -> None:
        raise APIError("delete refused", status_code=409)

    def interrupted() -> None:
        raise KeyboardInterrupt

    def inner() -> None:
        support.attempt_all(
            [("delete dataset d-1", refused), ("enter the workroom", interrupted)]
        )

    with pytest.raises(KeyboardInterrupt) as stopped:
        support.attempt_all(
            [("remove what the ledger recorded", inner), ("close", lambda: None)]
        )

    named = str(stopped.value.__cause__)
    assert "delete dataset d-1" in named, f"the inner summary was lost: {named}"


def test_expect_not_found_accepts_the_sdks_404() -> None:
    reads: list[str] = []

    def read() -> None:
        reads.append("read")
        raise NotFoundError("gone")

    support.expect_not_found(read, "the thing")

    assert reads == ["read"], "the absence proof never read the resource"


def test_expect_not_found_rejects_a_readable_resource() -> None:
    with pytest.raises(AssertionError, match="the thing is still readable"):
        support.expect_not_found(lambda: object(), "the thing")


@pytest.mark.parametrize(
    "error",
    [
        APIError("server broke", status_code=500),
        # The SDK raises NotFoundError for every JSON 404, so a plain 404 is
        # not a shape it produces and must not be read as absence.
        APIError("untyped 404", status_code=404),
    ],
)
def test_expect_not_found_propagates_any_other_error(error: APIError) -> None:
    def read() -> None:
        raise error

    with pytest.raises(APIError) as raised:
        support.expect_not_found(read, "the thing")
    assert raised.value is error


class _Workrooms:
    """Owner-side workroom calls, with a server-side store."""

    def __init__(
        self,
        *,
        fail_create_after_storing: bool = False,
        create_error: APIError | None = None,
    ) -> None:
        self.rooms: dict[str, str] = {}
        self.deleted: set[str] = set()
        self.archived: set[str] = set()
        self.calls: list[tuple[str, str | None]] = []
        self.fail_create_after_storing = fail_create_after_storing
        self.create_error = create_error
        # The session's own binding: a bound client reads in that workroom.
        self.bound: str | None = None

    def create(self, name, _workroom_type):
        if self.create_error is not None:
            # As a server does: the name is never stored.
            raise self.create_error
        workroom_id = str(uuid4())
        self.rooms[workroom_id] = name
        if self.fail_create_after_storing:
            raise ValueError("response validation failed after the create")
        return SimpleNamespace(id=workroom_id)

    def list(self, *, include_archived=False):
        return [
            SimpleNamespace(id=workroom_id, name=name)
            for workroom_id, name in self.rooms.items()
            if workroom_id not in self.deleted
            and (include_archived or workroom_id not in self.archived)
        ]

    def get(self, workroom_id):
        if workroom_id in self.deleted or workroom_id not in self.rooms:
            raise NotFoundError("gone")
        return SimpleNamespace(id=workroom_id)

    def enter(self, workroom_id):
        self.calls.append(("enter", workroom_id))
        self.bound = workroom_id

    def leave(self):
        self.calls.append(("leave", None))
        self.bound = None


class _Datasets:
    """The catalog dataset client: POST-only create, by-URN get and delete.

    ``faults`` is shared by every view of one owner (bound or header-scoped), so
    a test can make the server misbehave however the ledger reaches it.
    """

    def __init__(
        self,
        store: dict[str, str],
        calls: list,
        scope: str | None,
        faults: SimpleNamespace,
        bound: Callable[[], str | None] | None = None,
    ) -> None:
        self.store = store
        self.calls = calls
        self.scope = scope
        self.faults = faults
        # An unscoped client still reads in whatever the session entered.
        self.bound = bound or (lambda: None)

    def create(self, payload):
        self.calls.append(("create", self.scope))
        urn = self.faults.returned_urn or support.expected_dataset_urn(payload.name)
        self.store[urn] = payload.name
        if self.faults.create_error is not None:
            raise self.faults.create_error
        return urn

    def get(self, urn):
        self.calls.append(("get", self.scope))
        if urn not in self.store or self._hidden_here(urn):
            raise NotFoundError("Dataset not found")
        return SimpleNamespace(urn=urn)

    def _hidden_here(self, urn):
        """A mis-scoped dataset: readable unscoped, absent from any workroom."""
        scope = self.scope if self.scope is not None else self.bound()
        return scope is not None and urn in self.faults.mis_scoped

    def delete(self, urn):
        self.calls.append(("delete", self.scope))
        if urn not in self.store or self._hidden_here(urn):
            raise NotFoundError("Dataset not found")
        if self.faults.delete_applies:
            del self.store[urn]


class _Owner:
    def __init__(self, workrooms: _Workrooms) -> None:
        self.workrooms = workrooms
        self.store: dict[str, str] = {}
        self.dataset_faults = SimpleNamespace(
            returned_urn=None,
            create_error=None,
            delete_applies=True,
            mis_scoped=frozenset(),
        )
        self.opened_scopes = 0
        self.closed_scopes = 0
        self.catalog = SimpleNamespace(
            datasets=_Datasets(
                self.store,
                workrooms.calls,
                None,
                self.dataset_faults,
                bound=lambda: workrooms.bound,
            )
        )

    def workroom_scope(self, workroom_id):
        self.opened_scopes += 1
        return _ScopedClient(self, workroom_id)


class _ScopedClient:
    """A header-scoped client: a context manager that counts its closes."""

    def __init__(self, owner: _Owner, workroom_id: str) -> None:
        self.owner = owner
        self.catalog = SimpleNamespace(
            datasets=_Datasets(
                owner.store, owner.workrooms.calls, workroom_id, owner.dataset_faults
            )
        )

    def __enter__(self) -> _ScopedClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.owner.closed_scopes += 1


class _Admin:
    def __init__(self, workrooms: _Workrooms) -> None:
        self.workrooms = SimpleNamespace(admin_delete=self._admin_delete)
        self._owner_rooms = workrooms
        self.admin_deleted: list[str] = []

    def _admin_delete(self, workroom_id):
        self.admin_deleted.append(workroom_id)
        self._owner_rooms.deleted.add(workroom_id)


def _ledger(
    *,
    binds_session: bool,
    fail_create: bool = False,
    create_error: APIError | None = None,
):
    workrooms = _Workrooms(
        fail_create_after_storing=fail_create, create_error=create_error
    )
    owner = _Owner(workrooms)
    admin = _Admin(workrooms)
    ledger = support.WorkroomLedger(
        admin=admin, owner=owner, binds_session=binds_session
    )
    return ledger, workrooms, owner, admin


def test_a_workroom_whose_create_call_failed_is_removed_by_name() -> None:
    ledger, workrooms, _, admin = _ledger(binds_session=False, fail_create=True)

    with pytest.raises(ValueError):
        ledger.create_workroom("orphan")
    ledger.remove_remaining()

    assert admin.admin_deleted == list(workrooms.rooms)


def test_an_archived_workroom_whose_create_failed_is_found_by_name() -> None:
    ledger, workrooms, _, admin = _ledger(binds_session=False, fail_create=True)
    with pytest.raises(ValueError):
        ledger.create_workroom("archived-orphan")
    workrooms.archived.update(workrooms.rooms)

    ledger.remove_remaining()

    assert admin.admin_deleted == list(workrooms.rooms)


def test_forgotten_workrooms_are_left_alone_and_the_rest_are_removed() -> None:
    ledger, workrooms, _, admin = _ledger(binds_session=False)
    kept = ledger.create_workroom("removed-by-test")
    left = ledger.create_workroom("left-by-failure")
    workrooms.deleted.add(kept)
    ledger.forget_workroom(kept)

    ledger.remove_remaining()

    assert admin.admin_deleted == [left]


def test_remove_remaining_fails_when_a_workroom_stays_readable() -> None:
    ledger, _, _, admin = _ledger(binds_session=False)
    ledger.create_workroom("stubborn")
    admin.workrooms.admin_delete = lambda workroom_id: None

    with pytest.raises(support.CleanupError, match="stubborn"):
        ledger.remove_remaining()


@pytest.mark.parametrize(("binds_session", "scoped"), [(True, False), (False, True)])
def test_create_dataset_posts_once_through_the_binding_or_the_header(
    binds_session: bool, scoped: bool
) -> None:
    ledger, workrooms, owner, _ = _ledger(binds_session=binds_session)
    workroom_id = ledger.create_workroom("writer")
    workrooms.calls.clear()

    name, urn = ledger.create_dataset("data", workroom_id)

    assert workrooms.calls == [("create", workroom_id if scoped else None)]
    assert urn == support.expected_dataset_urn(name)
    assert owner.store == {urn: name}


def test_a_changed_urn_scheme_is_refused_and_the_created_dataset_still_removed() -> (
    None
):
    ledger, _, owner, _ = _ledger(binds_session=True)
    workroom_id = ledger.create_workroom("scheme")
    returned = "urn:li:dataset:(something,else,PROD)"
    owner.dataset_faults.returned_urn = returned

    with pytest.raises(AssertionError, match="URN"):
        ledger.create_dataset("data", workroom_id)
    assert list(owner.store) == [returned]

    ledger.remove_remaining()

    assert owner.store == {}


def test_session_datasets_are_deleted_by_urn_while_bound_then_the_session_leaves() -> (
    None
):
    ledger, workrooms, owner, _ = _ledger(binds_session=True)
    workroom_id = ledger.create_workroom("bound")
    ledger.create_dataset("bound-data", workroom_id)
    workrooms.calls.clear()

    ledger.remove_remaining()

    assert workrooms.calls[:4] == [
        ("enter", workroom_id),
        ("delete", None),
        ("get", None),
        ("leave", None),
    ]
    assert owner.store == {}


def test_a_recorded_dataset_the_test_saw_refused_is_proven_absent() -> None:
    """A refused write leaves only a record; removal must not need a search."""
    ledger, workrooms, _, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("scoped")
    name = ledger.record_dataset("never-created", workroom_id)
    ledger.declined(name)  # the test saw the server refuse its own write
    workrooms.calls.clear()

    ledger.remove_remaining()

    assert ("enter", workroom_id) not in workrooms.calls
    assert workrooms.calls[:2] == [("delete", workroom_id), ("get", workroom_id)]


def test_a_recorded_dataset_with_no_known_outcome_is_reported() -> None:
    """Absent from its scope and from the unscoped view is not proof it never was."""
    ledger, _, _, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("unknown-outcome")
    name = ledger.record_dataset("unknown", workroom_id)

    with pytest.raises(support.CleanupError, match=re.escape(name)):
        ledger.remove_remaining()


@pytest.mark.parametrize("binds_session", [True, False])
def test_a_dataset_stored_behind_a_failed_create_call_is_still_removed(
    binds_session: bool,
) -> None:
    ledger, _, owner, _ = _ledger(binds_session=binds_session)
    workroom_id = ledger.create_workroom("half-created")
    owner.dataset_faults.create_error = ValueError("response failed after the create")

    with pytest.raises(ValueError, match="after the create"):
        ledger.create_dataset("half-created-data", workroom_id)
    assert owner.store, "the fake server did not store the dataset"

    ledger.remove_remaining()

    assert owner.store == {}


def test_a_dataset_still_readable_after_deletion_fails_cleanup() -> None:
    ledger, _, owner, _ = _ledger(binds_session=True)
    workroom_id = ledger.create_workroom("sticky")
    name, _ = ledger.create_dataset("sticky-data", workroom_id)
    owner.dataset_faults.delete_applies = False

    with pytest.raises(support.CleanupError, match=name):
        ledger.remove_remaining()


def test_expected_dataset_urn_matches_the_v121_scheme() -> None:
    """The scheme every live run re-checks against the URN the server answers."""
    assert (
        support.expected_dataset_urn("sdk-evidence-x")
        == "urn:li:dataset:(urn:li:dataPlatform:s3,sdk-evidence-x,PROD)"
    )


def _authority_pending() -> APIError:
    return APIError(
        "unavailable",
        status_code=503,
        response_data={"detail": "authorization_unavailable"},
    )


def test_when_authority_projected_retries_until_the_call_succeeds() -> None:
    outcomes: list[object] = [_authority_pending(), _authority_pending(), "done"]
    slept: list[float] = []

    def call() -> object:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert support.when_authority_projected(call, sleep=slept.append) == "done"
    assert len(slept) == 2


@pytest.mark.parametrize(
    "error",
    [
        APIError(
            "denied",
            status_code=403,
            response_data={"detail": "Workroom access denied"},
        ),
        APIError("down", status_code=503, response_data={"detail": "something else"}),
    ],
)
def test_when_authority_projected_propagates_any_other_error(error: APIError) -> None:
    calls: list[int] = []

    def call() -> None:
        calls.append(1)
        raise error

    with pytest.raises(APIError) as raised:
        support.when_authority_projected(call, sleep=lambda _: None)
    assert raised.value is error
    assert calls == [1]


def test_when_authority_projected_gives_up_at_the_deadline() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 50:
            raise RuntimeError("the retry did not stop at its deadline")
        now[0] += seconds

    def call() -> None:
        raise _authority_pending()

    with pytest.raises(APIError) as raised:
        support.when_authority_projected(
            call, timeout=5.0, clock=lambda: now[0], sleep=sleep
        )
    assert raised.value.status_code == 503
    assert now[0] >= 5.0


def test_await_condition_polls_until_true() -> None:
    results = [False, False, True]
    slept: list[float] = []

    support.await_condition(
        lambda: results.pop(0), "never", attempts=5, delay=1.0, sleep=slept.append
    )

    assert slept == [1.0, 1.0]


def test_await_condition_fails_with_its_message_after_the_last_attempt() -> None:
    slept: list[float] = []

    with pytest.raises(AssertionError, match="still not there"):
        support.await_condition(
            lambda: False, "still not there", attempts=3, delay=0.5, sleep=slept.append
        )
    assert slept == [0.5, 0.5]


class _AdminListing:
    """The administrator listing, one fixed page per offset."""

    def __init__(self, pages: dict[int, list]) -> None:
        self.pages = pages
        self.skips: list[int] = []
        self.include_deleted: list[bool] = []

    def admin_list(self, *, include_deleted: bool, skip: int, limit: int):
        self.skips.append(skip)
        self.include_deleted.append(include_deleted)
        return self.pages.get(skip, [])


def _rooms(*ids: str) -> list:
    return [SimpleNamespace(id=workroom_id) for workroom_id in ids]


def test_find_admin_workroom_pages_until_it_finds_the_workroom() -> None:
    listing = _AdminListing({0: _rooms("a", "b"), 2: _rooms("c", "target")})
    admin = SimpleNamespace(workrooms=listing)

    found = support.find_admin_workroom(
        admin, "target", include_deleted=True, page_size=2
    )

    assert found is not None and found.id == "target"
    assert listing.skips == [0, 2]
    assert listing.include_deleted == [True, True]


def test_find_admin_workroom_answers_none_at_the_first_short_page() -> None:
    listing = _AdminListing({0: _rooms("a", "b"), 2: _rooms("c")})
    admin = SimpleNamespace(workrooms=listing)

    assert (
        support.find_admin_workroom(admin, "target", include_deleted=False, page_size=2)
        is None
    )
    assert listing.skips == [0, 2]
    assert listing.include_deleted == [False, False]


def test_find_admin_workroom_stops_at_its_page_cap() -> None:
    listing = _AdminListing({skip: _rooms("x", "y") for skip in range(0, 20, 2)})
    admin = SimpleNamespace(workrooms=listing)

    with pytest.raises(AssertionError, match="did not end within 3 pages"):
        support.find_admin_workroom(
            admin, "target", include_deleted=False, page_size=2, max_pages=3
        )
    assert listing.skips == [0, 2, 4]


def test_dataset_payload_names_an_s3_dataset_under_the_evidence_prefix() -> None:
    payload = support.dataset_payload("sdk-evidence-x-1")

    assert (payload.name, payload.platform) == ("sdk-evidence-x-1", "s3")
    assert payload.properties == {"path": "s3://sdk-evidence/sdk-evidence-x-1.json"}


def test_every_scoped_client_the_ledger_opens_is_closed() -> None:
    ledger, _, owner, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("scoped-clients")
    ledger.create_dataset("scoped-clients-data", workroom_id)

    ledger.remove_remaining()

    assert owner.opened_scopes == 2, "the header path did not scope its writes"
    assert owner.closed_scopes == owner.opened_scopes


def test_a_session_bound_ledger_enters_instead_of_scoping() -> None:
    ledger, workrooms, owner, _ = _ledger(binds_session=True)
    workroom_id = ledger.create_workroom("bound-writes")
    ledger.create_dataset("bound-writes-data", workroom_id)
    workrooms.calls.clear()

    ledger.remove_remaining()

    assert ("enter", workroom_id) in workrooms.calls
    assert owner.opened_scopes == 0


def test_a_bound_ledger_leaves_even_when_only_the_test_entered() -> None:
    """The test enters on its own, so the ledger cannot condition on its own enters."""
    ledger, workrooms, owner, _ = _ledger(binds_session=True)
    ledger.create_workroom("entered-by-the-test")
    owner.workrooms.enter("whatever-the-test-entered")
    workrooms.calls.clear()

    ledger.remove_remaining()

    assert ("leave", None) in workrooms.calls


def test_a_registered_cleanup_runs_inside_the_ledgers_own_teardown() -> None:
    """A fixture that registers here has no finalizer of its own to be skipped."""
    ledger, _, owner, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("registered")
    ledger.create_dataset("registered-data", workroom_id)
    order: list[str] = []
    ledger.register_cleanup("close the clients", lambda: order.append("registered"))

    ledger.remove_remaining()

    assert order == ["registered"]
    assert owner.store == {}, "the registered step replaced the removals"


def test_a_registered_cleanup_that_fails_does_not_stop_the_removals() -> None:
    ledger, _, owner, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("registered-failure")
    ledger.create_dataset("registered-failure-data", workroom_id)

    def broken() -> None:
        raise APIError("closing a scoped client broke", status_code=500)

    ledger.register_cleanup("close the clients", broken)

    with pytest.raises(support.CleanupError, match="close the clients"):
        ledger.remove_remaining()

    assert owner.store == {}, "a failed registered step stopped the removals"


@pytest.mark.parametrize(
    "module_name",
    ["test_workroom_lifecycle_live", "test_workroom_export_live"],
)
def test_the_scopes_fixture_closes_through_the_ledger(module_name: str) -> None:
    """A finalizer of its own would abort the consolidated teardown on a Ctrl-C."""
    module = importlib.import_module(f"tests.integration.{module_name}")
    ledger, _, _, _ = _ledger(binds_session=False)
    closed: list[str] = []

    stack = module.scopes.__wrapped__(ledger)
    stack.callback(lambda: closed.append("closed"))
    ledger.remove_remaining()

    assert closed == ["closed"], "the ledger does not close the scoped clients"


def test_forgetting_one_dataset_leaves_the_others_recorded() -> None:
    """T03 forgets its refused write while its own datasets are still recorded."""
    ledger, _, owner, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("forget-one")
    _kept_name, kept_urn = ledger.create_dataset("kept", workroom_id)
    forgotten_name, forgotten_urn = ledger.create_dataset("forgotten", workroom_id)

    ledger.forget_dataset(forgotten_name)
    ledger.remove_remaining()

    assert kept_urn not in owner.store, "a recorded dataset was not removed"
    assert forgotten_urn in owner.store, "a forgotten dataset was removed anyway"


def test_a_header_scoped_ledger_never_leaves() -> None:
    """It shares a client it never bound; leaving would unbind that session."""
    ledger, workrooms, _, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("header-scoped")
    ledger.create_dataset("header-scoped-data", workroom_id)
    workrooms.calls.clear()

    ledger.remove_remaining()

    assert ("leave", None) not in workrooms.calls


def test_a_workroom_no_listing_shows_is_named_as_unconfirmed() -> None:
    """Absence from a listing cannot tell "never created" from "not shown"."""
    ledger, workrooms, _, admin = _ledger(binds_session=False, fail_create=True)
    with pytest.raises(ValueError):
        ledger.create_workroom("vanished")
    workrooms.rooms.clear()

    (name,) = ledger._workrooms

    with pytest.raises(support.CleanupError, match=re.escape(name)) as reported:
        ledger.remove_remaining()

    assert isinstance(reported.value.__cause__, support.UnconfirmedResource)
    assert admin.admin_deleted == []


def test_create_dataset_waits_out_owner_authority_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wait the guard below credits to each ledger.create_dataset call."""
    ledger, _, _, _ = _ledger(binds_session=False)
    waited: list[object] = []
    projected = support.when_authority_projected

    def counted(call, **kwargs):
        waited.append(call)
        return projected(call, **kwargs)

    monkeypatch.setattr(support, "when_authority_projected", counted)
    workroom_id = ledger.create_workroom("one-wait")
    assert waited == [], "creating a workroom waits, which the guard does not count"
    ledger.create_dataset("one-wait-data", workroom_id)
    assert len(waited) == 1, "create_dataset no longer waits exactly once"
    ledger.remove_remaining()
    assert len(waited) == 1, "cleanup waits, which the guard does not count"


_LIVE_TESTS = Path(support.__file__).parent
_DISPOSABLE_USER_MODULES = (
    # Every test in these two modules runs as the disposable user, whose token
    # cannot be refreshed; the export test uses the shared admin client.
    "test_workroom_lifecycle_live.py",
    "test_workroom_admin_live.py",
)
_WAITS = ("await_condition", "when_authority_projected")
# What the ledger waits on the test's behalf, pinned by the test above.
_HELPER_WAITS = {"create_dataset": "when_authority_projected"}


class _BoundedWaits(ast.NodeVisitor):
    """Count the bounded waits one run of a live test makes.

    A call inside a ``for`` over a literal sequence counts once per element.
    Any other loop, and a comprehension holding a wait, raises rather than
    undercounting: a shape this cannot read must be counted by hand.

    What it does not see, because it reads one function body and two call
    names: a wait made by a helper other than ``ledger.create_dataset``, or by
    a fixture. Adding either means adding it to ``_HELPER_WAITS`` or counting
    it by hand; the consequence of missing one is a token that expires
    mid-run, which fails loudly in cleanup rather than corrupting evidence.
    """

    def __init__(self) -> None:
        self.waits = dict.fromkeys(_WAITS, 0)
        self._factor = 1

    def visit_For(self, node: ast.For) -> None:
        # The iterable is evaluated once, outside the loop's own multiplier.
        self.visit(node.iter)
        if not isinstance(node.iter, ast.Tuple | ast.List) or any(
            isinstance(element, ast.Starred) for element in node.iter.elts
        ):
            # A starred element, ``(*items, 1)``, has no knowable length.
            raise AssertionError(
                f"line {node.lineno}: this guard cannot count a loop over "
                "anything but a literal sequence of known length"
            )
        outer, self._factor = self._factor, self._factor * len(node.iter.elts)
        for child in node.body:
            self.visit(child)
        self._factor = outer
        for child in node.orelse:
            self.visit(child)

    def visit_While(self, node: ast.While) -> None:
        raise AssertionError(f"line {node.lineno}: a while loop is not bounded here")

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        # visit_For does not run for this node, so refuse rather than skip it.
        raise AssertionError(f"line {node.lineno}: an async for is not counted here")

    def _refuse_deferred(self, node: ast.AST) -> None:
        """A wait the guard would count once however many times it runs."""
        if any(
            isinstance(inner, ast.Call)
            and getattr(inner.func, "id", getattr(inner.func, "attr", ""))
            in (*_WAITS, *_HELPER_WAITS)
            for inner in ast.walk(node)
        ):
            raise AssertionError(
                f"line {node.lineno}: a wait inside a comprehension, lambda or "
                "nested function; this guard counts it once however many times "
                "it runs"
            )

    visit_ListComp = _refuse_deferred
    visit_SetComp = _refuse_deferred
    visit_DictComp = _refuse_deferred
    visit_GeneratorExp = _refuse_deferred
    visit_Lambda = _refuse_deferred
    visit_FunctionDef = _refuse_deferred
    visit_AsyncFunctionDef = _refuse_deferred

    def visit_Call(self, node: ast.Call) -> None:
        called = ""
        if isinstance(node.func, ast.Name):
            called = node.func.id
        elif isinstance(node.func, ast.Attribute):
            called = node.func.attr
        if called in self.waits:
            self.waits[called] += self._factor
        elif called in _HELPER_WAITS:
            self.waits[_HELPER_WAITS[called]] += self._factor
        self.generic_visit(node)


def _waits_in_source(module: str, source: str) -> dict[str, dict[str, int]]:
    """Count the waits each test in one module makes, whatever shape it takes."""
    counted: dict[str, dict[str, int]] = {}
    # ast.walk, not tree.body: a class-based or async test would otherwise be
    # skipped silently, and with it the disposable-user gate below.
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
            node.name.startswith("test_")
        ):
            requested = {arg.arg for arg in node.args.args}
            assert requested & {"workroom_user", "ledger"}, (
                f"{module}::{node.name} does not run as the disposable user, "
                "so this budget no longer describes the module"
            )
            walker = _BoundedWaits()
            for statement in node.body:
                walker.visit(statement)
            counted[f"{module}::{node.name}"] = walker.waits
    return counted


def _waits_per_live_test() -> dict[str, dict[str, int]]:
    counted: dict[str, dict[str, int]] = {}
    for module in _DISPOSABLE_USER_MODULES:
        counted |= _waits_in_source(module, (_LIVE_TESTS / module).read_text())
    return counted


# Named, not read, so a mutated count cannot move this test's node id.
_RECORDED_WAITS = {
    "await_condition": "LISTING_POLLS_PER_RUN",
    "when_authority_projected": "PROJECTION_RETRIES_PER_RUN",
}


@pytest.mark.parametrize("wait", list(_RECORDED_WAITS))
def test_the_recorded_wait_counts_match_the_live_tests(wait: str) -> None:
    """The token budget is only as honest as these counts: derive them."""
    recorded = getattr(support, _RECORDED_WAITS[wait])
    counted = _waits_per_live_test()
    assert counted, "no live test was counted"
    worst, waits = max(counted.items(), key=lambda item: item[1][wait])

    assert waits[wait] == recorded, (
        f"{worst} makes {waits[wait]} {wait} waits, not the {recorded} recorded"
    )


@pytest.mark.parametrize(
    ("source", "refused"),
    [
        ("for item in items:\n    await_condition(check, 'f')\n", "literal sequence"),
        ("while True:\n    await_condition(check, 'f')\n", "while loop"),
        ("[await_condition(check, 'f') for _ in (1, 2)]\n", "comprehension"),
        ("probe = lambda: await_condition(check, 'f')\n", "lambda"),
        ("def probe():\n    await_condition(check, 'f')\n", "nested function"),
        ("{k: when_authority_projected(c) for k in (1, 2)}\n", "comprehension"),
        ("for item in (*items, 1):\n    await_condition(check, 'f')\n", "known length"),
        (
            # The enclosing async def is refused first, as a nested function.
            (
                "async def run():\n    async for item in items:\n"
                "        await_condition(check, 'f')\n"
            ),
            "nested function",
        ),
    ],
    ids=[
        "non-literal-for",
        "while",
        "list-comprehension",
        "lambda",
        "nested-def",
        "dict-comprehension",
        "starred-literal",
        "async-for",
    ],
)
def test_the_wait_guard_refuses_a_shape_it_cannot_count(
    source: str, refused: str
) -> None:
    """Undercounting silently is the failure this guard exists to prevent."""
    walker = _BoundedWaits()

    with pytest.raises(AssertionError, match=refused):
        for statement in ast.parse(source).body:
            walker.visit(statement)


def test_the_wait_guard_counts_a_wait_that_builds_the_loop_iterable() -> None:
    """It runs once, before the loop; counting it zero times understates the bound."""
    walker = _BoundedWaits()

    source = "for value in (await_condition(check, 'f'), other()):\n    pass\n"
    for statement in ast.parse(source).body:
        walker.visit(statement)

    assert walker.waits["await_condition"] == 1


def test_a_class_based_or_async_live_test_is_counted_too() -> None:
    """Walking only the module body would skip both shapes, and the gate with them."""
    source = (
        "class TestRetirement:\n"
        "    def test_in_a_class(self, ledger):\n"
        "        await_condition(check, 'f')\n"
        "\n"
        "async def test_async(workroom_user):\n"
        "    await_condition(check, 'f')\n"
    )

    counted = _waits_in_source("m.py", source)

    assert set(counted) == {"m.py::test_in_a_class", "m.py::test_async"}


def test_a_live_test_that_is_not_the_disposable_user_fails_the_gate() -> None:
    with pytest.raises(AssertionError, match="does not run as the disposable user"):
        _waits_in_source("m.py", "def test_other(live_kamiwaza_client):\n    pass\n")


def test_the_wait_guard_refuses_an_async_for_on_its_own() -> None:
    """Reached directly: an enclosing async def is refused before it."""
    source = "async def run():\n    async for item in items:\n        pass\n"
    async_for = ast.parse(source).body[0].body[0]
    walker = _BoundedWaits()

    with pytest.raises(AssertionError, match="async for"):
        walker.visit(async_for)


def test_the_wait_guard_counts_a_comprehension_that_holds_no_wait() -> None:
    """The refusal is about waits, not about comprehensions."""
    walker = _BoundedWaits()

    for statement in ast.parse("names = [str(w) for w in rooms]\n").body:
        walker.visit(statement)

    assert walker.waits == dict.fromkeys(_WAITS, 0)


def test_the_budget_value_is_pinned() -> None:
    """A canary: changing a constant must be re-derived against the token floor."""
    assert support.BOUNDED_WAIT_BUDGET_SECONDS == 232.0


def test_a_dataset_missing_from_its_own_scope_is_swept_from_the_unscoped_view() -> None:
    """A scoping regression can put a write where the recorded scope cannot see it."""
    ledger, _, owner, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("mis-scoped")
    _name, urn = ledger.create_dataset("mis-scoped-data", workroom_id)
    owner.dataset_faults.mis_scoped = frozenset({urn})

    ledger.remove_remaining()

    assert urn not in owner.store, "the mis-scoped dataset was left behind"


def test_the_sweep_does_nothing_when_every_dataset_was_in_its_own_scope() -> None:
    """The control: a dataset removed in its scope is not looked for again."""
    ledger, workrooms, owner, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("well-scoped")
    _name, urn = ledger.create_dataset("well-scoped-data", workroom_id)

    ledger.remove_remaining()

    unscoped_reads = [call for call in workrooms.calls if call == ("get", None)]
    assert unscoped_reads == [], "the sweep read a dataset it had already removed"
    assert urn not in owner.store


@pytest.mark.parametrize(
    ("status", "reported"),
    [(400, False), (409, False), (502, True), (None, True)],
    ids=["declined-400", "declined-409", "unknown-502", "unknown-no-status"],
)
def test_only_an_undecided_workroom_create_is_reported(
    status: int | None, reported: bool
) -> None:
    """A 4xx created nothing; anything else leaves the outcome unknown."""
    ledger, _, _, _ = _ledger(
        binds_session=False, create_error=APIError("create failed", status_code=status)
    )

    with pytest.raises(APIError):
        ledger.create_workroom("decline")

    if reported:
        with pytest.raises(support.CleanupError, match="not in the listing"):
            ledger.remove_remaining()
    else:
        ledger.remove_remaining()


def test_the_sweep_proves_the_dataset_it_deleted_is_gone() -> None:
    """A delete the server accepts but does not apply must not read as removed."""
    ledger, _, owner, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("sweep-proof")
    _name, urn = ledger.create_dataset("sweep-proof-data", workroom_id)
    owner.dataset_faults.mis_scoped = frozenset({urn})
    owner.dataset_faults.delete_applies = False

    with pytest.raises(support.CleanupError, match="still readable"):
        ledger.remove_remaining()


def test_the_sweep_runs_after_the_leave_has_unbound_the_session() -> None:
    """Before the leave, the owner's own view is still the bound workroom."""
    ledger, workrooms, owner, _ = _ledger(binds_session=True)
    workroom_id = ledger.create_workroom("order")
    _name, urn = ledger.create_dataset("order-data", workroom_id)
    owner.dataset_faults.mis_scoped = frozenset({urn})
    workrooms.calls.clear()

    ledger.remove_remaining()

    kinds = [call for call, _scope in workrooms.calls]
    assert "leave" in kinds, "the session was never unbound"
    assert kinds.index("leave") < len(kinds) - 1, "nothing ran after the leave"


def test_a_failed_leave_makes_the_sweep_report_rather_than_prove() -> None:
    """A 404 from a still-bound client says nothing about the unscoped view."""
    ledger, workrooms, owner, _ = _ledger(binds_session=True)
    workroom_id = ledger.create_workroom("still-bound")
    name, urn = ledger.create_dataset("still-bound-data", workroom_id)
    owner.dataset_faults.mis_scoped = frozenset({urn})

    def refuse_leave() -> None:
        raise APIError("leave refused", status_code=409)

    workrooms.leave = refuse_leave  # type: ignore[method-assign]

    with pytest.raises(support.CleanupError) as reported:
        ledger.remove_remaining()

    # The reason matters: "still bound" says the sweep could not reach the
    # unscoped view, which is different from "absent from both views".
    assert "still bound" in str(reported.value), str(reported.value)
    assert name in str(reported.value)


def test_every_generated_name_is_unique_to_this_run() -> None:
    """Cleanup can delete by name, so two runs must not generate the same one."""
    names = {support.unique_name("collide") for _ in range(2000)}

    assert len(names) == 2000
    # A literal, not the constant: comparing the generated suffix to the
    # constant that generated it would stay green if the constant shrank.
    assert support.NAME_SUFFIX_HEX == 16, "64 bits is what the safety argument rests on"
    suffixes = {name.rsplit("-", 1)[1] for name in names}
    assert all(len(suffix) == 16 for suffix in suffixes)


def test_the_sweep_attempts_every_dataset_not_only_the_first() -> None:
    """A report on the first would leave the rest neither swept nor named."""
    ledger, _, owner, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("exhaustive")
    first_name, first_urn = ledger.create_dataset("first", workroom_id)
    _second_name, second_urn = ledger.create_dataset("second", workroom_id)
    owner.dataset_faults.mis_scoped = frozenset({first_urn, second_urn})
    del owner.store[first_urn]  # the first is absent from the unscoped view too

    with pytest.raises(support.CleanupError) as reported:
        ledger.remove_remaining()

    assert first_name in str(reported.value), "the unaccounted dataset was not named"
    assert second_urn not in owner.store, "the sweep stopped at the first dataset"


def test_a_still_bound_sweep_names_every_dataset_it_cannot_reach() -> None:
    ledger, workrooms, owner, _ = _ledger(binds_session=True)
    workroom_id = ledger.create_workroom("bound-many")
    first_name, first_urn = ledger.create_dataset("first", workroom_id)
    second_name, second_urn = ledger.create_dataset("second", workroom_id)
    owner.dataset_faults.mis_scoped = frozenset({first_urn, second_urn})

    def refuse_leave() -> None:
        raise APIError("leave refused", status_code=409)

    workrooms.leave = refuse_leave  # type: ignore[method-assign]

    with pytest.raises(support.CleanupError) as reported:
        ledger.remove_remaining()

    named = str(reported.value)
    assert first_name in named and second_name in named


def test_a_still_bound_sweep_says_nothing_about_a_declined_write() -> None:
    """The server created nothing, whether or not the sweep could reach Global."""
    ledger, workrooms, _, _ = _ledger(binds_session=True)
    workroom_id = ledger.create_workroom("bound-declined")
    name = ledger.record_dataset("declined", workroom_id)
    ledger.declined(name)

    def refuse_leave() -> None:
        raise APIError("leave refused", status_code=409)

    workrooms.leave = refuse_leave  # type: ignore[method-assign]

    with pytest.raises(support.CleanupError) as reported:
        ledger.remove_remaining()

    assert name not in str(reported.value), "a declined write was reported"
    assert "leave refused" in str(reported.value)


def test_proven_gone_forgets_only_after_the_read_answers_404() -> None:
    """One step, so an interrupt cannot land between the proof and the forget."""
    ledger, _, owner, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("proven")
    name, urn = ledger.create_dataset("proven-data", workroom_id)

    with pytest.raises(AssertionError, match="still readable"):
        ledger.proven_gone(lambda: SimpleNamespace(urn=urn), name)

    # The failed proof must not have forgotten it: cleanup still removes it.
    ledger.remove_remaining()
    assert urn not in owner.store, "a dataset was forgotten before it was proven gone"


def test_admin_workroom_ids_returns_every_id_one_traversal_saw() -> None:
    """The absence check and its control both come out of this set."""
    listing = _AdminListing({0: _rooms("a", "b"), 2: _rooms("c")})

    seen = support.admin_workroom_ids(
        SimpleNamespace(workrooms=listing), include_deleted=False, page_size=2
    )

    assert seen == {"a", "b", "c"}, "the traversal dropped rows"
    assert listing.skips == [0, 2], "the pages were not walked in order"


def test_admin_workroom_ids_forwards_include_deleted() -> None:
    listing = _AdminListing({0: _rooms("a")})

    support.admin_workroom_ids(
        SimpleNamespace(workrooms=listing), include_deleted=True, page_size=2
    )

    assert listing.include_deleted == [True]


def test_admin_workroom_ids_stops_at_its_page_cap() -> None:
    """A listing that never ends must fail loudly, not answer a partial set."""
    listing = _AdminListing({skip: _rooms("a", "b") for skip in range(0, 20, 2)})

    with pytest.raises(AssertionError, match="did not end within"):
        support.admin_workroom_ids(
            SimpleNamespace(workrooms=listing),
            include_deleted=False,
            page_size=2,
            max_pages=3,
        )


def test_the_admin_paging_defaults_match_the_sdks_own_ceiling() -> None:
    """1000 is the SDK's validated maximum; a smaller default widens the window."""
    assert support.ADMIN_PAGE_SIZE == 1000
    assert support.ADMIN_MAX_PAGES == 50


def test_dataset_urns_forwards_its_query() -> None:
    """The bound-view listings are per-name; dropping the query changes what they see."""
    seen: list[str | None] = []

    class _Catalog:
        def list_datasets(self, query=None):
            seen.append(query)
            return [SimpleNamespace(urn="urn:one")]

    urns = support.dataset_urns(SimpleNamespace(catalog=_Catalog()), "only-this-name")

    assert urns == {"urn:one"}
    assert seen == ["only-this-name"]


def test_a_declined_dataset_create_is_recorded_as_declined() -> None:
    """A 4xx write created nothing, so the sweep must not report it."""
    ledger, _, owner, _ = _ledger(binds_session=False)
    workroom_id = ledger.create_workroom("declined-write")
    owner.dataset_faults.create_error = APIError("refused", status_code=403)

    with pytest.raises(APIError):
        ledger.create_dataset("declined-data", workroom_id)
    owner.store.clear()  # the server kept nothing

    ledger.remove_remaining()  # no UnconfirmedResource for a declined write
