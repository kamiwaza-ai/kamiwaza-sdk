"""Nonce-aware transport for exact delegated protocol requests."""

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
from kamiwaza_sdk.delegated_workloads.errors import (
    DelegatedProtocolError,
    DPoPNonceRequired,
    delegated_error_from_response,
)


class ProtocolRetrySafety(str, Enum):
    """Whether the SDK may repeat a request after a nonce challenge."""

    NEVER = "never"
    IDEMPOTENT_PROTOCOL = "idempotent_protocol"


@dataclass(frozen=True, slots=True, kw_only=True)
class DelegatedProtocolRequest:
    """Exact bytes and authority for one SDK-owned protocol request."""

    method: str
    url: str
    body: bytes = field(repr=False)
    capability: str | None = field(default=None, repr=False)
    extra_headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    retry_safety: ProtocolRetrySafety = ProtocolRetrySafety.NEVER

    def __post_init__(self) -> None:
        if not self.method or not self.url:
            raise ValueError("delegated protocol request is incomplete")
        _validate_extra_headers(self.extra_headers)


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

    def send_json(self, request: DelegatedProtocolRequest) -> object:
        return checked_json_response(self.send(request))

    def proof_public_jwk(self) -> dict[str, str]:
        """Expose only the public key used by this transport's DPoP proofs."""
        return self._proof_key.public_jwk()

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
        headers = _request_headers(request, proof)
        return self._session.request(
            request.method, request.url, data=request.body, headers=headers
        )


def checked_json_response(response: ResponsePort) -> object:
    if response.status_code < 200 or response.status_code >= 300:
        raise delegated_error_from_response(response)
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise DelegatedProtocolError(response.status_code) from exc
    if not isinstance(payload, (Mapping, list)):
        raise DelegatedProtocolError(response.status_code)
    return payload


def _nonce_challenge(response: ResponsePort) -> DPoPNonceRequired | None:
    if response.status_code != 401:
        return None
    error = delegated_error_from_response(response)
    return error if isinstance(error, DPoPNonceRequired) else None


def _request_headers(request: DelegatedProtocolRequest, proof: str) -> dict[str, str]:
    headers = dict(request.extra_headers)
    headers["Content-Type"] = "application/json"
    headers["DPoP"] = proof
    if request.capability is not None:
        headers["Authorization"] = f"DPoP {request.capability}"
    return headers


def _validate_extra_headers(headers: tuple[tuple[str, str], ...]) -> None:
    reserved = {"authorization", "content-type", "dpop"}
    names = [name.casefold() for name, _ in headers]
    if any(name in reserved for name in names):
        raise ValueError("delegated protocol header overrides a trust header")
    if len(names) != len(set(names)):
        raise ValueError("delegated protocol headers contain a duplicate")
