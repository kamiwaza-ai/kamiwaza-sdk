"""Typed public contracts for delegated run, effect, and approval state."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from dataclasses import replace
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class RunLifecycleStatus(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AMBIGUOUS = "ambiguous"


class RunClaimStatus(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    FENCED = "fenced"
    TERMINAL = "terminal"


class RunTrigger(str, Enum):
    TEST = "test"
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    RETRY = "retry"
    RECOVERY = "recovery"


class RunTransition(str, Enum):
    START = "start"
    HEARTBEAT = "heartbeat"
    ACKNOWLEDGE_CANCEL = "acknowledge_cancel"
    SUCCEED = "succeed"
    FAIL = "fail"
    CANCEL = "cancel"
    AMBIGUOUS = "ambiguous"


class EffectDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    PENDING_APPROVAL = "pending_approval"


class EffectAuthorizationDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class EffectReservationStatus(str, Enum):
    RESERVED = "reserved"
    DENIED = "denied"
    PENDING_APPROVAL = "pending_approval"


class EffectLifecycleStatus(str, Enum):
    RESERVED = "reserved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AMBIGUOUS = "ambiguous"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    DENY = "deny"


class DecisionReasonCode(str, Enum):
    ALLOWED = "allowed"
    APPROVAL_REQUIRED = "approval_required"
    CURRENT_AUTHORITY_DENIED = "current_authority_denied"
    GRANT_INACTIVE = "grant_inactive"
    REGISTRATION_INACTIVE = "registration_inactive"
    REVISION_MISMATCH = "revision_mismatch"
    RESOURCE_CONTRACT_UNKNOWN = "resource_contract_unknown"
    QUOTA_DENIED = "quota_denied"
    CAPABILITY_FAMILY_UNAVAILABLE = "capability_family_unavailable"
    CREDENTIAL_BINDING_UNAVAILABLE = "credential_binding_unavailable"
    CANCELLATION_REQUESTED = "cancellation_requested"
    REPLAY_DETECTED = "replay_detected"
    POLICY_CHANGED = "policy_changed"
    DESTINATION_DENIED = "destination_denied"


class DelegatedResponse(BaseModel):
    """Forward-compatible immutable response base."""

    model_config = ConfigDict(extra="allow", frozen=True)


class DelegatedRequest(BaseModel):
    """Closed immutable request base."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ReasonedResponse(DelegatedResponse):
    reason_codes: tuple[DecisionReasonCode, ...]

    @field_validator("reason_codes")
    @classmethod
    def validate_unique_reasons(
        cls, values: tuple[DecisionReasonCode, ...]
    ) -> tuple[DecisionReasonCode, ...]:
        if len(values) != len(set(values)):
            raise ValueError("decision reason codes contain a duplicate")
        return values


class ResourceRef(DelegatedResponse):
    type: str
    descriptor_version: str
    id: str


class RunDetail(DelegatedResponse):
    run_id: UUID
    claim_id: UUID | None
    run_status: RunLifecycleStatus
    claim_status: RunClaimStatus | None
    lease_expires_at: datetime | None
    authority_deadline: datetime
    correlation_id: UUID
    grant_id: UUID
    occurrence_key: str
    revision_digest: Digest
    updated_at: datetime


class RunReservationRequest(DelegatedRequest):
    grant_id: UUID
    revision_digest: Digest
    occurrence_key: str = Field(min_length=1, max_length=256)
    trigger: RunTrigger


class OpaqueRunQueuePayload(DelegatedRequest):
    """Transport-only queue handoff with no delegated authority."""

    run_reference: str = Field(min_length=32, repr=False)


class RunReservation(DelegatedResponse):
    run_id: UUID
    status: Literal["queued"]
    run_reference: str = Field(min_length=32, repr=False)
    correlation_id: UUID
    authority_deadline: datetime

    def queue_payload(self) -> OpaqueRunQueuePayload:
        """Return the only value an untrusted queue may transport."""
        return OpaqueRunQueuePayload(run_reference=self.run_reference)


class ClaimedRun(DelegatedResponse):
    run_id: UUID
    claim_id: UUID
    status: Literal["claimed"]
    fencing_token: int = Field(ge=1)
    lease_expires_at: datetime
    run_capability: str = Field(min_length=1, repr=False)
    expires_at: datetime
    authority_deadline: datetime
    correlation_id: UUID

    def authority(self, workload_assertion: str) -> DelegatedRunAuthority:
        """Bind the claim's fence and capability to its workload assertion."""
        return DelegatedRunAuthority(
            run_id=self.run_id,
            claim_id=self.claim_id,
            fencing_token=self.fencing_token,
            capability=self.run_capability,
            workload_assertion=workload_assertion,
        )


class RunTransitionRequest(DelegatedRequest):
    transition: RunTransition
    outcome_category: str | None = Field(default=None, max_length=128)


class RunTransitionResult(DelegatedResponse):
    run_id: UUID
    claim_id: UUID | None
    run_status: RunLifecycleStatus
    claim_status: RunClaimStatus | None
    lease_expires_at: datetime | None
    authority_deadline: datetime
    correlation_id: UUID


class EffectResourceRef(DelegatedRequest):
    type: str = Field(min_length=1)
    descriptor_version: str = Field(pattern=r"^v[1-9][0-9]*$")
    id: str = Field(min_length=1)


class DestinationRef(DelegatedRequest):
    scheme: Literal["https"] = "https"
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    route_template: str | None = None


class EffectReservationRequest(DelegatedRequest):
    effect_key: str = Field(min_length=1, max_length=256)
    effect_digest: Digest
    action: str = Field(min_length=1)
    resource: EffectResourceRef
    audience: str = Field(min_length=1)
    destination: DestinationRef | None = None
    credential_binding_id: UUID | None = None


class EffectReservation(ReasonedResponse):
    effect_id: UUID
    decision: EffectDecision
    status: EffectReservationStatus
    policy_version: str
    effect_capability: str | None = Field(default=None, repr=False)
    broker_handle: str | None = Field(default=None, repr=False)
    valid_until: datetime | None
    correlation_id: UUID


class EffectDetail(EffectReservation):
    run_id: UUID
    effect_key: str
    request_digest: Digest
    lifecycle_status: EffectLifecycleStatus
    consumed_at: datetime | None
    approval_id: UUID | None = None
    resume_run_reference: str | None = Field(default=None, repr=False)
    updated_at: datetime


class DelegatedRequesterContext(DelegatedResponse):
    tenant_id: UUID
    subject_id: UUID
    client_id: UUID
    workload_role_id: UUID
    workload_instance_id: UUID
    workload_revision_id: UUID
    resource_registration_revision_id: UUID
    run_claim_id: UUID
    activation_epoch: int = Field(ge=1)
    quota_reservation_id: UUID | None = None
    member_charge_owner_id: UUID | None = None
    workload_budget_subject_id: UUID | None = None
    grant_id: UUID
    run_id: UUID
    effect_id: UUID
    action: str
    resource: ResourceRef
    audience: str
    policy_version: str
    fencing_token: int = Field(ge=1)
    authority_envelope_id: UUID
    correlation_id: UUID


class EffectAuthorization(ReasonedResponse):
    effect_id: UUID
    decision: EffectAuthorizationDecision
    requester_context: DelegatedRequesterContext | None = None
    consumption_token: str | None = Field(default=None, repr=False)
    correlation_id: UUID

    @model_validator(mode="after")
    def validate_authority_shape(self) -> EffectAuthorization:
        if self.decision is EffectAuthorizationDecision.ALLOW:
            if self.requester_context is None or self.consumption_token is None:
                raise ValueError("allow decision is missing guarded authority")
            return self
        if self.requester_context is not None or self.consumption_token is not None:
            raise ValueError("non-allow decision cannot carry guarded authority")
        return self


class EffectConsumption(DelegatedResponse):
    effect_id: UUID
    status: EffectLifecycleStatus
    requester_context: DelegatedRequesterContext | None = None
    correlation_id: UUID


class EffectAuthorizationRequest(DelegatedRequest):
    effect_id: UUID
    request_digest: Digest
    method: str = Field(min_length=1)
    target_uri: str = Field(min_length=1)


class EffectConsumptionRequest(DelegatedRequest):
    effect_id: UUID
    request_digest: Digest
    fencing_token: int = Field(ge=1)


class ApprovalDecisionRequest(DelegatedRequest):
    effect_id: UUID
    effect_digest: Digest
    policy_version: str
    decision: ApprovalDecision
    csrf_token: str = Field(repr=False, min_length=1)


@dataclass(frozen=True, slots=True)
class WorkloadReadAuthority:
    workload_assertion: str = dataclass_field(repr=False)

    def __post_init__(self) -> None:
        if not self.workload_assertion:
            raise ValueError("workload assertion is missing")


@dataclass(frozen=True, slots=True, kw_only=True)
class DelegatedRunAuthority:
    run_id: UUID
    claim_id: UUID
    fencing_token: int
    capability: str = dataclass_field(repr=False)
    workload_assertion: str = dataclass_field(repr=False)

    def __post_init__(self) -> None:
        if self.fencing_token < 1:
            raise ValueError("delegated run fencing token must be positive")
        if not self.capability or not self.workload_assertion:
            raise ValueError("delegated run authority is incomplete")


@dataclass(frozen=True, slots=True)
class DelegatedGuardAuthority:
    capability: str = dataclass_field(repr=False)
    workload_assertion: str = dataclass_field(repr=False)
    consumption_token: str | None = dataclass_field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.capability or not self.workload_assertion:
            raise ValueError("delegated guard authority is incomplete")

    def with_consumption(self, token: str) -> DelegatedGuardAuthority:
        if not token:
            raise ValueError("effect consumption token is missing")
        return replace(self, consumption_token=token)
