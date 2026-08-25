"""Reusable behavioral contract kit for scenario-provider implementations."""

from __future__ import annotations

from dataclasses import dataclass, field

from kamiwaza_sdk.validation.models import (
    CleanupEvidence,
    CoverageSummary,
    FixtureState,
    RuntimeContext,
    ScenarioDescriptor,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import (
    ProviderContractError,
    ScenarioProvider,
    validate_fixture_state_snapshots,
)
from kamiwaza_sdk.validation.registry import evaluate_coverage


@dataclass(frozen=True)
class ProviderContractResult:
    plan: ScenarioPlan
    state: FixtureState
    evidence: ScenarioEvidence
    coverage: CoverageSummary
    cleanup: CleanupEvidence


@dataclass
class RecordingFixtureStateWriter:
    """In-memory state sink used to verify incremental prepare journaling."""

    snapshots: list[FixtureState] = field(default_factory=list)

    def write(self, state: FixtureState) -> None:
        self.snapshots.append(state)


def exercise_provider_contract(
    provider: ScenarioProvider,
    profile: ValidationProfile,
    runtime: RuntimeContext,
) -> ProviderContractResult:
    """Exercise determinism, exact cases, lifecycle, and cleanup."""

    descriptors = tuple(provider.describe())
    if descriptors != tuple(provider.describe()):
        raise ProviderContractError("describe is not deterministic")
    _validate_descriptor_registry(descriptors)
    plan = provider.resolve(profile)
    if plan != provider.resolve(profile):
        raise ProviderContractError("resolve is not deterministic")
    _validate_plan_registry(descriptors, plan)
    state_writer = RecordingFixtureStateWriter()
    state = provider.prepare(plan, runtime, state_writer)
    validate_fixture_state_snapshots(state_writer.snapshots, state)
    evidence: ScenarioEvidence
    cleanup: CleanupEvidence
    try:
        evidence = provider.run(plan, runtime, state)
        coverage = evaluate_coverage(plan, evidence)
    finally:
        cleanup = provider.teardown(runtime, state)
    if coverage.status != "passed":
        raise ProviderContractError("provider evidence failed exact coverage")
    if cleanup.status != "passed":
        raise ProviderContractError("provider semantic cleanup failed")
    if provider.teardown(runtime, state).status != "passed":
        raise ProviderContractError("provider teardown is not idempotent")
    return ProviderContractResult(plan, state, evidence, coverage, cleanup)


def _validate_plan_registry(
    descriptors: tuple[ScenarioDescriptor, ...], plan: ScenarioPlan
) -> None:
    registry = {
        descriptor.scenario_id: set(descriptor.case_ids) for descriptor in descriptors
    }
    for selected in plan.selected:
        registered = registry.get(selected.scenario_id)
        if registered is None:
            raise ProviderContractError("plan selected an undescribed scenario")
        if not set(selected.case_ids) <= registered:
            raise ProviderContractError("plan selected an undescribed case")


def _validate_descriptor_registry(
    descriptors: tuple[ScenarioDescriptor, ...],
) -> None:
    if not descriptors:
        raise ProviderContractError("describe returned no scenario descriptors")
    scenario_ids = [descriptor.scenario_id for descriptor in descriptors]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ProviderContractError("describe returned a duplicate scenario ID")
