"""Typed control-plane operations for delegated workload runs."""

from __future__ import annotations

from kamiwaza_sdk.delegated_workloads._protocol import (
    base_url as normalized_base_url,
)
from kamiwaza_sdk.delegated_workloads._protocol import json_bytes, validated
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
        self._base_url = normalized_base_url(base_url)
        self._transport = transport

    def reserve_run(
        self,
        request: RunReservationRequest,
        authority: WorkloadReadAuthority | None = None,
    ) -> RunReservation:
        """Reserve one occurrence and return its opaque queue handoff."""
        resolved = authority or WorkloadReadAuthority(
            self._transport.workload_assertion()
        )
        protocol_request = DelegatedProtocolRequest(
            method="POST",
            url=self._base_url + "/runs",
            body=json_bytes(request.model_dump(mode="json")),
            extra_headers=(
                (
                    _WORKLOAD_ASSERTION_HEADER,
                    resolved.workload_assertion,
                ),
            ),
            retry_safety=ProtocolRetrySafety.IDEMPOTENT_PROTOCOL,
        )
        return validated(RunReservation, self._transport.send_json(protocol_request))
