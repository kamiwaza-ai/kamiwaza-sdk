"""Closed SDK domain and error mapping for delegated workload authority."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from kamiwaza_sdk.delegated_workloads import (
    DelegatedErrorCode,
    DelegatedProtocolError,
    EffectAuthorization,
    EffectDetail,
    RetryClassification,
    RunDetail,
    delegated_error_from_response,
)

from .protocol_test_support import (
    CLIENT_ID,
    CORRELATION_ID,
    EFFECT_ID,
    RESUME_REFERENCE,
    SUBJECT_ID,
    StubResponse,
    authorization_payload,
    effect_detail_payload,
    error_payload,
    run_detail_payload,
)

pytestmark = pytest.mark.contract


ERROR_RULES = (
    ("invalid_request", 422, "never"),
    ("readiness_unavailable", 503, "bounded_backoff"),
    ("incompatible_contract", 409, "never"),
    ("registration_rejected", 403, "never"),
    ("resource_registration_rejected", 409, "never"),
    ("attestation_rejected", 401, "after_reauthentication"),
    ("grant_inactive", 403, "never"),
    ("revision_mismatch", 403, "never"),
    ("current_authority_denied", 403, "never"),
    ("capability_expired", 401, "after_reauthentication"),
    ("proof_mismatch", 401, "never"),
    ("dpop_nonce_required", 401, "nonce_required"),
    ("replay_rejected", 409, "never"),
    ("claim_conflict", 409, "idempotent_read_only"),
    ("fenced_claim", 409, "never"),
    ("occurrence_digest_conflict", 409, "never"),
    ("effect_digest_conflict", 409, "never"),
    ("unknown_resource_contract", 422, "never"),
    ("protected_resource_rejected", 403, "never"),
    ("approval_required", 409, "idempotent_read_only"),
    ("credential_binding_unavailable", 503, "never"),
    ("provider_transient_failure", 503, "idempotent_read_only"),
    ("ambiguous_effect_outcome", 409, "never"),
)


def test_run_and_effect_reads_preserve_deadline_correlation_and_resume() -> None:
    run_payload = run_detail_payload()
    run_payload["future_safe_field"] = "preserved"

    run = RunDetail.model_validate(run_payload)
    effect = EffectDetail.model_validate(effect_detail_payload())

    assert run.authority_deadline.isoformat() == "2026-08-10T12:00:00+00:00"
    assert run.correlation_id == UUID(CORRELATION_ID)
    assert run.future_safe_field == "preserved"
    assert effect.resume_run_reference == RESUME_REFERENCE
    assert effect.consumed_at is None
    assert RESUME_REFERENCE not in repr(effect)


def test_canonical_requester_context_keeps_user_and_agent_distinct() -> None:
    result = EffectAuthorization.model_validate(authorization_payload())

    assert result.requester_context is not None
    assert result.requester_context.subject_id == UUID(SUBJECT_ID)
    assert result.requester_context.client_id == UUID(CLIENT_ID)
    assert result.requester_context.subject_id != result.requester_context.client_id
    assert result.requester_context.fencing_token == 3
    assert result.correlation_id == UUID(CORRELATION_ID)
    assert "one-use-consumption-token" not in repr(result)


def test_typed_deny_cannot_smuggle_context_or_consumption_authority() -> None:
    denied = authorization_payload()
    denied.update(
        {
            "decision": "deny",
            "reason_codes": ["current_authority_denied"],
            "requester_context": None,
            "consumption_token": None,
        }
    )
    assert EffectAuthorization.model_validate(denied).decision.value == "deny"

    denied["consumption_token"] = "smuggled-token"
    with pytest.raises(ValidationError):
        EffectAuthorization.model_validate(denied)


@pytest.mark.parametrize(
    "changes",
    [
        {"decision": "pending_approval"},
        {"reason_codes": ["allowed", "allowed"]},
    ],
)
def test_authorization_rejects_non_wire_decisions_and_duplicate_reasons(
    changes: dict[str, object],
) -> None:
    payload = authorization_payload()
    payload.update(changes)

    with pytest.raises(ValidationError):
        EffectAuthorization.model_validate(payload)


@pytest.mark.parametrize(("code", "status", "retry"), ERROR_RULES)
def test_every_wire_error_has_one_typed_http_retry_mapping(
    code: str,
    status: int,
    retry: str,
) -> None:
    response = _error_response(code, status, retry)

    error = delegated_error_from_response(response)

    assert error.code is DelegatedErrorCode(code)
    assert error.status_code == status
    assert error.retry_classification is RetryClassification(retry)
    assert error.correlation_id == UUID(CORRELATION_ID)


def test_each_closed_error_code_maps_to_a_distinct_exception_type() -> None:
    errors = [
        delegated_error_from_response(_error_response(code, status, retry))
        for code, status, retry in ERROR_RULES
    ]

    assert {error.code for error in errors} == set(DelegatedErrorCode)
    assert len({type(error) for error in errors}) == len(DelegatedErrorCode)


@pytest.mark.parametrize(
    "response",
    [
        StubResponse(409, error_payload("future_unknown_code", "never")),
        StubResponse(503, error_payload("effect_digest_conflict", "never")),
        StubResponse(
            409,
            error_payload("effect_digest_conflict", "bounded_backoff"),
        ),
        StubResponse(500, {"detail": "unsafe proxy failure"}),
    ],
)
def test_unknown_or_mismatched_error_contract_fails_closed(
    response: StubResponse,
) -> None:
    error = delegated_error_from_response(response)

    assert isinstance(error, DelegatedProtocolError)
    assert error.retry_classification is RetryClassification.NEVER
    assert "unsafe proxy failure" not in str(error)


def test_effect_identifiers_remain_exact() -> None:
    effect = EffectDetail.model_validate(effect_detail_payload())
    assert effect.effect_id == UUID(EFFECT_ID)


def _error_response(code: str, status: int, retry: str) -> StubResponse:
    headers = (
        {"DPoP-Nonce": "core-issued-nonce-0123456789"}
        if code == "dpop_nonce_required"
        else {}
    )
    return StubResponse(status, error_payload(code, retry), headers)
