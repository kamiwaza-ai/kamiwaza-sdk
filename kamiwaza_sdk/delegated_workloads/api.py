"""Typed SDK calls for delegated reads, resource guards, and approvals."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Generator, Mapping
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError

from kamiwaza_sdk.delegated_workloads.errors import DelegatedProtocolError
from kamiwaza_sdk.delegated_workloads.models import (
    ApprovalDecisionRequest,
    DelegatedGuardAuthority,
    EffectAuthorization,
    EffectAuthorizationRequest,
    EffectConsumption,
    EffectConsumptionRequest,
    EffectDetail,
    RunDetail,
    WorkloadReadAuthority,
)
from kamiwaza_sdk.delegated_workloads.transport import (
    DelegatedProtocolRequest,
    DelegatedWorkloadTransport,
    ProtocolRetrySafety,
    SessionPort,
    checked_json_response,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_WORKLOAD_ASSERTION_HEADER = "X-Kamiwaza-Workload-Assertion"
_CONSUMPTION_HEADER = "X-Kamiwaza-Effect-Consumption"


class DelegatedWorkloadAPI:
    """Low-level typed workload and protected-resource protocol surface."""

    def __init__(self, base_url: str, transport: DelegatedWorkloadTransport) -> None:
        self._base_url = _base_url(base_url)
        self._transport = transport

    def get_run(self, run_id: UUID, authority: WorkloadReadAuthority) -> RunDetail:
        payload = self._read(f"/runs/{run_id}", authority)
        return _validated(RunDetail, payload)

    def get_effect(
        self, effect_id: UUID, authority: WorkloadReadAuthority
    ) -> EffectDetail:
        payload = self._read(f"/effects/{effect_id}", authority)
        return _validated(EffectDetail, payload)

    def authorize_effect(
        self,
        request: EffectAuthorizationRequest,
        authority: DelegatedGuardAuthority,
    ) -> EffectAuthorization:
        payload = self._guard_request(
            "/effect-authorizations",
            request.model_dump(mode="json"),
            authority,
        )
        return _validated(EffectAuthorization, payload)

    def consume_effect(
        self,
        request: EffectConsumptionRequest,
        authority: DelegatedGuardAuthority,
    ) -> EffectConsumption:
        if authority.consumption_token is None:
            raise ValueError("effect consumption token is missing")
        body = request.model_dump(mode="json", exclude={"effect_id"})
        payload = self._guard_request(
            f"/effects/{request.effect_id}/consumption",
            body,
            authority,
        )
        return _validated(EffectConsumption, payload)

    def _read(self, path: str, authority: WorkloadReadAuthority) -> object:
        request = DelegatedProtocolRequest(
            method="GET",
            url=self._base_url + path,
            body=b"",
            extra_headers=((_WORKLOAD_ASSERTION_HEADER, authority.workload_assertion),),
            retry_safety=ProtocolRetrySafety.IDEMPOTENT_PROTOCOL,
        )
        return self._transport.send_json(request)

    def _guard_request(
        self,
        path: str,
        body: Mapping[str, object],
        authority: DelegatedGuardAuthority,
    ) -> object:
        request = DelegatedProtocolRequest(
            method="POST",
            url=self._base_url + path,
            body=_json_bytes(body),
            capability=authority.capability,
            extra_headers=_guard_headers(authority),
            retry_safety=ProtocolRetrySafety.IDEMPOTENT_PROTOCOL,
        )
        return self._transport.send_json(request)


class DelegatedApprovalAPI:
    """Member-session approval polling and exact decision calls."""

    def __init__(self, base_url: str, session: SessionPort) -> None:
        self._base_url = _base_url(base_url)
        self._session = session

    def list_pending(self, tenant_id: UUID) -> tuple[EffectDetail, ...]:
        response = self._session.request(
            "GET",
            self._base_url + "/effects/pending-approval",
            params={"tenant_id": str(tenant_id)},
        )
        payload = checked_json_response(response)
        if not isinstance(payload, list):
            raise DelegatedProtocolError(response.status_code)
        return tuple(_validated(EffectDetail, item) for item in payload)

    def watch_pending(
        self,
        tenant_id: UUID,
        *,
        poll_interval_seconds: float = 5.0,
        wait: Callable[[float], object] = time.sleep,
    ) -> Generator[tuple[EffectDetail, ...], None, None]:
        if poll_interval_seconds <= 0:
            raise ValueError("approval poll interval must be positive")
        while True:
            yield self.list_pending(tenant_id)
            wait(poll_interval_seconds)

    def decide(self, request: ApprovalDecisionRequest) -> EffectDetail:
        body = request.model_dump(
            mode="json",
            exclude={"effect_id", "csrf_token"},
        )
        response = self._session.request(
            "POST",
            self._base_url + f"/effects/{request.effect_id}/approval",
            data=_json_bytes(body),
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": request.csrf_token,
            },
        )
        return _validated(EffectDetail, checked_json_response(response))


def _validated(model: type[_ModelT], payload: object) -> _ModelT:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise DelegatedProtocolError() from exc


def _json_bytes(body: Mapping[str, object]) -> bytes:
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


def _base_url(value: str) -> str:
    resolved = value.rstrip("/")
    if not resolved:
        raise ValueError("delegated workload base URL is missing")
    return resolved


def _guard_headers(authority: DelegatedGuardAuthority) -> tuple[tuple[str, str], ...]:
    headers = [(_WORKLOAD_ASSERTION_HEADER, authority.workload_assertion)]
    if authority.consumption_token is not None:
        headers.append((_CONSUMPTION_HEADER, authority.consumption_token))
    return tuple(headers)
