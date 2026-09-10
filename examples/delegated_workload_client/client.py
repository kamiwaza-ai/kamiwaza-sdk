"""Neutral control-plane and executor example built only on the public SDK."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from kamiwaza_sdk.delegated_workloads import (
    AttestationProfile,
    ClaimedRun,
    DelegatedExecutorClient,
    DelegatedRunAuthority,
    DelegatedWorkloadClient,
    DelegatedWorkloadTransport,
    EffectReservation,
    EffectReservationRequest,
    OpaqueRunQueuePayload,
    RunReservation,
    RunReservationRequest,
    RunTransition,
    RunTransitionRequest,
    RunTransitionResult,
    WorkloadProof,
)
from kamiwaza_sdk.delegated_workloads.transport import SessionPort


_PROTOCOL_PATH = "/api/v1/delegated-workloads"


@dataclass(frozen=True, slots=True, kw_only=True)
class NeutralClientConfig:
    """Public protocol location and registrar-selected attestation profile."""

    base_url: str
    profile: AttestationProfile = AttestationProfile.KUBERNETES_OFFLINE_V1

    def __post_init__(self) -> None:
        origin = urlsplit(self.base_url)
        valid = (
            origin.scheme == "https",
            bool(origin.netloc),
            origin.path.rstrip("/") == _PROTOCOL_PATH,
            not origin.query,
            not origin.fragment,
        )
        if not all(valid):
            raise ValueError("neutral delegated-workload protocol URL is invalid")


class NeutralClaim:
    """One process-local fenced claim with no serializable authority surface."""

    __slots__ = ("_authority", "_claim", "_executor")

    def __init__(
        self,
        executor: DelegatedExecutorClient,
        claim: ClaimedRun,
        authority: DelegatedRunAuthority,
    ) -> None:
        self._executor = executor
        self._claim = claim
        self._authority = authority

    def transition(
        self,
        transition: RunTransition,
        outcome_category: str | None = None,
    ) -> RunTransitionResult:
        """Apply one lifecycle transition under this claim's current fence."""
        request = RunTransitionRequest(
            transition=transition,
            outcome_category=outcome_category,
        )
        return self._executor.transition(request, self._authority)

    def reserve_effect(self, request: EffectReservationRequest) -> EffectReservation:
        """Reserve one canonical exact effect under this claim."""
        return self._executor.reserve_effect(request, self._authority)

    def safe_summary(self) -> dict[str, str | int]:
        """Return only safe correlation fields for application logs."""
        return {
            "run_id": str(self._claim.run_id),
            "claim_id": str(self._claim.claim_id),
            "correlation_id": str(self._claim.correlation_id),
            "fencing_token": self._claim.fencing_token,
        }

    def __repr__(self) -> str:
        return f"NeutralClaim(run_id={self._claim.run_id!s}, authority=<redacted>)"


class NeutralWorkloadClient:
    """Role-neutral client with Kubernetes proof acquisition and safe handoff."""

    def __init__(
        self,
        config: NeutralClientConfig,
        session: SessionPort,
        *,
        proof: WorkloadProof | None = None,
    ) -> None:
        selected = proof or WorkloadProof.kubernetes(config.profile)
        self._transport = DelegatedWorkloadTransport(session, proof=selected)
        client = DelegatedWorkloadClient(config.base_url, self._transport)
        self._control_plane = client.control_plane()
        self._executor = client.executor()

    def reserve_run(self, request: RunReservationRequest) -> RunReservation:
        """Reserve an occurrence through the registered control-plane role."""
        return self._control_plane.reserve_run(request)

    def claim_run(
        self,
        message: Mapping[str, object] | OpaqueRunQueuePayload,
    ) -> NeutralClaim:
        """Attest and claim an opaque queue handoff through the executor role."""
        payload = _queue_payload(message)
        claim = self._executor.claim_run(payload)
        authority = self._executor.authority(claim)
        return NeutralClaim(self._executor, claim, authority)

    def close(self) -> None:
        """Close the process-local proof-key lifecycle."""
        self._transport.close()

    def __enter__(self) -> NeutralWorkloadClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def queue_message(reservation: RunReservation) -> dict[str, str]:
    """Return the only payload permitted on the untrusted queue."""
    payload = reservation.queue_payload()
    return {"run_reference": payload.run_reference}


def _queue_payload(
    message: Mapping[str, object] | OpaqueRunQueuePayload,
) -> OpaqueRunQueuePayload:
    if isinstance(message, OpaqueRunQueuePayload):
        return message
    return OpaqueRunQueuePayload.model_validate(dict(message))


__all__ = (
    "NeutralClaim",
    "NeutralClientConfig",
    "NeutralWorkloadClient",
    "queue_message",
)
