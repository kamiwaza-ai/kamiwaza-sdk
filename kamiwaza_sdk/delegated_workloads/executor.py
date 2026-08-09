"""Typed executor claim, lifecycle, and exact-effect operations."""

from __future__ import annotations

from collections.abc import Mapping

from kamiwaza_sdk.delegated_workloads._protocol import (
    base_url as normalized_base_url,
)
from kamiwaza_sdk.delegated_workloads._protocol import json_bytes, validated
from kamiwaza_sdk.delegated_workloads.models import (
    ClaimedRun,
    DelegatedRunAuthority,
    EffectReservation,
    EffectReservationRequest,
    OpaqueRunQueuePayload,
    RunTransitionRequest,
    RunTransitionResult,
    WorkloadReadAuthority,
)
from kamiwaza_sdk.delegated_workloads.proof import (
    DelegatedCapability,
    SensitiveValue,
    WorkloadAssertion,
)
from kamiwaza_sdk.delegated_workloads.transport import (
    DelegatedProtocolRequest,
    DelegatedWorkloadTransport,
    ProtocolRetrySafety,
)

_WORKLOAD_ASSERTION_HEADER = "X-Kamiwaza-Workload-Assertion"


class DelegatedExecutorClient:
    """Claim opaque work and exercise only the resulting fenced authority."""

    def __init__(self, base_url: str, transport: DelegatedWorkloadTransport) -> None:
        self._base_url = normalized_base_url(base_url)
        self._transport = transport

    def claim_run(
        self,
        queue_payload: OpaqueRunQueuePayload,
        authority: WorkloadReadAuthority | None = None,
    ) -> ClaimedRun:
        """Atomically claim an opaque queue reference with this proof key."""
        resolved = authority or WorkloadReadAuthority(
            self._transport.workload_assertion()
        )
        body: dict[str, object] = {
            **queue_payload.model_dump(mode="json"),
            "executor_proof_jwk": self._transport.proof_public_jwk(),
        }
        payload = self._request(
            "/run-claims",
            body,
            extra_headers=_assertion_header(resolved.workload_assertion),
        )
        return validated(ClaimedRun, payload)

    def authority(self, claim: ClaimedRun) -> DelegatedRunAuthority:
        """Bind a claim to fresh selected-profile assertion material."""
        return claim.authority(self._transport.workload_assertion())

    def transition(
        self,
        request: RunTransitionRequest,
        authority: DelegatedRunAuthority,
    ) -> RunTransitionResult:
        """Apply a heartbeat, cancellation acknowledgement, or terminal result."""
        body = request.model_dump(mode="json", exclude_none=True)
        body["fencing_token"] = authority.fencing_token
        payload = self._request(
            f"/run-claims/{authority.claim_id}/transitions",
            body,
            capability=authority.capability,
            extra_headers=_assertion_header(authority.workload_assertion),
        )
        return validated(RunTransitionResult, payload)

    def reserve_effect(
        self,
        request: EffectReservationRequest,
        authority: DelegatedRunAuthority,
    ) -> EffectReservation:
        """Reserve one digest-bound effect under the claimed run capability."""
        payload = self._request(
            f"/runs/{authority.run_id}/effects",
            request.model_dump(mode="json", exclude_none=True),
            capability=authority.capability,
        )
        return validated(EffectReservation, payload)

    def _request(
        self,
        path: str,
        body: Mapping[str, object],
        *,
        capability: DelegatedCapability | str | None = None,
        extra_headers: tuple[tuple[str, SensitiveValue | str], ...] = (),
    ) -> object:
        protocol_request = DelegatedProtocolRequest(
            method="POST",
            url=self._base_url + path,
            body=json_bytes(body),
            capability=capability,
            extra_headers=extra_headers,
            retry_safety=ProtocolRetrySafety.IDEMPOTENT_PROTOCOL,
        )
        return self._transport.send_json(protocol_request)


def _assertion_header(
    assertion: WorkloadAssertion | str,
) -> tuple[tuple[str, SensitiveValue | str]]:
    return ((_WORKLOAD_ASSERTION_HEADER, assertion),)
