"""Closed fake OAuth provider with digest-only observations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, NoReturn

from kamiwaza_sdk.delegated_workloads.proof import SensitiveValue

_SERIALIZATION_ERROR = "fake provider credential leases cannot be serialized"
_RESOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_MAX_TTL = timedelta(minutes=15)


class ClosedOperationRejected(RuntimeError):
    """A call outside the fake provider's immutable registry was rejected."""

    def __init__(self) -> None:
        super().__init__("broker operation is unavailable")


class ProviderCredentialRejected(RuntimeError):
    """The internal fake provider credential is no longer usable."""

    def __init__(self) -> None:
        super().__init__("provider credential is unavailable")


class ProviderResponseLost(RuntimeError):
    """The fake provider committed work but its response was lost."""

    def __init__(self) -> None:
        super().__init__("provider response unavailable")


@dataclass(frozen=True, slots=True)
class BrokerResourceCall:
    operation_id: str
    resource_id: str
    params: Mapping[str, object] | None = None
    body: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class SafeProviderRecord:
    operation_id: str
    method: str
    request_digest: str
    resource_id: str
    outcome: Literal["succeeded", "response_lost"]


@dataclass(frozen=True, slots=True)
class _OperationDefinition:
    method: str
    route: str
    mutation: bool


@dataclass(frozen=True, slots=True)
class _ProviderLease:
    access_credential: SensitiveValue = field(repr=False)
    revocation_handle: SensitiveValue = field(repr=False)
    issued_at: datetime
    expires_at: datetime

    def __reduce__(self) -> NoReturn:
        raise TypeError(_SERIALIZATION_ERROR)

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        raise TypeError(_SERIALIZATION_ERROR)


class FakeOAuthProvider:
    """Provider simulator that never exposes its seeded credential values."""

    def __init__(
        self,
        access_credential: SensitiveValue,
        revocation_handle: SensitiveValue,
        clock: Callable[[], datetime],
    ) -> None:
        self._access_credential = access_credential
        self._revocation_handle = revocation_handle
        self._clock = clock
        self._records: list[SafeProviderRecord] = []
        self._documents = {"doc-7": "original"}
        self._revoked = False
        self._revocation_acknowledged = False
        self._lose_response = False

    @classmethod
    def seeded(
        cls,
        access_credential: str,
        revocation_handle: str,
        *,
        clock: Callable[[], datetime],
    ) -> FakeOAuthProvider:
        return cls(
            SensitiveValue(access_credential),
            SensitiveValue(revocation_handle),
            clock,
        )

    @property
    def records(self) -> tuple[SafeProviderRecord, ...]:
        return tuple(self._records)

    @property
    def revocation_acknowledged(self) -> bool:
        return self._revocation_acknowledged

    def lose_next_response(self) -> None:
        self._lose_response = True

    def document(self, resource_id: str) -> dict[str, str]:
        content = self._documents.get(resource_id)
        if content is None:
            raise ClosedOperationRejected
        return {"content": content, "id": resource_id}

    def _issue(self, issued_at: datetime, expires_at: datetime) -> _ProviderLease:
        _validate_lease_window(issued_at, expires_at)
        return _ProviderLease(
            self._access_credential,
            self._revocation_handle,
            issued_at,
            expires_at,
        )

    def _execute(
        self,
        lease: _ProviderLease,
        call: BrokerResourceCall,
        operation: _OperationDefinition,
    ) -> dict[str, object]:
        self._require_credential(lease)
        result = self._apply(call, operation)
        outcome: Literal["succeeded", "response_lost"] = (
            "response_lost" if self._lose_response else "succeeded"
        )
        self._records.append(_provider_record(call, operation, outcome))
        if self._lose_response:
            self._lose_response = False
            raise ProviderResponseLost
        return result

    def _apply(
        self,
        call: BrokerResourceCall,
        operation: _OperationDefinition,
    ) -> dict[str, object]:
        if operation.mutation:
            body = call.body or {}
            self._documents[call.resource_id] = str(body["content"])
        return {"document": self.document(call.resource_id)}

    def _require_credential(self, lease: _ProviderLease) -> None:
        expected = (self._access_credential, self._revocation_handle)
        actual = (lease.access_credential, lease.revocation_handle)
        if actual != expected:
            raise ProviderCredentialRejected
        if self._revoked or self._clock() >= lease.expires_at:
            raise ProviderCredentialRejected

    def _revoke(self, lease: _ProviderLease) -> None:
        if lease.revocation_handle is not self._revocation_handle:
            raise ProviderCredentialRejected
        self._revoked = True
        self._revocation_acknowledged = True

    def __repr__(self) -> str:
        return (
            "FakeOAuthProvider("
            f"records={len(self._records)}, revoked={self._revoked}, "
            "credentials=<redacted>)"
        )


class ClosedBrokerResource:
    """Trusted fixture adapter for the fake provider's closed operations."""

    def __init__(self, provider: FakeOAuthProvider, lease: _ProviderLease) -> None:
        self._provider = provider
        self._lease = lease

    @classmethod
    def connect(
        cls,
        provider: FakeOAuthProvider,
        *,
        issued_at: datetime,
        expires_at: datetime,
    ) -> ClosedBrokerResource:
        return cls(provider, provider._issue(issued_at, expires_at))

    def execute(self, call: BrokerResourceCall) -> dict[str, object]:
        operation = _operation_for(call)
        return self._provider._execute(self._lease, call, operation)

    def request_digest(self, call: BrokerResourceCall) -> str:
        """Return the safe canonical digest used by the fake provider record."""
        return _request_digest(call, _operation_for(call))

    def document(self, resource_id: str) -> dict[str, str]:
        return self._provider.document(resource_id)

    def revoke(self) -> None:
        self._provider._revoke(self._lease)

    def __repr__(self) -> str:
        return "ClosedBrokerResource(provider_lease=<redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError(_SERIALIZATION_ERROR)

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        raise TypeError(_SERIALIZATION_ERROR)


def _validate_lease_window(issued_at: datetime, expires_at: datetime) -> None:
    if issued_at.tzinfo is None or expires_at.tzinfo is None:
        raise ValueError("fake provider lease window is invalid")
    ttl = expires_at - issued_at
    if ttl <= timedelta(0) or ttl > _MAX_TTL:
        raise ValueError("fake provider lease window is invalid")


def _validate_call(
    call: BrokerResourceCall,
    operation: _OperationDefinition,
) -> None:
    if _RESOURCE_ID.fullmatch(call.resource_id) is None:
        raise ClosedOperationRejected
    validator = _CALL_VALIDATORS[operation.mutation]
    validator(call)


def _operation_for(call: BrokerResourceCall) -> _OperationDefinition:
    operation = _OPERATIONS.get(call.operation_id)
    if operation is None:
        raise ClosedOperationRejected
    _validate_call(call, operation)
    return operation


def _validate_read(call: BrokerResourceCall) -> None:
    if call.body is not None:
        raise ClosedOperationRejected
    if call.params not in (None, {"projection": "summary"}):
        raise ClosedOperationRejected


def _validate_update(call: BrokerResourceCall) -> None:
    if call.params is not None:
        raise ClosedOperationRejected
    body = call.body
    if body is None:
        raise ClosedOperationRejected
    if set(body) != {"content"}:
        raise ClosedOperationRejected
    _validate_content(body["content"])


def _validate_content(content: object) -> None:
    if not isinstance(content, str):
        raise ClosedOperationRejected
    if not content:
        raise ClosedOperationRejected
    if len(content) > 4096:
        raise ClosedOperationRejected


def _provider_record(
    call: BrokerResourceCall,
    operation: _OperationDefinition,
    outcome: Literal["succeeded", "response_lost"],
) -> SafeProviderRecord:
    return SafeProviderRecord(
        operation_id=call.operation_id,
        method=operation.method,
        request_digest=_request_digest(call, operation),
        resource_id=call.resource_id,
        outcome=outcome,
    )


def _request_digest(
    call: BrokerResourceCall,
    operation: _OperationDefinition,
) -> str:
    payload = {
        "body": dict(call.body or {}),
        "method": operation.method,
        "params": dict(call.params or {}),
        "target": operation.route.format(resource_id=call.resource_id),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


_OPERATIONS = {
    "fake.documents.get": _OperationDefinition(
        "GET", "https://fake-provider.example.test/documents/{resource_id}", False
    ),
    "fake.documents.update": _OperationDefinition(
        "PATCH", "https://fake-provider.example.test/documents/{resource_id}", True
    ),
}
_CALL_VALIDATORS = {False: _validate_read, True: _validate_update}

__all__ = (
    "BrokerResourceCall",
    "ClosedBrokerResource",
    "ClosedOperationRejected",
    "FakeOAuthProvider",
    "ProviderCredentialRejected",
    "ProviderResponseLost",
    "SafeProviderRecord",
)
