"""Typed control-plane operations for delegated workload runs."""

from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic import ValidationError

from kamiwaza_sdk.delegated_workloads.errors import DelegatedProtocolError
from kamiwaza_sdk.delegated_workloads.models import (
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


class DelegatedControlPlaneClient:
    """Reserve idempotent runs without placing authority on the queue."""

    def __init__(self, base_url: str, transport: DelegatedWorkloadTransport) -> None:
        self._base_url = _base_url(base_url)
        self._transport = transport

    def reserve_run(
        self,
        request: RunReservationRequest,
        authority: WorkloadReadAuthority,
    ) -> RunReservation:
        """Reserve one occurrence and return its opaque queue handoff."""
        protocol_request = DelegatedProtocolRequest(
            method="POST",
            url=self._base_url + "/runs",
            body=_json_bytes(request.model_dump(mode="json")),
            extra_headers=(
                (_WORKLOAD_ASSERTION_HEADER, authority.workload_assertion),
            ),
            retry_safety=ProtocolRetrySafety.IDEMPOTENT_PROTOCOL,
        )
        return _reservation(self._transport.send_json(protocol_request))


def _reservation(payload: object) -> RunReservation:
    try:
        return RunReservation.model_validate(payload)
    except ValidationError as exc:
        raise DelegatedProtocolError() from exc


def _json_bytes(body: Mapping[str, object]) -> bytes:
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


def _base_url(value: str) -> str:
    resolved = value.rstrip("/")
    if not resolved:
        raise ValueError("delegated workload base URL is missing")
    return resolved
