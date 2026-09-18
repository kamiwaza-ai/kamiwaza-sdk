"""Workroom session scoping against a live deployment (ENG-12325, plan T03).

``test_entered_workroom_scopes_datasets_and_leave_clears_the_scope`` is the
evidence-bearing test for ``workrooms.session-scoping``. A disposable user
enters a workroom and creates a catalog dataset with no ``X-Workroom-Id``
header, so the session binding alone decides where it lands. The test proves:

* the bound view lists it; rebound to a second workroom, that view lists the
  second workroom's own dataset and not the first's;
* a conditional leave naming a workroom the session is not bound to is
  refused with 409 ``session_conflict`` and leaves the binding in place (raw
  API: the SDK's ``leave`` takes no ``expected_workroom_id``);
* after ``leave`` the unscoped (Global) view lists neither, and each
  workroom's explicit view lists only its own;
* scope denial is fail-closed and typed: a caller who is not a member is
  refused a workroom's view with 403 "Workroom access denied", and the
  unbound session is refused a write to the workroom with 403
  "Authenticated workroom context missing.", and a by-URN read in that
  workroom's scope, which the same caller uses successfully on an existing
  dataset, proves nothing landed there. Where a refused write would land if
  it were accepted is not evidenced: this identity holds no dataset outside a
  workroom, so an unscoped read has no positive control.

Each view's absence check runs after a read under the same identity has
listed the dataset (listings are polled for presence, which covers a catalog
that indexes writes asynchronously), and each deleted workroom is read before
it is deleted. Two behaviors observed on the 1.2.1 evidence deployment
(2026-09-17) shape the flow: a session binding takes precedence over
``X-Workroom-Id``, so other views are read after ``leave``; and writes need a
binding, so datasets are deleted while bound.

This evidences session scoping on a deployment that holds the binding server
side, which is what the 1.2.1 evidence deployment does. ``enter`` also answers
with an ``access_token``, and on a deployment that carries the binding in that
token instead this test would not hold: ``WorkroomService.enter``
documents that the SDK never installs a returned token, so the client would
keep its password-grant token and the headerless reads below would run
unbound. The test does not install it either -- doing so would evidence
token-carried scoping rather than the session binding named here -- so a
failure on such a deployment is that gap, not a platform regression.

Ids are read out of the enter and leave responses before they are asserted,
because those responses carry an access token. Not covered: a response that
fails the SDK's validation echoes the body it could not parse, tokens included,
in the pydantic error.

Opt-in: ``KAMIWAZA_TEST_DISPOSABLE_IDENTITIES=1``; see
``_workroom_disposable_user``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack

import pytest
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.services.context import ContextService

from . import _workroom_disposable_user as disposable
from ._workroom_support import (
    WorkroomLedger,
    await_condition,
    dataset_payload,
    dataset_urns,
    expect_not_found,
    refusal,
    when_authority_projected,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    disposable.requires_disposable_identities,
]

GLOBAL_WORKROOM_ID = ContextService.DEFAULT_WORKROOM_ID


@pytest.fixture(scope="session", autouse=True)
def refuse_credential_echo_early(pytestconfig: pytest.Config) -> None:
    """Refuse before any live fixture authenticates.

    Session-scoped and autouse, so it runs ahead of the session fixtures that
    hold the shared administrator's credentials; it requests only the config,
    so it cannot pull them in itself.
    """
    disposable.refuse_credential_echo(pytestconfig)


@pytest.fixture
def workroom_user(
    request: pytest.FixtureRequest,
    live_kamiwaza_client: KamiwazaClient,
    live_server_available: str,
    client_factory: disposable.ClientFactory,
) -> Iterator[disposable.WorkroomUser]:
    yield from disposable.session_user(
        request, live_kamiwaza_client, client_factory, live_server_available
    )


@pytest.fixture
def ledger(
    live_kamiwaza_client: KamiwazaClient, workroom_user: disposable.WorkroomUser
) -> WorkroomLedger:
    """Removes what a failed test left, as part of the user's own teardown.

    Registered on the user rather than torn down here: pytest stops calling
    the remaining finalizers when one raises a ``BaseException``, so a Ctrl-C
    in a finalizer of this fixture would leave the account behind.
    """
    record = WorkroomLedger(
        admin=live_kamiwaza_client, owner=workroom_user.client, binds_session=True
    )
    workroom_user.cleanups.append(
        ("remove what the workroom ledger recorded", record.remove_remaining)
    )
    return record


@pytest.fixture
def scopes(ledger: WorkroomLedger) -> ExitStack:
    """Closes every workroom-scoped client a test opens, during the ledger's own
    teardown: a finalizer of its own would abort that teardown on a Ctrl-C."""
    stack = ExitStack()
    ledger.register_cleanup("close the workroom-scoped clients", stack.close)
    return stack


def test_entered_workroom_scopes_datasets_and_leave_clears_the_scope(
    live_kamiwaza_client: KamiwazaClient, ledger: WorkroomLedger, scopes: ExitStack
) -> None:
    user = ledger.owner
    first = ledger.create_workroom("scope-a")
    second = ledger.create_workroom("scope-b")

    # Ids are read out of the responses first: enter and leave responses carry
    # an access_token field (a token in Lite/SAML), and a failing assertion
    # renders its operands.
    entered_id = str(
        when_authority_projected(lambda: user.workrooms.enter(first)).workroom_id
    )
    assert entered_id == first
    first_name, first_urn = ledger.create_dataset("scoped-a", first)
    await_condition(
        lambda: first_urn in dataset_urns(user, first_name),
        "the bound workroom omits its dataset",
    )

    rebound_id = str(
        when_authority_projected(lambda: user.workrooms.enter(second)).workroom_id
    )
    assert rebound_id == second
    second_name, second_urn = ledger.create_dataset("scoped-b", second)
    await_condition(
        lambda: second_urn in dataset_urns(user, second_name),
        "the rebound workroom omits its dataset",
    )
    assert first_urn not in dataset_urns(user, first_name), (
        "a second view lists the first's"
    )

    with pytest.raises(APIError) as mismatched_leave:
        user.post("/workrooms/leave", json={"expected_workroom_id": first})
    status, detail = refusal(mismatched_leave.value)
    conflict = detail.get("code") if isinstance(detail, dict) else detail
    assert (status, conflict) == (409, "session_conflict")
    assert second_urn in dataset_urns(user, second_name), (
        "a refused conditional leave unbound"
    )

    left = str(user.workrooms.leave().workroom_id)
    assert left == GLOBAL_WORKROOM_ID
    first_scoped = scopes.enter_context(user.workroom_scope(first))
    second_scoped = scopes.enter_context(user.workroom_scope(second))
    await_condition(
        lambda: dataset_urns(first_scoped, first_name) == {first_urn},
        "the first workroom's explicit view omits its dataset",
    )
    await_condition(
        lambda: dataset_urns(second_scoped, second_name) == {second_urn},
        "the second workroom's explicit view omits its dataset",
    )
    assert dataset_urns(user, first_name) == set(), "Global lists a workroom dataset"
    assert dataset_urns(user, second_name) == set(), "leave kept the session scoped"
    assert dataset_urns(first_scoped, second_name) == set()
    assert dataset_urns(second_scoped, first_name) == set()

    # The control is the member's identical request above (first_scoped,
    # querying first_name), which listed the dataset; only the identity differs.
    with pytest.raises(APIError) as not_a_member:
        dataset_urns(
            scopes.enter_context(live_kamiwaza_client.workroom_scope(first)),
            first_name,
        )
    assert refusal(not_a_member.value) == (403, "Workroom access denied")
    assert first_scoped.catalog.datasets.get(first_urn).urn == first_urn
    refused_name = ledger.record_dataset("refused-unbound", first)
    with pytest.raises(APIError) as unbound_write:
        # The POST alone: create_dataset follows it with a read, whose refusal
        # would pass this check even if the write had been accepted.
        first_scoped.catalog.datasets.create(dataset_payload(refused_name))
    assert refusal(unbound_write.value) == (
        403,
        "Authenticated workroom context missing.",
    )
    # The ledger recorded this name before the write; only this test saw the
    # server refuse it, so tell the ledger before proving absence below.
    ledger.declined(refused_name)
    refused_urn = ledger.dataset_urn(refused_name)
    # Scoped, where the write was addressed and where the same identity is
    # shown to read an existing dataset (first_urn, above). Whether a refused
    # write lands in some other scope is not evidenced: this identity has no
    # dataset outside a workroom, so an unscoped read has no positive control.
    expect_not_found(
        lambda: first_scoped.catalog.datasets.get(refused_urn), "the refused dataset"
    )
    ledger.forget_dataset(refused_name)

    for workroom_id, name, urn in (
        (first, first_name, first_urn),
        (second, second_name, second_urn),
    ):
        user.workrooms.enter(workroom_id)
        # Bound per pass: these probes run inside the iteration today, so late
        # binding changes nothing, but a probe that outlived its pass would
        # read the other workroom's dataset.
        await_condition(
            lambda urn=urn, name=name: urn in dataset_urns(user, name),
            "the bound query omits the dataset",
        )
        assert user.catalog.datasets.get(urn).urn == urn
        user.catalog.datasets.delete(urn)
        await_condition(
            lambda urn=urn, name=name: urn not in dataset_urns(user, name),
            "a deleted dataset still lists",
        )
        ledger.proven_gone(lambda urn=urn: user.catalog.datasets.get(urn), name)
    left_again = str(user.workrooms.leave().workroom_id)
    assert left_again == GLOBAL_WORKROOM_ID

    listed = {str(w.id) for w in user.workrooms.list(include_archived=True)}
    assert {first, second} <= listed, "the owner listing omits its workrooms"
    for workroom_id in (first, second):
        assert str(user.workrooms.get(workroom_id).id) == workroom_id
        user.workrooms.delete(workroom_id)
        with pytest.raises(NotFoundError):
            user.workrooms.get(workroom_id)
    remaining = {str(w.id) for w in user.workrooms.list(include_archived=True)}
    assert remaining.isdisjoint({first, second}), "a deleted workroom still lists"
    ledger.forget_workroom(first)
    ledger.forget_workroom(second)
