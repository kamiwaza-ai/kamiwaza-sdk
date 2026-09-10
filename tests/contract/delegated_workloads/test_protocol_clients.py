"""Recorded HTTP contracts for SDK effect reads, guards, and approvals."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import jwt
import pytest

from kamiwaza_sdk.delegated_workloads import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    DelegatedApprovalAPI,
    DelegatedGuardAuthority,
    DelegatedWorkloadAPI,
    DelegatedWorkloadTransport,
    EffectAuthorizationRequest,
    EffectConsumptionRequest,
    EffectDigestConflict,
    WorkloadReadAuthority,
)

from .protocol_test_support import (
    CORRELATION_ID,
    DIGEST,
    EFFECT_ID,
    RESUME_REFERENCE,
    RUN_ID,
    TENANT_ID,
    StubResponse,
    StubSession,
    authorization_payload,
    consumption_payload,
    effect_detail_payload,
    error_payload,
    run_detail_payload,
)

pytestmark = pytest.mark.contract
NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
BASE_URL = "https://core.example.test/api/v1/delegated-workloads"
ASSERTION = "projected-workload-assertion"
CAPABILITY = "header.effect-capability.signature"


def test_workload_reads_return_typed_run_and_parked_effect_state() -> None:
    session = StubSession(
        [
            StubResponse(200, run_detail_payload()),
            StubResponse(200, effect_detail_payload()),
        ]
    )
    api = _workload_api(session)
    authority = WorkloadReadAuthority(workload_assertion=ASSERTION)

    run = api.get_run(UUID(RUN_ID), authority)
    effect = api.get_effect(UUID(EFFECT_ID), authority)

    assert run.correlation_id == UUID(CORRELATION_ID)
    assert effect.resume_run_reference == RESUME_REFERENCE
    assert [call[0] for call in session.calls] == ["GET", "GET"]
    assert session.calls[0][1] == f"{BASE_URL}/runs/{RUN_ID}"
    assert session.calls[1][1] == f"{BASE_URL}/effects/{EFFECT_ID}"
    _assert_workload_proof(session.calls[0])
    assert "Authorization" not in _headers(session.calls[0])


def test_guard_authorizes_then_consumes_before_application_code() -> None:
    session = StubSession(
        [
            StubResponse(200, authorization_payload()),
            StubResponse(200, consumption_payload()),
        ]
    )
    api = _workload_api(session)
    authority = DelegatedGuardAuthority(
        capability=CAPABILITY,
        workload_assertion=ASSERTION,
    )
    request = EffectAuthorizationRequest(
        effect_id=UUID(EFFECT_ID),
        request_digest=DIGEST,
        method="POST",
        target_uri="https://resource.example.test/documents/doc-7",
    )

    decision = api.authorize_effect(request, authority)
    assert decision.consumption_token is not None
    consumed = api.consume_effect(
        EffectConsumptionRequest(
            effect_id=UUID(EFFECT_ID),
            request_digest=DIGEST,
            fencing_token=3,
        ),
        authority.with_consumption(decision.consumption_token),
    )

    assert consumed.status.value == "executing"
    assert _headers(session.calls[0])["Authorization"] == f"DPoP {CAPABILITY}"
    assert "X-Kamiwaza-Effect-Consumption" not in _headers(session.calls[0])
    assert _headers(session.calls[1])["X-Kamiwaza-Effect-Consumption"] == (
        "one-use-consumption-token"
    )
    assert _json_body(session.calls[1]) == {
        "fencing_token": 3,
        "request_digest": DIGEST,
    }


def test_member_approval_poll_and_decision_return_resume_reference() -> None:
    session = StubSession(
        [
            StubResponse(200, [effect_detail_payload()]),
            StubResponse(200, effect_detail_payload()),
        ]
    )
    api = DelegatedApprovalAPI(BASE_URL, session)

    pending = api.list_pending(UUID(TENANT_ID))
    approved = api.decide(
        ApprovalDecisionRequest(
            effect_id=UUID(EFFECT_ID),
            effect_digest=DIGEST,
            policy_version="policy-v1",
            decision=ApprovalDecision.APPROVE,
            csrf_token="session-bound-csrf",
        )
    )

    assert [effect.effect_id for effect in pending] == [UUID(EFFECT_ID)]
    assert approved.resume_run_reference == RESUME_REFERENCE
    assert session.calls[0][2]["params"] == {"tenant_id": TENANT_ID}
    assert _headers(session.calls[1])["X-CSRF-Token"] == "session-bound-csrf"
    assert _json_body(session.calls[1])["decision"] == "approve"


def test_approval_watch_has_no_sdk_owned_lifetime_limit() -> None:
    session = StubSession(
        [
            StubResponse(200, []),
            StubResponse(200, [effect_detail_payload()]),
        ]
    )
    waits: list[float] = []
    api = DelegatedApprovalAPI(BASE_URL, session)
    updates = api.watch_pending(
        UUID(TENANT_ID),
        poll_interval_seconds=2.5,
        wait=waits.append,
    )

    assert next(updates) == ()
    assert [effect.effect_id for effect in next(updates)] == [UUID(EFFECT_ID)]
    assert waits == [2.5]


def test_protocol_error_mapping_is_used_by_typed_clients() -> None:
    session = StubSession(
        [
            StubResponse(
                409,
                error_payload("effect_digest_conflict", "never"),
            )
        ]
    )
    api = _workload_api(session)

    with pytest.raises(EffectDigestConflict) as caught:
        api.get_effect(
            UUID(EFFECT_ID),
            WorkloadReadAuthority(workload_assertion=ASSERTION),
        )

    assert caught.value.correlation_id == UUID(CORRELATION_ID)


@pytest.mark.parametrize(
    "authority",
    [
        lambda: WorkloadReadAuthority(workload_assertion=""),
        lambda: DelegatedGuardAuthority(
            capability="",
            workload_assertion=ASSERTION,
        ),
        lambda: DelegatedGuardAuthority(
            capability=CAPABILITY,
            workload_assertion="",
        ),
    ],
)
def test_empty_workload_or_capability_authority_is_rejected(authority) -> None:
    with pytest.raises(ValueError):
        authority()


def _workload_api(session: StubSession) -> DelegatedWorkloadAPI:
    transport = DelegatedWorkloadTransport(session, clock=lambda: NOW)
    return DelegatedWorkloadAPI(BASE_URL, transport)


def _headers(call: tuple[str, str, dict[str, object]]) -> dict[str, str]:
    headers = call[2]["headers"]
    assert isinstance(headers, dict)
    return headers


def _assert_workload_proof(call: tuple[str, str, dict[str, object]]) -> None:
    headers = _headers(call)
    assert headers["X-Kamiwaza-Workload-Assertion"] == ASSERTION
    proof = jwt.decode(headers["DPoP"], options={"verify_signature": False})
    assert "ath" not in proof


def _json_body(call: tuple[str, str, dict[str, object]]) -> dict[str, object]:
    body = call[2]["data"]
    assert isinstance(body, bytes)
    decoded = json.loads(body)
    assert isinstance(decoded, dict)
    return decoded
