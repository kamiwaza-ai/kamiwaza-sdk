"""Recorded contract for executor claim, lifecycle, and effect reservation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import cast
from uuid import UUID

import jwt
import pytest

from kamiwaza_sdk.delegated_workloads import (
    DelegatedExecutorClient,
    DelegatedWorkloadTransport,
    EffectReservationRequest,
    EffectResourceRef,
    FencedClaim,
    OpaqueRunQueuePayload,
    RunTransition,
    RunTransitionRequest,
    WorkloadReadAuthority,
)
from kamiwaza_sdk.delegated_workloads.transport import SessionPort

from .protocol_test_support import (
    CLAIM_ID,
    CORRELATION_ID,
    DIGEST,
    EFFECT_ID,
    RUN_ID,
    StubResponse,
    StubSession,
    error_payload,
)

pytestmark = pytest.mark.contract
NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
BASE_URL = "https://core.example.test/api/v1/delegated-workloads"
ASSERTION = "projected-executor-assertion"
RUN_REFERENCE = "opaque-run-reference-0123456789abcdef"
RUN_CAPABILITY = "header.run-capability.signature"


def test_executor_claim_binds_the_transport_proof_key() -> None:
    session = StubSession([StubResponse(200, _claim_payload())])
    client = _client(session)

    claim = client.claim_run(
        OpaqueRunQueuePayload(run_reference=RUN_REFERENCE),
        WorkloadReadAuthority(workload_assertion=ASSERTION),
    )

    body = _json_body(session.calls[0])
    proof_header = jwt.get_unverified_header(_headers(session.calls[0])["DPoP"])
    assert body == {
        "executor_proof_jwk": proof_header["jwk"],
        "run_reference": RUN_REFERENCE,
    }
    assert _headers(session.calls[0])["X-Kamiwaza-Workload-Assertion"] == ASSERTION
    assert "Authorization" not in _headers(session.calls[0])
    assert RUN_REFERENCE not in repr(claim)
    assert RUN_CAPABILITY not in repr(claim)


def test_executor_transitions_are_capability_bound_and_fenced() -> None:
    session = StubSession(
        [
            StubResponse(200, _claim_payload()),
            StubResponse(200, _transition_payload()),
            StubResponse(200, _transition_payload(run_status="succeeded")),
        ]
    )
    client = _client(session)
    claim = client.claim_run(
        OpaqueRunQueuePayload(run_reference=RUN_REFERENCE),
        WorkloadReadAuthority(workload_assertion=ASSERTION),
    )
    authority = claim.authority(ASSERTION)

    heartbeat = client.transition(
        RunTransitionRequest(transition=RunTransition.HEARTBEAT), authority
    )
    finished = client.transition(
        RunTransitionRequest(
            transition=RunTransition.SUCCEED,
            outcome_category="completed",
        ),
        authority,
    )

    assert heartbeat.run_status.value == "running"
    assert finished.run_status.value == "succeeded"
    assert _json_body(session.calls[1]) == {
        "fencing_token": 3,
        "transition": "heartbeat",
    }
    assert _json_body(session.calls[2]) == {
        "fencing_token": 3,
        "outcome_category": "completed",
        "transition": "succeed",
    }
    _assert_run_authority(session.calls[1])


def test_executor_reserves_an_exact_effect_without_assertion_forwarding() -> None:
    session = StubSession(
        [
            StubResponse(200, _claim_payload()),
            StubResponse(200, _effect_payload()),
        ]
    )
    client = _client(session)
    claim = client.claim_run(
        OpaqueRunQueuePayload(run_reference=RUN_REFERENCE),
        WorkloadReadAuthority(workload_assertion=ASSERTION),
    )

    effect = client.reserve_effect(
        EffectReservationRequest(
            effect_key="document:read",
            effect_digest=DIGEST,
            action="read",
            resource=EffectResourceRef(
                type="example.document",
                descriptor_version="v1",
                id="doc-7",
            ),
            audience="https://resource.example.test",
        ),
        claim.authority(ASSERTION),
    )

    assert effect.effect_id == UUID(EFFECT_ID)
    headers = _headers(session.calls[1])
    assert headers["Authorization"] == f"DPoP {RUN_CAPABILITY}"
    assert "X-Kamiwaza-Workload-Assertion" not in headers
    assert _json_body(session.calls[1])["effect_digest"] == DIGEST


def test_executor_uses_closed_fence_error() -> None:
    session = StubSession(
        [
            StubResponse(200, _claim_payload()),
            StubResponse(409, error_payload("fenced_claim", "never")),
        ]
    )
    client = _client(session)
    claim = client.claim_run(
        OpaqueRunQueuePayload(run_reference=RUN_REFERENCE),
        WorkloadReadAuthority(workload_assertion=ASSERTION),
    )

    with pytest.raises(FencedClaim):
        client.transition(
            RunTransitionRequest(transition=RunTransition.ACKNOWLEDGE_CANCEL),
            claim.authority(ASSERTION),
        )


def _claim_payload() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "claim_id": CLAIM_ID,
        "status": "claimed",
        "fencing_token": 3,
        "lease_expires_at": "2026-08-09T12:05:00Z",
        "run_capability": RUN_CAPABILITY,
        "expires_at": "2026-08-09T12:05:00Z",
        "authority_deadline": "2026-08-10T12:00:00Z",
        "correlation_id": CORRELATION_ID,
    }


def _transition_payload(*, run_status: str = "running") -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "claim_id": CLAIM_ID,
        "run_status": run_status,
        "claim_status": "active" if run_status == "running" else "terminal",
        "lease_expires_at": "2026-08-09T12:05:00Z",
        "authority_deadline": "2026-08-10T12:00:00Z",
        "correlation_id": CORRELATION_ID,
    }


def _effect_payload() -> dict[str, object]:
    return {
        "effect_id": EFFECT_ID,
        "decision": "allow",
        "status": "reserved",
        "policy_version": "policy-v1",
        "reason_codes": ["allowed"],
        "effect_capability": "header.effect-capability.signature",
        "broker_handle": None,
        "valid_until": "2026-08-09T12:01:00Z",
        "correlation_id": CORRELATION_ID,
    }


def _client(session: StubSession) -> DelegatedExecutorClient:
    transport = DelegatedWorkloadTransport(
        cast(SessionPort, session), clock=lambda: NOW
    )
    return DelegatedExecutorClient(BASE_URL, transport)


def _assert_run_authority(call: tuple[str, str, dict[str, object]]) -> None:
    headers = _headers(call)
    assert headers["Authorization"] == f"DPoP {RUN_CAPABILITY}"
    assert headers["X-Kamiwaza-Workload-Assertion"] == ASSERTION
    proof = jwt.decode(headers["DPoP"], options={"verify_signature": False})
    assert "ath" in proof


def _headers(call: tuple[str, str, dict[str, object]]) -> dict[str, str]:
    headers = call[2]["headers"]
    assert isinstance(headers, dict)
    return headers


def _json_body(call: tuple[str, str, dict[str, object]]) -> dict[str, object]:
    body = call[2]["data"]
    assert isinstance(body, bytes)
    decoded = json.loads(body)
    assert isinstance(decoded, dict)
    return decoded
