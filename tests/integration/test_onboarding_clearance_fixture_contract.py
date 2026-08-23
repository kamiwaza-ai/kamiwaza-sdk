"""Offline contracts for the disposable ENG-10096 live fixture."""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from tests.integration import _onboarding_clearance_fixture as fixture

pytestmark = pytest.mark.unit


def _receiver(*, response: dict, attributes: list[object]) -> SimpleNamespace:
    return SimpleNamespace(
        _request=Mock(return_value=response),
        cluster=SimpleNamespace(list_attributes=Mock(return_value=attributes)),
    )


def test_absent_clearance_schema_is_force_withdrawn_without_sdk_followup_get() -> None:
    receiver = _receiver(
        response={"state": "withdrawn", "subjects_holding_value": 0},
        attributes=[],
    )

    fixture._restore_clearance_schema(
        {"clearance_prior_state": None},
        receiver,
    )

    receiver._request.assert_called_once_with(
        "DELETE",
        "/cluster/attribute-schema/clearance",
        params={"force": "true", "subjects_holding_value": 0},
    )
    receiver.cluster.list_attributes.assert_called_once_with()


def test_deprecated_clearance_schema_uses_supported_delete_and_list_contract() -> None:
    receiver = _receiver(
        response={"state": "deprecated", "subjects_holding_value": 0},
        attributes=[SimpleNamespace(name="clearance", state="deprecated")],
    )

    fixture._restore_clearance_schema(
        {"clearance_prior_state": "deprecated"},
        receiver,
    )

    receiver._request.assert_called_once_with(
        "DELETE",
        "/cluster/attribute-schema/clearance",
    )
    receiver.cluster.list_attributes.assert_called_once_with()


def test_clearance_restore_fails_closed_when_postcondition_does_not_match() -> None:
    receiver = _receiver(
        response={"state": "withdrawn", "subjects_holding_value": 0},
        attributes=[SimpleNamespace(name="clearance", state="declared")],
    )

    with pytest.raises(
        AssertionError,
        match="clearance schema cleanup did not restore its prior state",
    ):
        fixture._restore_clearance_schema(
            {"clearance_prior_state": None},
            receiver,
        )


def _phase(timeline: list[str], name: str, *, error: Exception | None = None):
    def run(*_args) -> None:
        timeline.append(name)
        if error is not None:
            raise error

    return run


def test_cleanup_attempts_every_phase_before_reporting_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline: list[str] = []
    phases = (
        ("_recover_guest_subs", "guest recovery"),
        ("_remove_dataset_grants", "dataset grants"),
        ("_remove_dataset", "dataset"),
        ("_remove_requesters", "requesters"),
        ("_remove_gate_package", "gate package"),
        ("_restore_clearance_schema", "clearance schema"),
        ("_remove_federations", "federations"),
    )
    for symbol, label in phases:
        error = RuntimeError("cleanup canary") if label == "guest recovery" else None
        monkeypatch.setattr(fixture, symbol, _phase(timeline, label, error=error))

    with pytest.raises(AssertionError, match="guest recovery") as exc_info:
        fixture.cleanup({"receiver": object(), "initiator": object()})

    assert timeline == [label for _symbol, label in phases]
    assert "cleanup canary" not in str(exc_info.value)


@pytest.mark.parametrize(
    "primary_error",
    [None, ValueError("test body failed")],
    ids=["cleanup-only", "preserve-primary"],
)
def test_cleanup_failure_precedence(
    monkeypatch: pytest.MonkeyPatch,
    primary_error: BaseException | None,
) -> None:
    cleanup_error = RuntimeError("cleanup failed")
    cleanup = Mock(side_effect=cleanup_error)
    monkeypatch.setattr(fixture, "cleanup", cleanup)
    expected_error = primary_error if primary_error is not None else cleanup_error

    with pytest.raises(type(expected_error)) as exc_info:
        with fixture.cleanup_preserving_primary({"state": "owned"}):
            if primary_error is not None:
                raise primary_error

    assert exc_info.value is expected_error
    assert cleanup.call_args_list == [call({"state": "owned"})]
