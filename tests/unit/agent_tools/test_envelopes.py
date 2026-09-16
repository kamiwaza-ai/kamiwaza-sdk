from __future__ import annotations

import pytest

from kamiwaza_sdk.agent_tools.envelopes import (
    Failure,
    FailureKind,
    Success,
    entitlement_refusal,
    expired_wait,
    platform_fault,
    rejected_input,
    unmet_prerequisite,
)

pytestmark = pytest.mark.unit

_COMMON = {
    "status": "409",
    "code": "conflict",
    "timeout_ms": 30_000,
    "source": "platform",
    "request_id": "req-1",
}


def test_success_carries_only_data() -> None:
    """A caller must not have to inspect the envelope to learn it succeeded."""
    assert Success(data={"id": "m-1"}).as_payload() == {"data": {"id": "m-1"}}


def test_success_tolerates_an_unknown_result_field() -> None:
    """Permissive out: a field the platform adds later must not break a caller."""
    payload = Success(data={"id": "m-1", "field_added_next_release": 7}).as_payload()
    assert payload["data"]["field_added_next_release"] == 7


def test_expired_wait_requires_the_resource_identifier() -> None:
    """SC-003: without the identifier the only possible response is a duplicate."""
    with pytest.raises(ValueError, match="resource_id"):
        Failure(
            message="still converging",
            kind=FailureKind.EXPIRED_WAIT,
            **_COMMON,
        )


def test_expired_wait_helper_carries_identifier_and_resume_hint() -> None:
    failure = expired_wait(
        message="deployment did not settle within 30s",
        resource_id="dep-7",
        resume_with="get_deployment_serving",
        **_COMMON,
    )
    assert failure.resource_id == "dep-7"
    assert failure.detail == {"resumable": True, "resume_with": "get_deployment_serving"}


def test_a_failure_must_carry_an_actionable_message() -> None:
    with pytest.raises(ValueError, match="message"):
        Failure(message="   ", kind=FailureKind.PLATFORM_FAULT, **_COMMON)


def test_rejected_input_names_the_offending_field() -> None:
    """"Invalid arguments" without a field leaves an agent guessing."""
    failure = rejected_input(
        message="model_id is not a valid identifier",
        field_name="model_id",
        **_COMMON,
    )
    assert failure.detail["field"] == "model_id"
    assert failure.kind is FailureKind.REJECTED_INPUT


def test_entitlement_refusal_states_what_would_grant_access() -> None:
    failure = entitlement_refusal(
        message="you may not deploy models in this workroom",
        required="grant: workroom.deploy",
        **_COMMON,
    )
    assert failure.detail["required"] == "grant: workroom.deploy"


def test_unmet_prerequisite_names_what_was_missing() -> None:
    failure = unmet_prerequisite(
        message="no accelerator capacity available",
        missing="1 GPU with 24GB",
        **_COMMON,
    )
    assert failure.detail["missing"] == "1 GPU with 24GB"


def test_platform_fault_states_whether_retry_is_safe() -> None:
    """"Retry only if idempotent" is unfollowable without knowing which it was."""
    assert platform_fault(message="upstream 502", idempotent=True, **_COMMON).detail[
        "safe_to_retry"
    ]
    assert not platform_fault(message="upstream 502", **_COMMON).detail["safe_to_retry"]


@pytest.mark.parametrize(
    ("kind", "retryable"),
    [
        (FailureKind.REJECTED_INPUT, False),
        (FailureKind.ENTITLEMENT_REFUSAL, False),
        (FailureKind.UNMET_PREREQUISITE, True),
        (FailureKind.EXPIRED_WAIT, False),
        (FailureKind.PLATFORM_FAULT, True),
    ],
)
def test_retryability_matches_the_contract(kind: FailureKind, retryable: bool) -> None:
    """SC-010: a refusal must never read as retryable, and an expired wait is
    resumable rather than retryable — retrying it creates a duplicate."""
    assert kind.retryable is retryable


def test_all_five_kinds_are_distinct_values() -> None:
    values = {kind.value for kind in FailureKind}
    assert len(values) == 5
    assert values == {
        "rejected_input",
        "entitlement_refusal",
        "unmet_prerequisite",
        "expired_wait",
        "platform_fault",
    }


def test_payload_omits_absent_optional_fields() -> None:
    payload = platform_fault(message="upstream 502", **_COMMON).as_payload()
    assert "resource_id" not in payload
    assert set(payload) == {
        "message",
        "kind",
        "status",
        "code",
        "timeout_ms",
        "source",
        "request_id",
        "detail",
    }
