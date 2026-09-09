"""Complete v1 readiness evaluation with a short revision-fenced cache."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol

from kamiwaza_sdk.delegated_workloads._protocol import (
    base_url as normalized_base_url,
)
from kamiwaza_sdk.delegated_workloads._protocol import validated
from kamiwaza_sdk.delegated_workloads.models import DelegatedResponse
from kamiwaza_sdk.delegated_workloads.proof import WorkloadAssertion
from kamiwaza_sdk.delegated_workloads.transport import (
    DelegatedProtocolRequest,
    ProtocolRetrySafety,
)


MANDATORY_V1_CAPABILITY_FAMILIES = (
    "automation_grants",
    "immutable_workload_revisions",
    "resource_registration",
    "atomic_queue_claims",
    "run_capabilities",
    "run_lifecycle",
    "effect_capabilities",
    "effect_lifecycle",
    "dpop",
    "durable_revocation",
    "durable_audit",
    "dual_principal_rebac",
    "model_attribution",
    "member_workload_quota",
    "brokered_credentials",
    "exact_effect_approval",
    "registrar_registration",
    "workload_attestation",
    "platform_consent",
    "protected_resource_guard",
)
#: Fallback for a Core that predates `family_platform_operations` on the
#: discovery document. When the document carries the mapping, that is used
#: instead — a client copy is exactly how the discovery/admission disagreement
#: this contract exists to close arose, so the served value always wins.
FAMILY_PLATFORM_OPERATIONS: Mapping[str, tuple[str, ...]] = {
    "atomic_queue_claims": ("run:claim",),
    "automation_grants": ("intent:create",),
    "brokered_credentials": ("credential:use",),
    "effect_capabilities": ("effect:reserve",),
    "effect_lifecycle": ("effect:transition",),
    "exact_effect_approval": ("effect:execute",),
    "platform_consent": ("intent:read",),
    "run_capabilities": ("run:reserve",),
    "run_lifecycle": ("run:transition",),
}

MAX_READINESS_CACHE_SECONDS = 30
_ASSERTION_HEADER = "X-Kamiwaza-Workload-Assertion"


class ComponentStatus(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"


class ReadinessDiagnosticCode(str, Enum):
    HEALTHY = "healthy"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    INCOMPATIBLE_VERSION = "incompatible_version"
    PROFILE_UNAVAILABLE = "profile_unavailable"
    RESOURCE_REGISTRATION_UNAVAILABLE = "resource_registration_unavailable"
    V1_FAMILY_MISSING = "v1_family_missing"
    ROLLOUT_DISABLED = "rollout_disabled"


class ComponentReadiness(DelegatedResponse):
    status: ComponentStatus
    reason_codes: tuple[ReadinessDiagnosticCode, ...]


class CapabilityDiscoveryDocument(DelegatedResponse):
    contract_versions: tuple[str, ...]
    attestation_profiles: tuple[str, ...]
    attestation_profile_status: Mapping[str, ComponentReadiness]
    profile_requirement_semantics: str
    resource_registrations: Mapping[str, ComponentReadiness]
    capabilities: tuple[str, ...]
    components: Mapping[str, ComponentReadiness]
    #: Served by Core so a consumer need not keep its own copy. Empty on a Core
    #: that predates the field, in which case the local fallback is used.
    family_platform_operations: Mapping[str, tuple[str, ...]] = {}
    #: Platform operations the attested caller's roles hold, as Core observed
    #: them. Absent on a Core older than the admission-aware discovery, which
    #: is why it defaults rather than being required: an old server cannot
    #: answer the question, and the evaluator must not read that silence as a
    #: grant of everything.
    permitted_platform_operations: tuple[str, ...] = ()
    #: How Core resolved the role read: "observed", "registry_unavailable" or
    #: "role_inactive". An empty permitted set means something different under
    #: each — an outage that clears itself, an assertion a fresh one would fix,
    #: or a grant to go and ask an operator for. Typed as a plain string, not
    #: an enum, so a value added later cannot make the document unparseable
    #: here. Defaults to "observed" for a Core that predates the field.
    role_resolution: str = "observed"
    checked_at: datetime
    valid_until: datetime
    ready: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceReadinessRequirement:
    resource_type: str
    descriptor_versions: frozenset[str]
    guard_versions: frozenset[str]
    adapter_ids: frozenset[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadinessRequirements:
    workload_revision_id: str
    contract_versions: frozenset[str]
    acceptable_profile_sets: Mapping[str, tuple[str, ...]]
    resources: tuple[ResourceReadinessRequirement, ...]

    def cache_fence(self) -> str:
        payload = {
            "workload_revision_id": self.workload_revision_id,
            "contract_versions": sorted(self.contract_versions),
            "profiles": sorted(self.acceptable_profile_sets.items()),
            "resources": [_resource_fence(item) for item in self.resources],
        }
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadinessResult:
    ready: bool
    selected_profiles: Mapping[str, str]
    diagnostics: tuple[ReadinessDiagnosticCode, ...]
    checked_at: datetime
    valid_until: datetime


class ReadinessTransport(Protocol):
    def workload_assertion(self) -> WorkloadAssertion: ...

    def send_json(self, request: DelegatedProtocolRequest) -> object: ...


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    fence: str
    expires_at: datetime
    result: ReadinessResult


class ReadinessEvaluator:
    """Resolve one discovery document without trusting its ready boolean alone."""

    def evaluate(
        self,
        document: CapabilityDiscoveryDocument,
        requirements: ReadinessRequirements,
        *,
        now: datetime,
    ) -> ReadinessResult:
        diagnostics: list[ReadinessDiagnosticCode] = []
        _check_contract(document, requirements, diagnostics)
        _check_families(document, diagnostics)
        _check_components(document, diagnostics)
        selected = _select_profiles(document, requirements, diagnostics)
        _check_resources(document, requirements, diagnostics)
        if not document.checked_at <= now < document.valid_until:
            diagnostics.append(ReadinessDiagnosticCode.DEPENDENCY_UNAVAILABLE)
        if not document.ready and not diagnostics:
            diagnostics.append(ReadinessDiagnosticCode.DEPENDENCY_UNAVAILABLE)
        unique = tuple(dict.fromkeys(diagnostics))
        return ReadinessResult(
            ready=not unique,
            selected_profiles=selected,
            diagnostics=unique,
            checked_at=document.checked_at,
            valid_until=document.valid_until,
        )


class ReadinessClient:
    """Fetch readiness with one bounded entry fenced by local revisions."""

    def __init__(
        self,
        base_url: str,
        transport: ReadinessTransport,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._base_url = normalized_base_url(base_url)
        self._transport = transport
        self._clock = clock
        self._evaluator = ReadinessEvaluator()
        self._cache: _CacheEntry | None = None

    def check(self, requirements: ReadinessRequirements) -> ReadinessResult:
        now = self._clock()
        fence = requirements.cache_fence()
        if self._cache is not None and self._cache.fence == fence:
            if now < self._cache.expires_at:
                return self._cache.result
        document = self._fetch()
        response_time = self._clock()
        result = self._evaluator.evaluate(document, requirements, now=response_time)
        expires_at = min(
            document.valid_until,
            response_time + timedelta(seconds=MAX_READINESS_CACHE_SECONDS),
        )
        self._cache = _CacheEntry(fence, expires_at, result)
        return result

    def _fetch(self) -> CapabilityDiscoveryDocument:
        request = DelegatedProtocolRequest(
            method="GET",
            url=self._base_url + "/capabilities",
            body=b"",
            extra_headers=((_ASSERTION_HEADER, self._transport.workload_assertion()),),
            retry_safety=ProtocolRetrySafety.IDEMPOTENT_PROTOCOL,
        )
        return validated(
            CapabilityDiscoveryDocument,
            self._transport.send_json(request),
        )


def _check_contract(
    document: CapabilityDiscoveryDocument,
    requirements: ReadinessRequirements,
    diagnostics: list[ReadinessDiagnosticCode],
) -> None:
    if not all(
        (
            "v1" in requirements.contract_versions,
            "v1" in document.contract_versions,
        )
    ):
        diagnostics.append(ReadinessDiagnosticCode.INCOMPATIBLE_VERSION)
    if document.profile_requirement_semantics != "ordered_any_of":
        diagnostics.append(ReadinessDiagnosticCode.INCOMPATIBLE_VERSION)


def _check_families(
    document: CapabilityDiscoveryDocument,
    diagnostics: list[ReadinessDiagnosticCode],
) -> None:
    if set(document.capabilities) != set(MANDATORY_V1_CAPABILITY_FAMILIES):
        diagnostics.append(ReadinessDiagnosticCode.V1_FAMILY_MISSING)


def _check_components(
    document: CapabilityDiscoveryDocument,
    diagnostics: list[ReadinessDiagnosticCode],
) -> None:
    family_statuses = (
        document.components.get(family) for family in MANDATORY_V1_CAPABILITY_FAMILIES
    )
    if any(item is None for item in family_statuses):
        diagnostics.append(ReadinessDiagnosticCode.V1_FAMILY_MISSING)
    statuses = {item.status for item in document.components.values()}
    if ComponentStatus.INCOMPATIBLE in statuses:
        diagnostics.append(ReadinessDiagnosticCode.INCOMPATIBLE_VERSION)
    elif statuses - {ComponentStatus.READY}:
        diagnostics.append(ReadinessDiagnosticCode.DEPENDENCY_UNAVAILABLE)


def _select_profiles(
    document: CapabilityDiscoveryDocument,
    requirements: ReadinessRequirements,
    diagnostics: list[ReadinessDiagnosticCode],
) -> dict[str, str]:
    selected: dict[str, str] = {}
    for role, acceptable in requirements.acceptable_profile_sets.items():
        profile = next(
            (
                name
                for name in acceptable
                if _component_ready(document.attestation_profile_status.get(name))
            ),
            None,
        )
        if profile is None:
            diagnostics.append(ReadinessDiagnosticCode.PROFILE_UNAVAILABLE)
        else:
            selected[role] = profile
    return selected


def _check_resources(
    document: CapabilityDiscoveryDocument,
    requirements: ReadinessRequirements,
    diagnostics: list[ReadinessDiagnosticCode],
) -> None:
    statuses = tuple(
        document.resource_registrations.get(item.resource_type)
        for item in requirements.resources
    )
    if any(
        item is not None and item.status is ComponentStatus.INCOMPATIBLE
        for item in statuses
    ):
        diagnostics.append(ReadinessDiagnosticCode.INCOMPATIBLE_VERSION)
    elif any(not _component_ready(item) for item in statuses):
        diagnostics.append(ReadinessDiagnosticCode.RESOURCE_REGISTRATION_UNAVAILABLE)


def _component_ready(component: ComponentReadiness | None) -> bool:
    return component is not None and component.status is ComponentStatus.READY


def _resource_fence(item: ResourceReadinessRequirement) -> dict[str, object]:
    return {
        "resource_type": item.resource_type,
        "descriptor_versions": sorted(item.descriptor_versions),
        "guard_versions": sorted(item.guard_versions),
        "adapter_ids": sorted(item.adapter_ids),
    }


def gated_families(
    document: CapabilityDiscoveryDocument,
) -> tuple[str, ...]:
    """Families this caller's roles do not hold the operations for.

    Derived rather than read off a reason code on purpose. ``reason_codes`` is
    a closed enum in every released client, so Core cannot introduce a value
    naming admission without making the whole document unparseable for anyone
    who has not upgraded — and the least-privileged caller, the one this
    answers for, is exactly who would hit that. ``permitted_platform_operations``
    is a new *field*, which older clients ignore harmlessly, so the precise
    answer travels there and is resolved against the published family map here.
    """

    served = document.family_platform_operations
    families = served if served else FAMILY_PLATFORM_OPERATIONS
    permitted = frozenset(document.permitted_platform_operations)
    return tuple(
        family
        for family, required in sorted(families.items())
        if not permitted.issuperset(required)
    )


__all__ = (
    "FAMILY_PLATFORM_OPERATIONS",
    "MANDATORY_V1_CAPABILITY_FAMILIES",
    "MAX_READINESS_CACHE_SECONDS",
    "CapabilityDiscoveryDocument",
    "ComponentReadiness",
    "ComponentStatus",
    "ReadinessClient",
    "ReadinessDiagnosticCode",
    "ReadinessEvaluator",
    "ReadinessRequirements",
    "ReadinessResult",
    "ResourceReadinessRequirement",
    "gated_families",
)
