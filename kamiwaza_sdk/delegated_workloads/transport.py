"""Nonce-aware transport for exact delegated protocol requests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from kamiwaza_sdk.delegated_workloads.proof import (
    DelegatedCapability,
    DPoPNonce,
    DPoPProofKey,
    DPoPProofRequest,
    SensitiveValue,
    WorkloadAssertion,
    WorkloadProof,
    _secret_value,
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
    capability: DelegatedCapability | str | None = field(default=None, repr=False)
    content_type: str = "application/json"
    extra_headers: tuple[tuple[str, SensitiveValue | str], ...] = field(
        default=(), repr=False
    )
    retry_safety: ProtocolRetrySafety = ProtocolRetrySafety.NEVER

    def __post_init__(self) -> None:
        if not self.method:
            raise ValueError("delegated protocol request is incomplete")
        if not self.url:
            raise ValueError("delegated protocol request is incomplete")
        if isinstance(self.capability, str):
            object.__setattr__(
                self, "capability", DelegatedCapability(self.capability)
            )
        _validate_content_type(self.content_type)
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
        proof: WorkloadProof | None = None,
        proof_key: DPoPProofKey | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if proof is not None and proof_key is not None:
            raise ValueError("delegated transport received two proof lifecycles")
        self._session = session
        self._proof = proof or WorkloadProof.proof_only(proof_key)
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

    def prepare_headers(
        self,
        request: DelegatedProtocolRequest,
        *,
        nonce: DPoPNonce | str | None = None,
    ) -> dict[str, str]:
        """Create fresh trust headers for exact bytes sent by another transport.

        The caller must send the same method, URL, and body and must not log the
        returned credentials. This method performs no I/O and never retries the
        application request; a new protected-resource effect is required for a
        distinct provider attempt.
        """
        proof = self._proof.create(
            DPoPProofRequest(
                method=request.method,
                target_uri=request.url,
                access_token=request.capability,
                body_digest=body_digest(request.body),
                issued_at=self._clock(),
                nonce=nonce,
            )
        )
        return _request_headers(request, _secret_value(proof))

    def proof_public_jwk(self) -> dict[str, str]:
        """Expose only the public key used by this transport's DPoP proofs."""
        return self._proof.public_jwk()

    def proof_key_thumbprint(self) -> str:
        return self._proof.key_thumbprint()

    def workload_assertion(self) -> WorkloadAssertion:
        """Read fresh assertion material through the selected trusted adapter."""
        return self._proof.assertion()

    def select_attestation_profile(self, profile: str) -> None:
        """Select a current profile and rotate proof authority when it changes."""
        self._proof.select_profile(profile)

    def rotate_proof_key(self) -> None:
        self._proof.rotate_key()

    def close(self) -> None:
        self._proof.close()

    def _send(
        self, request: DelegatedProtocolRequest, *, nonce: DPoPNonce | str | None
    ) -> ResponsePort:
        headers = self.prepare_headers(request, nonce=nonce)
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
    headers = {
        name: _secret_value(value) for name, value in request.extra_headers
    }
    headers["Content-Type"] = request.content_type
    headers["DPoP"] = proof
    if request.capability is not None:
        headers["Authorization"] = f"DPoP {_secret_value(request.capability)}"
    return headers


def _validate_extra_headers(
    headers: tuple[tuple[str, SensitiveValue | str], ...],
) -> None:
    reserved = {"authorization", "content-type", "dpop"}
    names = [name.casefold() for name, _ in headers]
    if any(name in reserved for name in names):
        raise ValueError("delegated protocol header overrides a trust header")
    if len(names) != len(set(names)):
        raise ValueError("delegated protocol headers contain a duplicate")


def _validate_content_type(value: str) -> None:
    if not value:
        raise ValueError("delegated protocol content type is invalid")
    if "\r" in value:
        raise ValueError("delegated protocol content type is invalid")
    if "\n" in value:
        raise ValueError("delegated protocol content type is invalid")
