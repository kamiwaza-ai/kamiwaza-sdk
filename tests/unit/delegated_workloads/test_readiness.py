"""The SDK evaluates and caches only a complete compatible readiness graph."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

from kamiwaza_sdk.delegated_workloads.readiness import (
    MANDATORY_V1_CAPABILITY_FAMILIES,
    MAX_READINESS_CACHE_SECONDS,
    CapabilityDiscoveryDocument,
    ComponentStatus,
    ComponentReadiness,
    ReadinessClient,
    ReadinessDiagnosticCode,
    ReadinessEvaluator,
    ReadinessRequirements,
    ResourceReadinessRequirement,
)
from kamiwaza_sdk.delegated_workloads.proof import WorkloadAssertion
from kamiwaza_sdk.delegated_workloads.transport import (
    DelegatedProtocolRequest,
    ProtocolRetrySafety,
)


NOW = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
READY = ComponentReadiness(
    status=ComponentStatus.READY,
    reason_codes=(ReadinessDiagnosticCode.HEALTHY,),
)
UNAVAILABLE = ComponentReadiness(
    status=ComponentStatus.UNAVAILABLE,
    reason_codes=(ReadinessDiagnosticCode.DEPENDENCY_UNAVAILABLE,),
)


def _document() -> CapabilityDiscoveryDocument:
    return CapabilityDiscoveryDocument(
        contract_versions=("v1",),
        attestation_profiles=("preferred-v1", "portable-v1"),
        attestation_profile_status={"preferred-v1": READY, "portable-v1": READY},
        profile_requirement_semantics="ordered_any_of",
        resource_registrations={"example.document": READY},
        capabilities=MANDATORY_V1_CAPABILITY_FAMILIES,
        components={family: READY for family in MANDATORY_V1_CAPABILITY_FAMILIES},
        checked_at=NOW,
        valid_until=NOW + timedelta(minutes=5),
        ready=True,
    )


def _requirements(**changes: object) -> ReadinessRequirements:
    values: dict[str, object] = {
        "workload_revision_id": "workload-revision-1",
        "contract_versions": frozenset({"v1"}),
        "acceptable_profile_sets": {"executor": ("preferred-v1", "portable-v1")},
        "resources": (
            ResourceReadinessRequirement(
                resource_type="example.document",
                descriptor_versions=frozenset({"v1"}),
                guard_versions=frozenset({"v1"}),
                adapter_ids=frozenset({"policy-v1", "quota-v1"}),
            ),
        ),
    }
    values.update(changes)
    return ReadinessRequirements(**values)  # type: ignore[arg-type]


def _evaluate(document: CapabilityDiscoveryDocument):
    return ReadinessEvaluator().evaluate(document, _requirements(), now=NOW)


def test_compatible_complete_graph_is_ready() -> None:
    result = _evaluate(_document())

    assert result.ready is True
    assert result.selected_profiles == {"executor": "preferred-v1"}
    assert result.diagnostics == ()


def test_each_missing_v1_family_blocks_readiness() -> None:
    for missing in MANDATORY_V1_CAPABILITY_FAMILIES:
        capabilities = tuple(
            family for family in MANDATORY_V1_CAPABILITY_FAMILIES if family != missing
        )
        result = _evaluate(_document().model_copy(update={"capabilities": capabilities}))

        assert result.ready is False
        assert ReadinessDiagnosticCode.V1_FAMILY_MISSING in result.diagnostics


def test_incompatible_protocol_or_family_status_blocks_readiness() -> None:
    incompatible = _document().model_copy(update={"contract_versions": ("v2",)})
    components = dict(_document().components)
    components["durable_audit"] = UNAVAILABLE

    assert ReadinessDiagnosticCode.INCOMPATIBLE_VERSION in _evaluate(
        incompatible
    ).diagnostics
    assert ReadinessDiagnosticCode.DEPENDENCY_UNAVAILABLE in _evaluate(
        _document().model_copy(update={"components": components})
    ).diagnostics

    components["durable_audit"] = ComponentReadiness(
        status=ComponentStatus.INCOMPATIBLE,
        reason_codes=(ReadinessDiagnosticCode.INCOMPATIBLE_VERSION,),
    )
    assert ReadinessDiagnosticCode.INCOMPATIBLE_VERSION in _evaluate(
        _document().model_copy(update={"components": components})
    ).diagnostics


def test_optional_profile_loss_selects_the_first_healthy_fallback() -> None:
    profiles = {"preferred-v1": UNAVAILABLE, "portable-v1": READY}
    result = _evaluate(_document().model_copy(update={"attestation_profile_status": profiles}))

    assert result.ready is True
    assert result.selected_profiles == {"executor": "portable-v1"}
    assert result.diagnostics == ()


def test_missing_required_resource_blocks_with_safe_diagnostics() -> None:
    document = _document().model_copy(
        update={"resource_registrations": {"secret-token-from-server": UNAVAILABLE}},
    )
    result = _evaluate(document)

    assert result.ready is False
    assert result.diagnostics == (
        ReadinessDiagnosticCode.RESOURCE_REGISTRATION_UNAVAILABLE,
    )
    assert "secret-token-from-server" not in repr(result)


class _Transport:
    def __init__(self, responses: list[CapabilityDiscoveryDocument]) -> None:
        self.responses = responses
        self.requests: list[object] = []

    def workload_assertion(self) -> WorkloadAssertion:
        return WorkloadAssertion("fresh-assertion")

    def send_json(self, request: object) -> object:
        self.requests.append(request)
        return self.responses[min(len(self.requests) - 1, len(self.responses) - 1)].model_dump(
            mode="json"
        )


def test_cache_is_bounded_by_sdk_ceiling_and_server_validity() -> None:
    clock = [NOW]
    transport = _Transport([_document()])
    client = ReadinessClient(
        "https://core.example.test/api/v1/delegated-workloads",
        transport,
        clock=lambda: clock[0],
    )

    assert client.check(_requirements()).ready is True
    clock[0] += timedelta(seconds=MAX_READINESS_CACHE_SECONDS - 1)
    assert client.check(_requirements()).ready is True
    assert len(transport.requests) == 1

    clock[0] += timedelta(seconds=2)
    assert client.check(_requirements()).ready is True
    assert len(transport.requests) == 2
    request = cast(DelegatedProtocolRequest, transport.requests[0])
    assert (request.method, request.body) == ("GET", b"")
    assert request.url.endswith("/capabilities")
    assert request.retry_safety is ProtocolRetrySafety.IDEMPOTENT_PROTOCOL
    assert "fresh-assertion" not in repr(request)

    short_document = _document().model_copy(
        update={"valid_until": NOW + timedelta(seconds=5)}
    )
    short_transport = _Transport([short_document])
    short_clock = [NOW]
    short_client = ReadinessClient(
        "https://core.example.test/api/v1/delegated-workloads",
        short_transport,
        clock=lambda: short_clock[0],
    )
    short_client.check(_requirements())
    short_clock[0] += timedelta(seconds=6)
    short_client.check(_requirements())
    assert len(short_transport.requests) == 2


def test_freshness_uses_response_time_after_the_discovery_request() -> None:
    clock = [NOW]

    class _DelayedTransport(_Transport):
        def send_json(self, request: object) -> object:
            clock[0] += timedelta(seconds=5)
            document = _document().model_copy(
                update={
                    "checked_at": clock[0],
                    "valid_until": clock[0] + timedelta(minutes=1),
                }
            )
            self.responses = [document]
            return super().send_json(request)

    client = ReadinessClient(
        "https://core.example.test/api/v1/delegated-workloads",
        _DelayedTransport([]),
        clock=lambda: clock[0],
    )

    result = client.check(_requirements())

    assert result.ready is True
    assert result.diagnostics == ()


def test_readiness_types_are_exported_from_the_neutral_package() -> None:
    import kamiwaza_sdk.delegated_workloads as delegated

    assert delegated.ReadinessClient is ReadinessClient
    assert delegated.ReadinessRequirements is ReadinessRequirements


def test_workload_and_descriptor_revision_changes_fence_the_cache() -> None:
    transport = _Transport([_document()])
    client = ReadinessClient(
        "https://core.example.test/api/v1/delegated-workloads",
        transport,
        clock=lambda: NOW,
    )
    base = _requirements()
    changed_workload = _requirements(workload_revision_id="workload-revision-2")
    changed_resource = _requirements(
        resources=(
            ResourceReadinessRequirement(
                resource_type="example.document",
                descriptor_versions=frozenset({"v2"}),
                guard_versions=base.resources[0].guard_versions,
                adapter_ids=base.resources[0].adapter_ids,
            ),
        )
    )

    client.check(base)
    client.check(changed_workload)
    client.check(changed_resource)

    assert len(transport.requests) == 3
