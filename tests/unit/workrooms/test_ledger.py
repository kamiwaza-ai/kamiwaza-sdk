"""The ledger's record-before-create discipline and its removals.

A workroom or dataset whose create call failed is still found and removed; a
resource the server declined was never created and must not be reported; and a
resource no listing shows is named rather than passed over.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from kamiwaza_sdk.exceptions import (
    APIError,
    BrokeredUserNotAllowlistedError,
    KamiwazaError,
    NativeRealmRequiredError,
)
from tests.integration import _workroom_support as support
from tests.unit.workrooms._ledger_fakes import (
    make_ledger,
)

pytestmark = pytest.mark.unit


def test_a_workroom_whose_create_call_failed_is_removed_by_name() -> None:
    ledger, workrooms, _, admin = make_ledger(binds_session=False, fail_create=True)

    with pytest.raises(ValueError):
        ledger.create_workroom("orphan")
    ledger.remove_remaining()

    assert admin.admin_deleted == list(workrooms.rooms)


def test_an_archived_workroom_whose_create_failed_is_found_by_name() -> None:
    ledger, workrooms, _, admin = make_ledger(binds_session=False, fail_create=True)
    with pytest.raises(ValueError):
        ledger.create_workroom("archived-orphan")
    workrooms.archived.update(workrooms.rooms)

    ledger.remove_remaining()

    assert admin.admin_deleted == list(workrooms.rooms)


def test_forgotten_workrooms_are_left_alone_and_the_rest_are_removed() -> None:
    ledger, workrooms, _, admin = make_ledger(binds_session=False)
    kept = ledger.create_workroom("removed-by-test")
    left = ledger.create_workroom("left-by-failure")
    workrooms.deleted.add(kept)
    ledger.forget_workroom(kept)

    ledger.remove_remaining()

    assert admin.admin_deleted == [left]


def test_remove_remaining_fails_when_a_workroom_stays_readable() -> None:
    ledger, _, _, admin = make_ledger(binds_session=False)
    ledger.create_workroom("stubborn")
    admin.workrooms.admin_delete = lambda workroom_id: None

    with pytest.raises(support.CleanupError, match="stubborn"):
        ledger.remove_remaining()


@pytest.mark.parametrize(("binds_session", "scoped"), [(True, False), (False, True)])
def test_create_dataset_posts_once_through_the_binding_or_the_header(
    binds_session: bool, scoped: bool
) -> None:
    ledger, workrooms, owner, _ = make_ledger(binds_session=binds_session)
    workroom_id = ledger.create_workroom("writer")
    workrooms.calls.clear()

    name, urn = ledger.create_dataset("data", workroom_id)

    assert workrooms.calls == [("create", workroom_id if scoped else None)]
    assert urn == support.expected_dataset_urn(name)
    assert owner.store == {urn: name}


def test_a_changed_urn_scheme_is_refused_and_the_created_dataset_still_removed() -> (
    None
):
    ledger, _, owner, _ = make_ledger(binds_session=True)
    workroom_id = ledger.create_workroom("scheme")
    returned = "urn:li:dataset:(something,else,PROD)"
    owner.dataset_faults.returned_urn = returned

    with pytest.raises(AssertionError, match="URN"):
        ledger.create_dataset("data", workroom_id)
    assert list(owner.store) == [returned]

    ledger.remove_remaining()

    assert owner.store == {}


def test_a_recorded_dataset_the_test_saw_refused_is_proven_absent() -> None:
    """A refused write leaves only a record; removal must not need a search."""
    ledger, workrooms, _, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("scoped")
    name = ledger.record_dataset("never-created", workroom_id)
    ledger.declined(name)  # the test saw the server refuse its own write
    workrooms.calls.clear()

    ledger.remove_remaining()

    assert ("enter", workroom_id) not in workrooms.calls
    assert workrooms.calls[:2] == [("delete", workroom_id), ("get", workroom_id)]


def test_a_recorded_dataset_with_no_known_outcome_is_reported() -> None:
    """Absent from its scope and from the unscoped view is not proof it never was."""
    ledger, _, _, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("unknown-outcome")
    name = ledger.record_dataset("unknown", workroom_id)

    with pytest.raises(support.CleanupError, match=re.escape(name)):
        ledger.remove_remaining()


@pytest.mark.parametrize("binds_session", [True, False])
def test_a_dataset_stored_behind_a_failed_create_call_is_still_removed(
    binds_session: bool,
) -> None:
    ledger, _, owner, _ = make_ledger(binds_session=binds_session)
    workroom_id = ledger.create_workroom("half-created")
    owner.dataset_faults.create_error = ValueError("response failed after the create")

    with pytest.raises(ValueError, match="after the create"):
        ledger.create_dataset("half-created-data", workroom_id)
    assert owner.store, "the fake server did not store the dataset"

    ledger.remove_remaining()

    assert owner.store == {}


def test_a_dataset_still_readable_after_deletion_fails_cleanup() -> None:
    ledger, _, owner, _ = make_ledger(binds_session=True)
    workroom_id = ledger.create_workroom("sticky")
    name, _ = ledger.create_dataset("sticky-data", workroom_id)
    owner.dataset_faults.delete_applies = False

    with pytest.raises(support.CleanupError, match=name):
        ledger.remove_remaining()


def test_forgetting_one_dataset_leaves_the_others_recorded() -> None:
    """T03 forgets its refused write while its own datasets are still recorded."""
    ledger, _, owner, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("forget-one")
    _kept_name, kept_urn = ledger.create_dataset("kept", workroom_id)
    forgotten_name, forgotten_urn = ledger.create_dataset("forgotten", workroom_id)

    ledger.forget_dataset(forgotten_name)
    ledger.remove_remaining()

    assert kept_urn not in owner.store, "a recorded dataset was not removed"
    assert forgotten_urn in owner.store, "a forgotten dataset was removed anyway"


def test_a_workroom_no_listing_shows_is_named_as_unconfirmed() -> None:
    """Absence from a listing cannot tell "never created" from "not shown"."""
    ledger, workrooms, _, admin = make_ledger(binds_session=False, fail_create=True)
    with pytest.raises(ValueError):
        ledger.create_workroom("vanished")
    workrooms.rooms.clear()

    (name,) = ledger._workrooms

    with pytest.raises(support.CleanupError, match=re.escape(name)) as reported:
        ledger.remove_remaining()

    assert isinstance(reported.value.__cause__, support.UnconfirmedResource)
    assert admin.admin_deleted == []


@pytest.mark.parametrize(
    ("status", "reported"),
    [(400, False), (409, False), (502, True), (None, True)],
    ids=["declined-400", "declined-409", "unknown-502", "unknown-no-status"],
)
def test_only_an_undecided_workroom_create_is_reported(
    status: int | None, reported: bool
) -> None:
    """A 4xx created nothing; anything else leaves the outcome unknown."""
    ledger, _, _, _ = make_ledger(
        binds_session=False, create_error=APIError("create failed", status_code=status)
    )

    with pytest.raises(APIError):
        ledger.create_workroom("decline")

    if reported:
        with pytest.raises(support.CleanupError, match="not in the listing"):
            ledger.remove_remaining()
    else:
        ledger.remove_remaining()


def test_a_declined_dataset_create_is_recorded_as_declined() -> None:
    """A 4xx write created nothing, so the sweep must not report it."""
    ledger, _, owner, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("declined-write")
    owner.dataset_faults.create_error = APIError("refused", status_code=403)

    with pytest.raises(APIError):
        ledger.create_dataset("declined-data", workroom_id)
    owner.store.clear()  # the server kept nothing

    ledger.remove_remaining()  # no UnconfirmedResource for a declined write


def test_a_typed_refusal_of_a_workroom_create_is_recorded_as_declined() -> None:
    """A refusal carrying a known ``detail.reason`` is not an ``APIError``.

    ``error_for_response`` answers a recognised ``(status, detail.reason)`` pair
    with a subclass registered in ``_REASON_TO_EXCEPTION``, and the client
    raises that object as-is. Most of those subclasses descend from
    ``KamiwazaError`` beside ``APIError`` rather than under it, so bookkeeping
    that watched only for ``APIError`` would miss the refusal and go on to
    report a workroom the server never created.
    """
    refusal = BrokeredUserNotAllowlistedError("not allowlisted", status_code=403)
    assert not isinstance(refusal, APIError), (
        "this test is only meaningful while the typed refusal is not an APIError"
    )
    ledger, _, _, _ = make_ledger(binds_session=False, create_error=refusal)

    with pytest.raises(KamiwazaError):
        ledger.create_workroom("typed-decline")

    ledger.remove_remaining()  # nothing was created, so nothing is reported


def test_a_typed_refusal_of_a_dataset_write_is_recorded_as_declined() -> None:
    """The dataset write needs the same widening as the workroom create."""
    refusal = NativeRealmRequiredError("native realm required", status_code=403)
    assert not isinstance(refusal, APIError), (
        "this test is only meaningful while the typed refusal is not an APIError"
    )
    ledger, _, owner, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("typed-declined-write")
    owner.dataset_faults.create_error = refusal

    with pytest.raises(KamiwazaError):
        ledger.create_dataset("typed-declined-data", workroom_id)
    owner.store.clear()  # the server kept nothing

    ledger.remove_remaining()  # no UnconfirmedResource for a declined write


@pytest.mark.parametrize(
    ("spelling", "status", "declined"),
    [
        ("status", 403, True),
        ("status", 502, False),
        ("status_code", 403, True),
        ("status_code", 502, False),
    ],
    ids=["status-4xx", "status-5xx", "status_code-4xx", "status_code-5xx"],
)
def test_the_decline_predicate_reads_either_status_spelling(
    spelling: str, status: int, declined: bool
) -> None:
    """One predicate serves both modules, so it reads both attribute names."""
    error = RuntimeError("refused")
    setattr(error, spelling, status)

    assert support.declined_by_the_server(error) is declined


def test_the_decline_predicate_says_nothing_without_a_status() -> None:
    """No status means the outcome is unknown, which is not a decline."""
    assert support.declined_by_the_server(RuntimeError("no status")) is False


def test_a_non_int_status_does_not_mask_a_4xx_status_code() -> None:
    """A spelling holding a non-int is passed over, not treated as the answer.

    Taking the first non-``None`` attribute would answer False here and the
    ledger would report a workroom the server had already refused to create.
    The parametrized test above varies the spelling but sets only one at a
    time, so this crossed case is the one it cannot reach.
    """
    error = RuntimeError("refused")
    error.status = "403 Forbidden"  # type: ignore[attr-defined]
    error.status_code = 403  # type: ignore[attr-defined]

    assert support.declined_by_the_server(error) is True


def test_proven_gone_forgets_only_after_the_read_answers_404() -> None:
    """One step, so an interrupt cannot land between the proof and the forget."""
    ledger, _, owner, _ = make_ledger(binds_session=False)
    workroom_id = ledger.create_workroom("proven")
    name, urn = ledger.create_dataset("proven-data", workroom_id)

    with pytest.raises(AssertionError, match="still readable"):
        ledger.proven_gone(lambda: SimpleNamespace(urn=urn), name)

    # The failed proof must not have forgotten it: cleanup still removes it.
    ledger.remove_remaining()
    assert urn not in owner.store, "a dataset was forgotten before it was proven gone"
