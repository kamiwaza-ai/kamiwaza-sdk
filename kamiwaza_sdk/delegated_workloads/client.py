"""Typed control-plane operations for delegated workload runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar
from uuid import UUID

from kamiwaza_sdk.delegated_workloads._protocol import (
    base_url as normalized_base_url,
)
from kamiwaza_sdk.delegated_workloads._protocol import json_bytes, validated
from kamiwaza_sdk.delegated_workloads.executor import DelegatedExecutorClient
from kamiwaza_sdk.delegated_workloads.models import (
    AutomationRevision,
    ConsentRequest,
    IntentStatus,
    RunReservation,
    RunReservationRequest,
    WorkloadReadAuthority,
)
from kamiwaza_sdk.delegated_workloads.transport import (
    DelegatedProtocolRequest,
    DelegatedWorkloadTransport,
    ProtocolRetrySafety,
)

_WORKLOAD_ASSERTION_HEADER = "X-Kamiwaza-Workload-Assertion"
_ResponseT = TypeVar(
    "_ResponseT",
    ConsentRequest,
    IntentStatus,
    RunReservation,
)


@dataclass(frozen=True, slots=True)
class _ControlPlaneCall:
    method: str
    path: str
    body: bytes
    authority: WorkloadReadAuthority | None

    @classmethod
    def post(
        cls,
        path: str,
        request: AutomationRevision | RunReservationRequest,
        authority: WorkloadReadAuthority | None,
    ) -> _ControlPlaneCall:
        return cls(
            method="POST",
            path=path,
            body=json_bytes(request.model_dump(mode="json")),
            authority=authority,
        )


class DelegatedWorkloadClient:
    """Create role-specific clients without exposing registrar authority."""

    def __init__(self, base_url: str, transport: DelegatedWorkloadTransport) -> None:
        self._base_url = normalized_base_url(base_url)
        self._transport = transport

    def control_plane(self) -> DelegatedControlPlaneClient:
        """Return the registered control-plane role client."""
        return DelegatedControlPlaneClient(self._base_url, self._transport)

    def executor(self) -> DelegatedExecutorClient:
        """Return the registered executor role client."""
        return DelegatedExecutorClient(self._base_url, self._transport)


class DelegatedControlPlaneClient:
    """Create consent intents and reserve runs for a control-plane workload."""

    def __init__(self, base_url: str, transport: DelegatedWorkloadTransport) -> None:
        self._base_url = normalized_base_url(base_url)
        self._transport = transport

    def reserve_run(
        self,
        request: RunReservationRequest,
        authority: WorkloadReadAuthority | None = None,
    ) -> RunReservation:
        """Reserve one occurrence and return its opaque queue handoff."""
        return self._send(
            _ControlPlaneCall.post("/runs", request, authority),
            response_model=RunReservation,
        )

    def create_intent(
        self,
        automation_revision: AutomationRevision,
        authority: WorkloadReadAuthority | None = None,
    ) -> ConsentRequest:
        """Create an immutable proposal and return its Core consent link."""
        return self._send(
            _ControlPlaneCall.post("/intents", automation_revision, authority),
            response_model=ConsentRequest,
        )

    def get_intent(
        self,
        intent_id: UUID,
        authority: WorkloadReadAuthority | None = None,
    ) -> IntentStatus:
        """Poll the safe consent result for an intent created by this workload."""
        return self._send(
            _ControlPlaneCall(
                method="GET",
                path=f"/intents/{intent_id}",
                body=b"",
                authority=authority,
            ),
            response_model=IntentStatus,
        )

    def _send(
        self,
        call: _ControlPlaneCall,
        *,
        response_model: type[_ResponseT],
    ) -> _ResponseT:
        resolved = call.authority or WorkloadReadAuthority(
            self._transport.workload_assertion()
        )
        protocol_request = DelegatedProtocolRequest(
            method=call.method,
            url=self._base_url + call.path,
            body=call.body,
            extra_headers=(
                (
                    _WORKLOAD_ASSERTION_HEADER,
                    resolved.workload_assertion,
                ),
            ),
            retry_safety=ProtocolRetrySafety.IDEMPOTENT_PROTOCOL,
        )
        return validated(
            response_model,
            self._transport.send_json(protocol_request),
        )


__all__ = ("DelegatedControlPlaneClient", "DelegatedWorkloadClient")
