"""Nonce-aware transport for idempotent delegated protocol requests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from kamiwaza_sdk.delegated_workloads.dpop import (
    DPoPProofKey,
    DPoPProofRequest,
    body_digest,
)
from kamiwaza_sdk.delegated_workloads.errors import DPoPNonceRequired


class ProtocolRetrySafety(str, Enum):
    """Whether the SDK may repeat the protocol request after a nonce challenge."""

    NEVER = "never"
    IDEMPOTENT_PROTOCOL = "idempotent_protocol"


@dataclass(frozen=True, slots=True, kw_only=True)
class DelegatedProtocolRequest:
    """Exact bytes and authority for one SDK-owned protocol request."""

    method: str
    url: str
    capability: str = field(repr=False)
    body: bytes = field(repr=False)
    retry_safety: ProtocolRetrySafety = ProtocolRetrySafety.NEVER

    def __post_init__(self) -> None:
        if not all((self.method, self.url, self.capability)):
            raise ValueError("delegated protocol request is incomplete")


class ResponsePort(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> object: ...


class SessionPort(Protocol):
    def request(self, method: str, url: str, **kwargs: object) -> ResponsePort: ...


class DelegatedWorkloadTransport:
    """Create fresh proofs and perform at most one explicitly safe nonce retry."""

    def __init__(
        self,
        session: SessionPort,
        *,
        proof_key: DPoPProofKey | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._session = session
        self._proof_key = proof_key or DPoPProofKey.generate()
        self._clock = clock

    def send(self, request: DelegatedProtocolRequest) -> ResponsePort:
        response = self._send(request, nonce=None)
        challenge = _nonce_challenge(response)
        if challenge is None:
            return response
        if request.retry_safety is ProtocolRetrySafety.NEVER:
            raise challenge
        response = self._send(request, nonce=challenge.nonce)
        repeated = _nonce_challenge(response)
        if repeated is not None:
            raise repeated
        return response

    def _send(
        self, request: DelegatedProtocolRequest, *, nonce: str | None
    ) -> ResponsePort:
        proof = self._proof_key.create(
            DPoPProofRequest(
                method=request.method,
                target_uri=request.url,
                access_token=request.capability,
                body_digest=body_digest(request.body),
                issued_at=self._clock(),
                nonce=nonce,
            )
        )
        headers = {
            "Authorization": f"DPoP {request.capability}",
            "Content-Type": "application/json",
            "DPoP": proof,
        }
        return self._session.request(
            request.method, request.url, data=request.body, headers=headers
        )


def _nonce_challenge(response: ResponsePort) -> DPoPNonceRequired | None:
    if response.status_code != 401:
        return None
    error = _error_body(response)
    if error is None:
        return None
    if error.get("code") != "dpop_nonce_required":
        return None
    if error.get("retry_classification") != "nonce_required":
        return None
    nonce = _header(response.headers, "DPoP-Nonce")
    if not _valid_nonce_header(nonce):
        return None
    assert nonce is not None
    return DPoPNonceRequired(nonce)


def _error_body(response: ResponsePort) -> Mapping[str, object] | None:
    try:
        body = response.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(body, Mapping):
        return None
    error = body.get("error")
    return error if isinstance(error, Mapping) else None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    for key, value in headers.items():
        if key.casefold() == expected:
            return value
    return None


def _valid_nonce_header(nonce: str | None) -> bool:
    if nonce is None:
        return False
    return 16 <= len(nonce) <= 1024
