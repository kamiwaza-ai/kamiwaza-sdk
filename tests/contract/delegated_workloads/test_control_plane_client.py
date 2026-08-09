"""Recorded contract for control-plane run reservation and queue handoff."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import cast
from uuid import UUID

import jwt
import pytest

from kamiwaza_sdk.delegated_workloads import (
    DelegatedControlPlaneClient,
    DelegatedProtocolError,
    DelegatedWorkloadTransport,
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
