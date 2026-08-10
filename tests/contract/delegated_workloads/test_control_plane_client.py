"""Recorded contract for control-plane run reservation and queue handoff."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import cast
from uuid import UUID

import jwt
import pytest

from kamiwaza_sdk.delegated_workloads import (
    AutomationApprovalPolicy,
    AutomationDescriptor,
    AutomationLimits,
    AutomationRevision,
    ConsentDecision,
    DelegatedControlPlaneClient,
    DelegatedProtocolError,
    DelegatedWorkloadTransport,
    DestinationRef,
    EffectResourceRef,
    IntentLifecycleStatus,
    OccurrenceDigestConflict,
    RunReservationRequest,
    RunTrigger,
    WorkloadReadAuthority,
)
from kamiwaza_sdk.delegated_workloads.transport import SessionPort

from .protocol_test_support import (
    CORRELATION_ID,
    DIGEST,
    GRANT_ID,
    RUN_ID,
    StubResponse,
    StubSession,
    error_payload,
)

pytestmark = pytest.mark.contract
NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
BASE_URL = "https://core.example.test/api/v1/delegated-workloads"
ASSERTION = "projected-control-plane-assertion"
RUN_REFERENCE = "opaque-run-reference-0123456789abcdef"
INTENT_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
CONSENT_URL = (
    "https://core.example.test/delegated-workloads/consent/"
    f"{INTENT_ID}?consent_nonce=one-use-consent-nonce-0123456789"
)


def test_create_intent_sends_closed_revision_and_redacts_consent_url() -> None:
    session = StubSession([StubResponse(201, _consent_request_payload())])

    consent = _client(session).create_intent(
        _automation_revision(),
        WorkloadReadAuthority(workload_assertion=ASSERTION),
    )

    assert consent.intent_id == UUID(INTENT_ID)
    assert str(consent.consent_url) == CONSENT_URL
    assert CONSENT_URL not in repr(consent)
    assert session.calls[0][:2] == (
        "POST",
        BASE_URL + "/intents",
    )
    assert _json_body(session.calls[0]) == {
        "automation_external_id": "tomo-task-7",
        "descriptor": {
            "actions": ["document.read", "document.update"],
            "approval_policy": {"mutations_require_it_approval": True},
            "credential_binding_ids": [
                "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
            ],
            "destinations": [
                {
                    "host": "documents.example.test",
                    "port": 443,
                    "route_template": "/v1/documents/{document_id}",
                    "scheme": "https",
                }
            ],
            "limits": {
                "max_concurrency": 1,
                "max_cost_microunits": 1000,
                "max_runs": 5,
                "max_runtime_seconds": 900,
            },
            "resources": [
                {
                    "descriptor_version": "v1",
                    "id": "document-7",
                    "type": "example.document",
                }
            ],
        },
        "return_uri": "https://tomo.example.test/tasks/tomo-task-7",
        "revision_digest": DIGEST,
        "revision_id": "revision-3",
    }
    _assert_workload_proof(session.calls[0])


def test_get_intent_returns_safe_consumed_status() -> None:
    session = StubSession(
        [
            StubResponse(
                200,
                {
                    "intent_id": INTENT_ID,
                    "status": "consumed",
                    "decision": "approve",
                    "grant_id": GRANT_ID,
                    "correlation_id": CORRELATION_ID,
                },
            )
        ]
    )

    status = _client(session).get_intent(
        UUID(INTENT_ID),
        WorkloadReadAuthority(workload_assertion=ASSERTION),
    )

    assert status.status is IntentLifecycleStatus.CONSUMED
    assert status.decision is ConsentDecision.APPROVE
    assert status.grant_id == UUID(GRANT_ID)
    assert session.calls[0][:2] == (
        "GET",
        BASE_URL + f"/intents/{INTENT_ID}",
    )
    assert session.calls[0][2]["data"] == b""
    _assert_workload_proof(session.calls[0])


@pytest.mark.parametrize(
    "payload",
    [
        {
            "intent_id": INTENT_ID,
            "status": "approved",
            "decision": "approve",
            "grant_id": GRANT_ID,
            "correlation_id": CORRELATION_ID,
        },
        {
            "intent_id": INTENT_ID,
            "status": "pending",
            "decision": "approve",
            "grant_id": GRANT_ID,
            "correlation_id": CORRELATION_ID,
        },
        {
            "intent_id": INTENT_ID,
            "status": "consumed",
            "decision": "deny",
            "grant_id": GRANT_ID,
            "correlation_id": CORRELATION_ID,
        },
    ],
)
def test_get_intent_rejects_unknown_or_inconsistent_state(
    payload: dict[str, object],
) -> None:
    session = StubSession([StubResponse(200, payload)])

    with pytest.raises(DelegatedProtocolError):
        _client(session).get_intent(
            UUID(INTENT_ID),
            WorkloadReadAuthority(workload_assertion=ASSERTION),
        )


def test_reserve_run_returns_redacted_opaque_queue_handoff() -> None:
    session = StubSession([StubResponse(201, _reservation_payload())])
    client = _client(session)

    reservation = client.reserve_run(
        RunReservationRequest(
            grant_id=UUID(GRANT_ID),
            revision_digest=DIGEST,
            occurrence_key="scheduled:2026-08-09T12:00:00Z",
            trigger=RunTrigger.SCHEDULED,
        ),
        WorkloadReadAuthority(workload_assertion=ASSERTION),
    )

    assert reservation.run_id == UUID(RUN_ID)
    assert reservation.queue_payload().model_dump() == {
        "run_reference": RUN_REFERENCE
    }
    assert RUN_REFERENCE not in repr(reservation)
    assert RUN_REFERENCE not in repr(reservation.queue_payload())
    assert _json_body(session.calls[0]) == {
        "grant_id": GRANT_ID,
        "occurrence_key": "scheduled:2026-08-09T12:00:00Z",
        "revision_digest": DIGEST,
        "trigger": "scheduled",
    }
    _assert_workload_proof(session.calls[0])


def test_reserve_run_uses_closed_error_mapping() -> None:
    session = StubSession(
        [
            StubResponse(
                409,
                error_payload("occurrence_digest_conflict", "never"),
            )
        ]
    )

    with pytest.raises(OccurrenceDigestConflict):
        _client(session).reserve_run(
            RunReservationRequest(
                grant_id=UUID(GRANT_ID),
                revision_digest=DIGEST,
                occurrence_key="conflicting-occurrence",
                trigger=RunTrigger.RETRY,
            ),
            WorkloadReadAuthority(workload_assertion=ASSERTION),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "running"),
        ("run_reference", "short"),
    ],
)
def test_reserve_run_rejects_non_opaque_or_non_queued_responses(
    field: str,
    value: str,
) -> None:
    payload = {**_reservation_payload(), field: value}
    session = StubSession([StubResponse(200, payload)])

    with pytest.raises(DelegatedProtocolError):
        _client(session).reserve_run(
            RunReservationRequest(
                grant_id=UUID(GRANT_ID),
                revision_digest=DIGEST,
                occurrence_key="scheduled:2026-08-09T12:00:00Z",
                trigger=RunTrigger.SCHEDULED,
            ),
            WorkloadReadAuthority(workload_assertion=ASSERTION),
        )


def _reservation_payload() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "status": "queued",
        "run_reference": RUN_REFERENCE,
        "correlation_id": CORRELATION_ID,
        "authority_deadline": "2026-08-10T12:00:00Z",
    }


def _consent_request_payload() -> dict[str, object]:
    return {
        "intent_id": INTENT_ID,
        "consent_url": CONSENT_URL,
        "expires_at": "2026-08-09T12:10:00Z",
        "status": "pending",
        "correlation_id": CORRELATION_ID,
    }


def _automation_revision() -> AutomationRevision:
    return AutomationRevision(
        automation_external_id="tomo-task-7",
        revision_id="revision-3",
        revision_digest=DIGEST,
        descriptor=AutomationDescriptor(
            actions=("document.read", "document.update"),
            resources=(
                EffectResourceRef(
                    type="example.document",
                    descriptor_version="v1",
                    id="document-7",
                ),
            ),
            destinations=(
                DestinationRef(
                    host="documents.example.test",
                    port=443,
                    route_template="/v1/documents/{document_id}",
                ),
            ),
            credential_binding_ids=(
                UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
            ),
            limits=AutomationLimits(
                max_runs=5,
                max_concurrency=1,
                max_runtime_seconds=900,
                max_cost_microunits=1000,
            ),
            approval_policy=AutomationApprovalPolicy(),
        ),
        return_uri="https://tomo.example.test/tasks/tomo-task-7",
    )


def _client(session: StubSession) -> DelegatedControlPlaneClient:
    transport = DelegatedWorkloadTransport(
        cast(SessionPort, session), clock=lambda: NOW
    )
    return DelegatedControlPlaneClient(BASE_URL, transport)


def _assert_workload_proof(call: tuple[str, str, dict[str, object]]) -> None:
    headers = call[2]["headers"]
    assert isinstance(headers, dict)
    assert headers["X-Kamiwaza-Workload-Assertion"] == ASSERTION
    assert "Authorization" not in headers
    proof = jwt.decode(headers["DPoP"], options={"verify_signature": False})
    assert "ath" not in proof


def _json_body(call: tuple[str, str, dict[str, object]]) -> dict[str, object]:
    body = call[2]["data"]
    assert isinstance(body, bytes)
    decoded = json.loads(body)
    assert isinstance(decoded, dict)
    return decoded
