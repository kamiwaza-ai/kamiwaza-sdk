"""Reusable behavioral contract kit for scenario-provider implementations."""

from __future__ import annotations

from dataclasses import dataclass, field

from kamiwaza_sdk.validation.models import (
    CleanupEvidence,
    CoverageSummary,
    FixtureState,
    RuntimeContext,
    ScenarioCatalog,
    ScenarioDescriptor,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import (
    ProviderContractError,
    ScenarioProvider,
    validate_fixture_state_snapshots,
    validate_provider_output,
)
from kamiwaza_sdk.validation.registry import evaluate_coverage, model_digest


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
        self.snapshots.append(validate_provider_output(state, FixtureState))


def exercise_provider_contract(
    provider: ScenarioProvider,
    profile: ValidationProfile,
    runtime: RuntimeContext,
) -> ProviderContractResult:
    """Exercise determinism, exact cases, lifecycle, and cleanup."""

    catalog = validate_provider_output(tuple(provider.describe()), ScenarioCatalog)
    try:
        repeated_catalog = validate_provider_output(
            tuple(provider.describe()), ScenarioCatalog
        )
    except ProviderContractError:
        raise ProviderContractError("describe is not deterministic") from None
    if catalog != repeated_catalog:
        raise ProviderContractError("describe is not deterministic")
    descriptors = catalog.root
    _validate_descriptor_registry(descriptors)
    plan = validate_provider_output(provider.resolve(profile), ScenarioPlan)
    try:
        repeated_plan = validate_provider_output(
            provider.resolve(profile), ScenarioPlan
        )
    except ProviderContractError:
        raise ProviderContractError("resolve is not deterministic") from None
    if plan != repeated_plan:
        raise ProviderContractError("resolve is not deterministic")
    _validate_plan_registry(descriptors, plan)
    _validate_plan_identity(profile, plan)
    state_writer = RecordingFixtureStateWriter()
    state = validate_provider_output(
        provider.prepare(plan, runtime, state_writer), FixtureState
    )
    validate_fixture_state_snapshots(state_writer.snapshots, state)
    _validate_state_identity(plan, runtime, state)
    evidence: ScenarioEvidence
    cleanup: CleanupEvidence
    try:
        evidence = validate_provider_output(
            provider.run(plan, runtime, state), ScenarioEvidence
        )
        coverage = evaluate_coverage(plan, evidence)
    finally:
        cleanup = validate_provider_output(
            provider.teardown(runtime, state), CleanupEvidence
        )
    _validate_cleanup_identity(plan, runtime, state, cleanup)
    if coverage.status != "passed":
        raise ProviderContractError("provider evidence failed exact coverage")
    if cleanup.status != "passed":
        raise ProviderContractError("provider semantic cleanup failed")
    repeated_cleanup = validate_provider_output(
        provider.teardown(runtime, state), CleanupEvidence
    )
    _validate_cleanup_identity(plan, runtime, state, repeated_cleanup)
    if repeated_cleanup.status != "passed":
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


def _validate_plan_identity(profile: ValidationProfile, plan: ScenarioPlan) -> None:
    if plan.profile_digest != model_digest(profile):
        raise ProviderContractError("plan profile digest mismatch")


def _validate_state_identity(
    plan: ScenarioPlan, runtime: RuntimeContext, state: FixtureState
) -> None:
    if state.provider_revision != plan.provider_revision:
        raise ProviderContractError("fixture state provider revision mismatch")
    if state.run_id != runtime.run_id:
        raise ProviderContractError("fixture state run identity mismatch")
    selected_targets = {item.target_id for item in plan.selected}
    if any(item.target_id not in selected_targets for item in state.journal):
        raise ProviderContractError("fixture state references an unplanned target")


def _validate_cleanup_identity(
    plan: ScenarioPlan,
    runtime: RuntimeContext,
    state: FixtureState,
    cleanup: CleanupEvidence,
) -> None:
    if cleanup.provider_revision != plan.provider_revision:
        raise ProviderContractError("cleanup provider revision mismatch")
    if cleanup.run_id != runtime.run_id:
        raise ProviderContractError("cleanup run identity mismatch")
    if cleanup.state_digest != model_digest(state):
        raise ProviderContractError("cleanup state digest mismatch")
    selected_targets = {item.target_id for item in plan.selected}
    if any(item.target_id not in selected_targets for item in cleanup.results):
        raise ProviderContractError("cleanup references an unplanned target")


def _validate_descriptor_registry(
    descriptors: tuple[ScenarioDescriptor, ...],
) -> None:
    if not descriptors:
        raise ProviderContractError("describe returned no scenario descriptors")
    scenario_ids = [descriptor.scenario_id for descriptor in descriptors]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ProviderContractError("describe returned a duplicate scenario ID")
