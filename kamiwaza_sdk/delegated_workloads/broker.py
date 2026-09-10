"""Typed no-secret client for delegated credential broker operations."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Callable
from typing import NoReturn, cast
from uuid import UUID

from pydantic import Field, field_validator

from kamiwaza_sdk.delegated_workloads._protocol import (
    base_url as normalized_base_url,
)
from kamiwaza_sdk.delegated_workloads._protocol import json_bytes, validated
from kamiwaza_sdk.delegated_workloads.errors import DelegatedProtocolError
from kamiwaza_sdk.delegated_workloads.models import (
    DelegatedRequest,
    DelegatedResponse,
    Digest,
    EffectDecision,
    EffectReservation,
    EffectReservationStatus,
)
from kamiwaza_sdk.delegated_workloads.proof import (
    BrokerHandle,
    DelegatedCapability,
    _secret_value,
)
from kamiwaza_sdk.delegated_workloads.transport import (
    DelegatedProtocolRequest,
    DelegatedWorkloadTransport,
    ProtocolRetrySafety,
    SessionPort,
    checked_json_response,
)

_SECRET_FRAGMENTS = ("authorization", "credential", "password", "secret", "token")
_SERIALIZATION_ERROR = "opaque delegated broker leases cannot be serialized"
_MAX_DEPTH = 8
_MAX_NODES = 4096


class CredentialMode(str, Enum):
    BROKERED = "brokered"
    EPHEMERAL_TOKEN = "ephemeral_token"


class CredentialBindingStatus(str, Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    EXPIRED = "expired"
    REVOKED = "revoked"


class CredentialUseStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class CredentialBindingSummary(DelegatedResponse):
    id: UUID
    provider: str
    display_name: str
    allowed_operations: tuple[str, ...]
    mode: CredentialMode
    status: CredentialBindingStatus
    revocation_supported: bool
    maximum_ephemeral_ttl_seconds: int | None = Field(default=None, ge=1, le=900)

    @field_validator("allowed_operations")
    @classmethod
    def validate_operations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or len(values) != len(set(values)):
            raise ValueError("credential operations are invalid")
        return values


class CredentialOperationParameters(DelegatedRequest):
    params: dict[str, object] | None = None
    body: dict[str, object] | None = None
    resource_id: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("params", "body", mode="before")
    @classmethod
    def validate_parameters(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("credential parameters are invalid")
        _validate_parameter_tree(value)
        return value


class CredentialUseRequest(DelegatedRequest):
    credential_binding_id: UUID
    operation_id: str = Field(min_length=1, max_length=256)
    request_digest: Digest
    parameters: CredentialOperationParameters


class CredentialUseResponse(DelegatedResponse):
    lease_id: UUID
    status: CredentialUseStatus
    result: dict[str, object]
    correlation_id: UUID


@dataclass(frozen=True, slots=True)
class TrustedAdapterLease:
    """Process-local broker authority with no provider credential value."""

    effect_id: UUID
    request: CredentialUseRequest
    _broker_handle: BrokerHandle = field(repr=False)
    _effect_capability: DelegatedCapability = field(repr=False)

    @classmethod
    def from_effect(
        cls,
        effect: EffectReservation,
        request: CredentialUseRequest,
    ) -> TrustedAdapterLease:
        _require_broker_effect(effect)
        handle = cast(BrokerHandle, effect.broker_handle)
        capability = cast(DelegatedCapability, effect.effect_capability)
        return cls(effect.effect_id, request, handle, capability)

    def __reduce__(self) -> NoReturn:
        raise TypeError(_SERIALIZATION_ERROR)

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        raise TypeError(_SERIALIZATION_ERROR)


class CredentialBroker:
    """Discover safe bindings and execute one exact Core-brokered operation."""

    def __init__(
        self,
        base_url: str,
        transport: DelegatedWorkloadTransport,
        *,
        member_session: SessionPort | None = None,
    ) -> None:
        self._base_url = normalized_base_url(base_url)
        self._transport = transport
        self._member_session = member_session

    def list_bindings(
        self,
        client_id: UUID,
        *,
        operation: str | None = None,
    ) -> tuple[CredentialBindingSummary, ...]:
        session = self._member_session
        if session is None:
            raise ValueError("member session is unavailable")
        response = session.request(
            "GET",
            self._base_url + "/credential-bindings",
            params=_binding_query(client_id, operation),
        )
        payload = checked_json_response(response)
        if not isinstance(payload, list):
            raise DelegatedProtocolError(response.status_code)
        return tuple(validated(CredentialBindingSummary, item) for item in payload)

    def execute(self, lease: TrustedAdapterLease) -> CredentialUseResponse:
        request = DelegatedProtocolRequest(
            method="POST",
            url=self._base_url + "/credential-uses",
            body=json_bytes(_broker_body(lease)),
            capability=lease._effect_capability,
            retry_safety=ProtocolRetrySafety.NEVER,
        )
        payload = self._transport.send_json(request)
        return validated(CredentialUseResponse, payload)


def _binding_query(client_id: UUID, operation: str | None) -> dict[str, str]:
    query = {"client_id": str(client_id)}
    if operation is not None:
        query["operation"] = operation
    return query


def _broker_body(lease: TrustedAdapterLease) -> dict[str, object]:
    request = lease.request
    return {
        "effect_id": str(lease.effect_id),
        "broker_handle": _secret_value(lease._broker_handle),
        "credential_binding_id": str(request.credential_binding_id),
        "operation_id": request.operation_id,
        "request_digest": request.request_digest,
        "parameters": request.parameters.model_dump(mode="json", exclude_none=True),
    }


def _require_broker_effect(effect: EffectReservation) -> None:
    actual = (
        effect.decision,
        effect.status,
        effect.broker_handle is not None,
        effect.effect_capability is not None,
    )
    expected = (EffectDecision.ALLOW, EffectReservationStatus.RESERVED, True, True)
    if actual != expected:
        raise ValueError("broker authority is unavailable")


def _validate_parameter_tree(root: dict[str, object]) -> None:
    pending: list[tuple[object, int]] = [(root, 0)]
    visited = 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if visited > _MAX_NODES or depth > _MAX_DEPTH:
            raise ValueError("credential parameters are invalid")
        pending.extend(_parameter_children(value, depth))


def _parameter_children(value: object, depth: int) -> list[tuple[object, int]]:
    if isinstance(value, dict):
        _validate_parameter_keys(value)
        return [(nested, depth + 1) for nested in value.values()]
    if isinstance(value, list):
        return [(nested, depth + 1) for nested in value]
    _validate_parameter_scalar(value)
    return []


def _validate_parameter_keys(value: dict[object, object]) -> None:
    for key in value:
        if not isinstance(key, str):
            raise ValueError("credential parameters are invalid")
        normalized = key.casefold().replace("-", "_")
        if len(key) > 256 or any(part in normalized for part in _SECRET_FRAGMENTS):
            raise ValueError("credential parameters are invalid")


def _validate_parameter_scalar(value: object) -> None:
    validator = _SCALAR_VALIDATORS.get(type(value), _reject_parameter)
    validator(value)


def _validate_finite_float(value: object) -> None:
    if not math.isfinite(cast(float, value)):
        raise ValueError("credential parameters are invalid")


def _validate_bounded_string(value: object) -> None:
    if len(cast(str, value)) > 65_536:
        raise ValueError("credential parameters are invalid")


def _accept_parameter(_value: object) -> None:
    return


def _reject_parameter(_value: object) -> NoReturn:
    raise ValueError("credential parameters are invalid")


_ScalarValidator = Callable[[object], None]
_SCALAR_VALIDATORS: dict[type, _ScalarValidator] = {
    type(None): _accept_parameter,
    bool: _accept_parameter,
    float: _validate_finite_float,
    int: _accept_parameter,
    str: _validate_bounded_string,
}


__all__ = (
    "CredentialBindingStatus",
    "CredentialBindingSummary",
    "CredentialBroker",
    "CredentialMode",
    "CredentialOperationParameters",
    "CredentialUseRequest",
    "CredentialUseResponse",
    "CredentialUseStatus",
    "TrustedAdapterLease",
)
