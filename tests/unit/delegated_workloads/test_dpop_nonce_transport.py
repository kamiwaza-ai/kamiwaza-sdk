"""SDK-owned DPoP nonce retry safety for delegated protocol requests."""

from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest

from kamiwaza_sdk.delegated_workloads import (
    DelegatedProtocolRequest,
    DelegatedWorkloadTransport,
    DPoPNonceRequired,
    ProtocolRetrySafety,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
NONCE = "core-issued-nonce-0123456789"
CORRELATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class StubResponse:
    def __init__(self, status_code: int, body: object, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers

    def json(self) -> object:
        return self._body


class StubSession:
    def __init__(self, responses: list[StubResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> StubResponse:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def _challenge(nonce: str = NONCE) -> StubResponse:
    return StubResponse(
        401,
        {
            "error": {
                "code": "dpop_nonce_required",
                "message": "a fresh DPoP nonce is required",
                "retry_classification": "nonce_required",
                "correlation_id": CORRELATION_ID,
            }
        },
        {"DPoP-Nonce": nonce},
    )


def _success() -> StubResponse:
    return StubResponse(200, {"status": "ok"}, {})


def _request(retry_safety: ProtocolRetrySafety) -> DelegatedProtocolRequest:
    return DelegatedProtocolRequest(
        method="POST",
        url="https://core.example.test/api/v1/delegated-workloads/run-claims",
        capability="header.payload.signature",
        body=b'{"run_id":"run-1"}',
        retry_safety=retry_safety,
    )


def _proof_claims(call: tuple[str, str, dict[str, object]]) -> dict[str, object]:
    headers = call[2]["headers"]
    assert isinstance(headers, dict)
    proof = headers["DPoP"]
    assert isinstance(proof, str)
    return jwt.decode(proof, options={"verify_signature": False})


def test_safe_protocol_request_retries_once_without_changing_body() -> None:
    session = StubSession([_challenge(), _success()])
    transport = DelegatedWorkloadTransport(session, clock=lambda: NOW)

    response = transport.send(_request(ProtocolRetrySafety.IDEMPOTENT_PROTOCOL))

    assert response.status_code == 200
    assert len(session.calls) == 2
    assert session.calls[0][2]["data"] == session.calls[1][2]["data"]
    first = _proof_claims(session.calls[0])
    second = _proof_claims(session.calls[1])
    assert "nonce" not in first
    assert second["nonce"] == NONCE
    assert first["jti"] != second["jti"]


def test_application_work_is_never_replayed_for_nonce_challenge() -> None:
    session = StubSession([_challenge()])
    transport = DelegatedWorkloadTransport(session, clock=lambda: NOW)

    with pytest.raises(DPoPNonceRequired):
        transport.send(_request(ProtocolRetrySafety.NEVER))

    assert len(session.calls) == 1


def test_repeated_nonce_challenge_stops_after_one_safe_retry() -> None:
    session = StubSession([_challenge(), _challenge("second-core-nonce-012345")])
    transport = DelegatedWorkloadTransport(session, clock=lambda: NOW)

    with pytest.raises(DPoPNonceRequired) as caught:
        transport.send(_request(ProtocolRetrySafety.IDEMPOTENT_PROTOCOL))

    assert caught.value.nonce == "second-core-nonce-012345"
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        ({"error": {"code": "attestation_rejected"}}, {"DPoP-Nonce": NONCE}),
        (
            {
                "error": {
                    "code": "dpop_nonce_required",
                    "retry_classification": "never",
                }
            },
            {"DPoP-Nonce": NONCE},
        ),
        (
            {
                "error": {
                    "code": "dpop_nonce_required",
                    "retry_classification": "nonce_required",
                }
            },
            {},
        ),
    ],
)
def test_only_complete_closed_nonce_challenge_is_retryable(
    body: object, headers: dict[str, str]
) -> None:
    response = StubResponse(401, body, headers)
    session = StubSession([response])
    transport = DelegatedWorkloadTransport(session, clock=lambda: NOW)

    assert transport.send(_request(ProtocolRetrySafety.IDEMPOTENT_PROTOCOL)) is response
    assert len(session.calls) == 1
