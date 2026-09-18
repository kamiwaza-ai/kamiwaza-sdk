"""Session binding, the scopes it opens, and the unscoped sweep.

A session-bound ledger enters and leaves; a header-scoped one never leaves. The
sweep looks where a mis-scoped write would land, and degrades to a report rather
than a false proof when the leave itself failed.
"""

from __future__ import annotations

import importlib

import pytest
from kamiwaza_sdk.exceptions import (
    APIError,
)
from tests.integration import _workroom_support as support
from tests.unit.workrooms._ledger_fakes import (
    make_ledger,
)

pytestmark = pytest.mark.unit


def test_session_datasets_are_deleted_by_urn_while_bound_then_the_session_leaves() -> (
    None
):
    ledger, workrooms, owner, _ = make_ledger(binds_session=True)
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


def test_every_scoped_client_the_ledger_opens_is_closed() -> None:
    ledger, _, owner, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("scoped-clients")
    ledger.create_dataset("scoped-clients-data", workroom_id)

    ledger.remove_remaining()

    assert owner.opened_scopes == 2, "the header path did not scope its writes"
    assert owner.closed_scopes == owner.opened_scopes


def test_a_session_bound_ledger_enters_instead_of_scoping() -> None:
    ledger, workrooms, owner, _ = make_ledger(binds_session=True)
    workroom_id = ledger.create_workroom("bound-writes")
    ledger.create_dataset("bound-writes-data", workroom_id)
    workrooms.calls.clear()

    ledger.remove_remaining()

    assert ("enter", workroom_id) in workrooms.calls
    assert owner.opened_scopes == 0


def test_a_bound_ledger_leaves_even_when_only_the_test_entered() -> None:
    """The test enters on its own, so the ledger cannot condition on its own enters."""
    ledger, workrooms, owner, _ = make_ledger(binds_session=True)
    ledger.create_workroom("entered-by-the-test")
    # A workroom that exists but this ledger did not create, so the binding is
    # one only the test knows about. It has to exist: entering a workroom that
    # does not is a 404, and a double that let that through is what hid a
    # teardown failure on the real deployment.
    entered_by_the_test = "workroom-the-test-entered"
    workrooms.rooms[entered_by_the_test] = "not-this-ledgers"
    owner.workrooms.enter(entered_by_the_test)
    workrooms.calls.clear()

    ledger.remove_remaining()

    assert ("leave", None) in workrooms.calls


def test_a_dataset_outlives_the_workroom_it_was_recorded_for() -> None:
    """A recorded dataset whose workroom the test already deleted still sweeps.

    The session-scoping test keeps its refused write recorded so the unscoped
    sweep can reach a mis-scoped copy, and deletes both workrooms before
    teardown. Entering a deleted workroom is a 404, so a teardown that reaches
    for the recorded scope first must treat that as "not reachable here" and
    hand the dataset to the sweep -- not fail the whole cleanup, which would
    stop the sweep from ever running.
    """
    ledger, workrooms, owner, _ = make_ledger(binds_session=True)
    workroom_id = ledger.create_workroom("retired")
    name = ledger.record_dataset("refused-unbound", workroom_id)
    ledger.declined(name)
    workrooms.deleted.add(workroom_id)  # the test deleted it before teardown

    ledger.remove_remaining()

    assert ("leave", None) in workrooms.calls, "the sweep never ran"


def test_a_dataset_left_behind_by_a_deleted_workroom_is_still_swept() -> None:
    """The point of keeping the record: a copy elsewhere is still removed."""
    ledger, workrooms, owner, _ = make_ledger(binds_session=True)
    workroom_id = ledger.create_workroom("retired")
    name = ledger.record_dataset("refused-unbound", workroom_id)
    urn = ledger.dataset_urn(name)
    ledger.declined(name)
    owner.store[urn] = name  # refused, but the server persisted it anyway
    workrooms.deleted.add(workroom_id)

    ledger.remove_remaining()

    assert urn not in owner.store, "the mis-scoped copy survived teardown"


def test_a_registered_cleanup_runs_inside_the_ledgers_own_teardown() -> None:
    """A fixture that registers here has no finalizer of its own to be skipped."""
    ledger, _, owner, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("registered")
    ledger.create_dataset("registered-data", workroom_id)
    order: list[str] = []
    ledger.register_cleanup("close the clients", lambda: order.append("registered"))

    ledger.remove_remaining()

    assert order == ["registered"]
    assert owner.store == {}, "the registered step replaced the removals"


def test_a_registered_cleanup_that_fails_does_not_stop_the_removals() -> None:
    ledger, _, owner, _ = make_ledger(binds_session=False)
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
    ledger, _, _, _ = make_ledger(binds_session=False)
    closed: list[str] = []

    stack = module.scopes.__wrapped__(ledger)
    stack.callback(lambda: closed.append("closed"))
    ledger.remove_remaining()

    assert closed == ["closed"], "the ledger does not close the scoped clients"


def test_a_header_scoped_ledger_never_leaves() -> None:
    """It shares a client it never bound; leaving would unbind that session."""
    ledger, workrooms, _, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("header-scoped")
    ledger.create_dataset("header-scoped-data", workroom_id)
    workrooms.calls.clear()

    ledger.remove_remaining()

    assert ("leave", None) not in workrooms.calls


def test_a_dataset_missing_from_its_own_scope_is_swept_from_the_unscoped_view() -> None:
    """A scoping regression can put a write where the recorded scope cannot see it."""
    ledger, _, owner, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("mis-scoped")
    _name, urn = ledger.create_dataset("mis-scoped-data", workroom_id)
    owner.dataset_faults.mis_scoped = frozenset({urn})

    ledger.remove_remaining()

    assert urn not in owner.store, "the mis-scoped dataset was left behind"


def test_the_sweep_does_nothing_when_every_dataset_was_in_its_own_scope() -> None:
    """The control: a dataset removed in its scope is not looked for again."""
    ledger, workrooms, owner, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("well-scoped")
    _name, urn = ledger.create_dataset("well-scoped-data", workroom_id)

    ledger.remove_remaining()

    unscoped_reads = [call for call in workrooms.calls if call == ("get", None)]
    assert unscoped_reads == [], "the sweep read a dataset it had already removed"
    assert urn not in owner.store


def test_the_sweep_proves_the_dataset_it_deleted_is_gone() -> None:
    """A delete the server accepts but does not apply must not read as removed."""
    ledger, _, owner, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("sweep-proof")
    _name, urn = ledger.create_dataset("sweep-proof-data", workroom_id)
    owner.dataset_faults.mis_scoped = frozenset({urn})
    owner.dataset_faults.delete_applies = False

    with pytest.raises(support.CleanupError, match="still readable"):
        ledger.remove_remaining()


def test_the_sweep_runs_after_the_leave_has_unbound_the_session() -> None:
    """Before the leave, the owner's own view is still the bound workroom."""
    ledger, workrooms, owner, _ = make_ledger(binds_session=True)
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
    ledger, workrooms, owner, _ = make_ledger(binds_session=True)
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


def test_the_sweep_attempts_every_dataset_not_only_the_first() -> None:
    """A report on the first would leave the rest neither swept nor named."""
    ledger, _, owner, _ = make_ledger(binds_session=False)
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
    ledger, workrooms, owner, _ = make_ledger(binds_session=True)
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
    ledger, workrooms, _, _ = make_ledger(binds_session=True)
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
