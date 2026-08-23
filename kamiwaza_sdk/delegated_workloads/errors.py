"""Closed safe failures for delegated-workload protocol responses."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Protocol
from uuid import UUID

from kamiwaza_sdk.delegated_workloads.proof import DPoPNonce
from kamiwaza_sdk.exceptions import KamiwazaError


class DelegatedIdentityError(KamiwazaError):
    """Base for safe local workload-identity failures."""


class WorkloadAssertionUnavailable(DelegatedIdentityError):
    """The selected adapter could not obtain safe assertion material."""

    def __init__(self) -> None:
        super().__init__("workload assertion is unavailable")


class UnsupportedAttestationProfile(DelegatedIdentityError):
    """The selected profile has no trusted SDK adapter."""

    def __init__(self) -> None:
        super().__init__("attestation profile is unsupported")


class ProofKeyUnavailable(DelegatedIdentityError):
    """The ephemeral proof-key lifecycle is already closed."""

    def __init__(self) -> None:
        super().__init__("workload proof key is unavailable")


class DelegatedErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    READINESS_UNAVAILABLE = "readiness_unavailable"
    INCOMPATIBLE_CONTRACT = "incompatible_contract"
    REGISTRATION_REJECTED = "registration_rejected"
    RESOURCE_REGISTRATION_REJECTED = "resource_registration_rejected"
    ATTESTATION_REJECTED = "attestation_rejected"
    GRANT_INACTIVE = "grant_inactive"
    REVISION_MISMATCH = "revision_mismatch"
    CURRENT_AUTHORITY_DENIED = "current_authority_denied"
    CAPABILITY_EXPIRED = "capability_expired"
    PROOF_MISMATCH = "proof_mismatch"
    DPOP_NONCE_REQUIRED = "dpop_nonce_required"
    REPLAY_REJECTED = "replay_rejected"
    CLAIM_CONFLICT = "claim_conflict"
    FENCED_CLAIM = "fenced_claim"
    OCCURRENCE_DIGEST_CONFLICT = "occurrence_digest_conflict"
    EFFECT_DIGEST_CONFLICT = "effect_digest_conflict"
    UNKNOWN_RESOURCE_CONTRACT = "unknown_resource_contract"
    PROTECTED_RESOURCE_REJECTED = "protected_resource_rejected"
    APPROVAL_REQUIRED = "approval_required"
    CREDENTIAL_BINDING_UNAVAILABLE = "credential_binding_unavailable"
    PROVIDER_TRANSIENT_FAILURE = "provider_transient_failure"
    AMBIGUOUS_EFFECT_OUTCOME = "ambiguous_effect_outcome"


class RetryClassification(str, Enum):
    NEVER = "never"
    AFTER_REAUTHENTICATION = "after_reauthentication"
    NONCE_REQUIRED = "nonce_required"
    BOUNDED_BACKOFF = "bounded_backoff"
    IDEMPOTENT_READ_ONLY = "idempotent_read_only"


@dataclass(frozen=True, slots=True)
class ErrorContext:
    status_code: int
    retry_classification: RetryClassification
    correlation_id: UUID | None
    safe_details: Mapping[str, object]


class DelegatedWorkloadError(KamiwazaError):
    """Base for one validated closed delegated error envelope."""

    code: ClassVar[DelegatedErrorCode]

    def __init__(self, message: str, context: ErrorContext) -> None:
        body = {
            "error": {
                "code": self.code.value,
                "retry_classification": context.retry_classification.value,
                "correlation_id": (
                    str(context.correlation_id) if context.correlation_id else None
                ),
                "safe_details": dict(context.safe_details),
            }
        }
        super().__init__(message, status_code=context.status_code, body=body)
        self.retry_classification = context.retry_classification
        self.correlation_id = context.correlation_id
        self.safe_details = dict(context.safe_details)


class DelegatedProtocolError(KamiwazaError):
    """An unknown or internally inconsistent wire response; always fail closed."""

    retry_classification = RetryClassification.NEVER

    def __init__(self, status_code: int | None = None) -> None:
        super().__init__(
            "delegated workload response violates the published contract",
            status_code=status_code,
        )
        self.correlation_id = None


class InvalidRequest(DelegatedWorkloadError):
    code = DelegatedErrorCode.INVALID_REQUEST


class ReadinessUnavailable(DelegatedWorkloadError):
    code = DelegatedErrorCode.READINESS_UNAVAILABLE


class IncompatibleContract(DelegatedWorkloadError):
    code = DelegatedErrorCode.INCOMPATIBLE_CONTRACT


class RegistrationRejected(DelegatedWorkloadError):
    code = DelegatedErrorCode.REGISTRATION_REJECTED


class ResourceRegistrationRejected(DelegatedWorkloadError):
    code = DelegatedErrorCode.RESOURCE_REGISTRATION_REJECTED


class AttestationRejected(DelegatedWorkloadError):
    code = DelegatedErrorCode.ATTESTATION_REJECTED


class GrantInactive(DelegatedWorkloadError):
    code = DelegatedErrorCode.GRANT_INACTIVE


class RevisionMismatch(DelegatedWorkloadError):
    code = DelegatedErrorCode.REVISION_MISMATCH


class CurrentAuthorityDenied(DelegatedWorkloadError):
    code = DelegatedErrorCode.CURRENT_AUTHORITY_DENIED


class CapabilityExpired(DelegatedWorkloadError):
    code = DelegatedErrorCode.CAPABILITY_EXPIRED


class ProofMismatch(DelegatedWorkloadError):
    code = DelegatedErrorCode.PROOF_MISMATCH


class DPoPNonceRequired(DelegatedWorkloadError):
    code = DelegatedErrorCode.DPOP_NONCE_REQUIRED

    def __init__(
        self,
        nonce: DPoPNonce | str,
        *,
        context: ErrorContext | None = None,
        message: str = "a fresh DPoP nonce is required",
    ) -> None:
        resolved = context or ErrorContext(
            401,
            RetryClassification.NONCE_REQUIRED,
            None,
            {},
        )
        super().__init__(message, resolved)
        self.nonce = nonce if isinstance(nonce, DPoPNonce) else DPoPNonce(nonce)


class ReplayRejected(DelegatedWorkloadError):
    code = DelegatedErrorCode.REPLAY_REJECTED


class ClaimConflict(DelegatedWorkloadError):
    code = DelegatedErrorCode.CLAIM_CONFLICT


class FencedClaim(DelegatedWorkloadError):
    code = DelegatedErrorCode.FENCED_CLAIM


class OccurrenceDigestConflict(DelegatedWorkloadError):
    code = DelegatedErrorCode.OCCURRENCE_DIGEST_CONFLICT


class EffectDigestConflict(DelegatedWorkloadError):
    code = DelegatedErrorCode.EFFECT_DIGEST_CONFLICT


class UnknownResourceContract(DelegatedWorkloadError):
    code = DelegatedErrorCode.UNKNOWN_RESOURCE_CONTRACT


class ProtectedResourceRejected(DelegatedWorkloadError):
    code = DelegatedErrorCode.PROTECTED_RESOURCE_REJECTED


class ApprovalRequired(DelegatedWorkloadError):
    code = DelegatedErrorCode.APPROVAL_REQUIRED


class CredentialBindingUnavailable(DelegatedWorkloadError):
    code = DelegatedErrorCode.CREDENTIAL_BINDING_UNAVAILABLE


class ProviderTransientFailure(DelegatedWorkloadError):
    code = DelegatedErrorCode.PROVIDER_TRANSIENT_FAILURE


class AmbiguousEffectOutcome(DelegatedWorkloadError):
    code = DelegatedErrorCode.AMBIGUOUS_EFFECT_OUTCOME


@dataclass(frozen=True, slots=True)
class ErrorRule:
    status_code: int
    retry_classification: RetryClassification
    error_type: type[DelegatedWorkloadError]
    message: str


_NEVER = RetryClassification.NEVER
_REAUTH = RetryClassification.AFTER_REAUTHENTICATION
_NONCE = RetryClassification.NONCE_REQUIRED
_BACKOFF = RetryClassification.BOUNDED_BACKOFF
_READ_ONLY = RetryClassification.IDEMPOTENT_READ_ONLY


ERROR_RULES = {
    DelegatedErrorCode.INVALID_REQUEST: ErrorRule(
        422, _NEVER, InvalidRequest, "request is invalid"
    ),
    DelegatedErrorCode.READINESS_UNAVAILABLE: ErrorRule(
        503, _BACKOFF, ReadinessUnavailable, "delegated authority is unavailable"
    ),
    DelegatedErrorCode.INCOMPATIBLE_CONTRACT: ErrorRule(
        409, _NEVER, IncompatibleContract, "contract versions are incompatible"
    ),
    DelegatedErrorCode.REGISTRATION_REJECTED: ErrorRule(
        403, _NEVER, RegistrationRejected, "registration was rejected"
    ),
    DelegatedErrorCode.RESOURCE_REGISTRATION_REJECTED: ErrorRule(
        409, _NEVER, ResourceRegistrationRejected, "resource registration was rejected"
    ),
    DelegatedErrorCode.ATTESTATION_REJECTED: ErrorRule(
        401, _REAUTH, AttestationRejected, "workload attestation was rejected"
    ),
    DelegatedErrorCode.GRANT_INACTIVE: ErrorRule(
        403, _NEVER, GrantInactive, "grant is inactive"
    ),
    DelegatedErrorCode.REVISION_MISMATCH: ErrorRule(
        403, _NEVER, RevisionMismatch, "workload revision does not match"
    ),
    DelegatedErrorCode.CURRENT_AUTHORITY_DENIED: ErrorRule(
        403, _NEVER, CurrentAuthorityDenied, "current authority denied the request"
    ),
    DelegatedErrorCode.CAPABILITY_EXPIRED: ErrorRule(
        401, _REAUTH, CapabilityExpired, "capability has expired"
    ),
    DelegatedErrorCode.PROOF_MISMATCH: ErrorRule(
        401, _NEVER, ProofMismatch, "proof binding does not match"
    ),
    DelegatedErrorCode.DPOP_NONCE_REQUIRED: ErrorRule(
        401, _NONCE, DPoPNonceRequired, "a fresh DPoP nonce is required"
    ),
    DelegatedErrorCode.REPLAY_REJECTED: ErrorRule(
        409, _NEVER, ReplayRejected, "replay was rejected"
    ),
    DelegatedErrorCode.CLAIM_CONFLICT: ErrorRule(
        409, _READ_ONLY, ClaimConflict, "run claim conflicts with current state"
    ),
    DelegatedErrorCode.FENCED_CLAIM: ErrorRule(
        409, _NEVER, FencedClaim, "run claim was fenced"
    ),
    DelegatedErrorCode.OCCURRENCE_DIGEST_CONFLICT: ErrorRule(
        409,
        _NEVER,
        OccurrenceDigestConflict,
        "occurrence digest conflicts with durable state",
    ),
    DelegatedErrorCode.EFFECT_DIGEST_CONFLICT: ErrorRule(
        409, _NEVER, EffectDigestConflict, "effect digest conflicts with durable state"
    ),
    DelegatedErrorCode.UNKNOWN_RESOURCE_CONTRACT: ErrorRule(
        422, _NEVER, UnknownResourceContract, "resource contract is unknown"
    ),
    DelegatedErrorCode.PROTECTED_RESOURCE_REJECTED: ErrorRule(
        403,
        _NEVER,
        ProtectedResourceRejected,
        "protected resource rejected the request",
    ),
    DelegatedErrorCode.APPROVAL_REQUIRED: ErrorRule(
        409, _READ_ONLY, ApprovalRequired, "exact effect approval is required"
    ),
    DelegatedErrorCode.CREDENTIAL_BINDING_UNAVAILABLE: ErrorRule(
        503, _NEVER, CredentialBindingUnavailable, "credential binding is unavailable"
    ),
    DelegatedErrorCode.PROVIDER_TRANSIENT_FAILURE: ErrorRule(
        503, _READ_ONLY, ProviderTransientFailure, "credential provider is unavailable"
    ),
    DelegatedErrorCode.AMBIGUOUS_EFFECT_OUTCOME: ErrorRule(
        409, _NEVER, AmbiguousEffectOutcome, "effect outcome is ambiguous"
    ),
}


class ErrorResponsePort(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> object: ...


def delegated_error_from_response(
    response: ErrorResponsePort,
) -> DelegatedWorkloadError | DelegatedProtocolError:
    parsed = _validated_error(response)
    if parsed is None:
        return DelegatedProtocolError(response.status_code)
    code, rule, context = parsed
    if code is DelegatedErrorCode.DPOP_NONCE_REQUIRED:
        nonce = _header(response.headers, "DPoP-Nonce")
        if nonce is None or not 16 <= len(nonce) <= 1024:
            return DelegatedProtocolError(response.status_code)
        return DPoPNonceRequired(nonce, context=context, message=rule.message)
    return rule.error_type(rule.message, context)


def _validated_error(
    response: ErrorResponsePort,
) -> tuple[DelegatedErrorCode, ErrorRule, ErrorContext] | None:
    body = _safe_json(response)
    error = body.get("error") if body is not None else None
    if not isinstance(error, Mapping):
        return None
    values = _error_values(error)
    if values is None:
        return None
    code, retry, correlation_id, safe_details = values
    rule = ERROR_RULES[code]
    if response.status_code != rule.status_code:
        return None
    if retry is not rule.retry_classification:
        return None
    return (
        code,
        rule,
        ErrorContext(response.status_code, retry, correlation_id, safe_details),
    )


def _error_values(
    error: Mapping[str, object],
) -> tuple[DelegatedErrorCode, RetryClassification, UUID, Mapping[str, object]] | None:
    try:
        code = DelegatedErrorCode(error.get("code"))
        retry = RetryClassification(error.get("retry_classification"))
        correlation_id = UUID(str(error.get("correlation_id")))
    except (TypeError, ValueError):
        return None
    message = error.get("message")
    details = error.get("safe_details", {})
    if not isinstance(message, str) or not isinstance(details, Mapping):
        return None
    return code, retry, correlation_id, dict(details)


def _safe_json(response: ErrorResponsePort) -> Mapping[str, object] | None:
    try:
        body = response.json()
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, Mapping) else None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    for key, value in headers.items():
        if key.casefold() == expected:
            return value
    return None
