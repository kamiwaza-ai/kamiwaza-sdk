"""Reusable behavioral contract kit for scenario-provider implementations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TypeVar

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

ResultT = TypeVar("ResultT")


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

    catalog = _describe_deterministically(provider)
    descriptors = catalog.root
    _validate_descriptor_registry(descriptors)
    plan = _resolve_deterministically(provider, profile)
    _validate_plan_registry(descriptors, plan)
    _validate_plan_identity(profile, plan)
    state = _prepare(provider, plan, runtime)
    _validate_state_identity(plan, runtime, state)
    evidence, coverage, cleanup = _run_and_cleanup(provider, plan, runtime, state)
    _validate_cleanup_identity(plan, runtime, state, cleanup)
    _require_passed(coverage.status, "provider evidence failed exact coverage")
    _require_passed(cleanup.status, "provider semantic cleanup failed")
    repeated_cleanup = _teardown(provider, runtime, state)
    _validate_cleanup_identity(plan, runtime, state, repeated_cleanup)
    _require_passed(
        repeated_cleanup.status, "provider teardown is not idempotent"
    )
    return ProviderContractResult(plan, state, evidence, coverage, cleanup)


def _describe_deterministically(provider: ScenarioProvider) -> ScenarioCatalog:
    catalog = _describe(provider)
    return _require_deterministic(catalog, lambda: _describe(provider), "describe")


def _describe(provider: ScenarioProvider) -> ScenarioCatalog:
    return validate_provider_output(tuple(provider.describe()), ScenarioCatalog)


def _resolve_deterministically(
    provider: ScenarioProvider, profile: ValidationProfile
) -> ScenarioPlan:
    plan = _resolve(provider, profile)
    return _require_deterministic(
        plan, lambda: _resolve(provider, profile), "resolve"
    )


def _resolve(
    provider: ScenarioProvider, profile: ValidationProfile
) -> ScenarioPlan:
    return validate_provider_output(provider.resolve(profile), ScenarioPlan)


def _require_deterministic(
    expected: ResultT, repeat: Callable[[], ResultT], phase: str
) -> ResultT:
    try:
        actual = repeat()
    except ProviderContractError:
        raise ProviderContractError(f"{phase} is not deterministic") from None
    if actual != expected:
        raise ProviderContractError(f"{phase} is not deterministic")
    return expected


def _prepare(
    provider: ScenarioProvider, plan: ScenarioPlan, runtime: RuntimeContext
) -> FixtureState:
    writer = RecordingFixtureStateWriter()
    state = validate_provider_output(
        provider.prepare(plan, runtime, writer), FixtureState
    )
    validate_fixture_state_snapshots(writer.snapshots, state)
    return state


def _run_and_cleanup(
    provider: ScenarioProvider,
    plan: ScenarioPlan,
    runtime: RuntimeContext,
    state: FixtureState,
) -> tuple[ScenarioEvidence, CoverageSummary, CleanupEvidence]:
    try:
        evidence = validate_provider_output(
            provider.run(plan, runtime, state), ScenarioEvidence
        )
        coverage = evaluate_coverage(plan, evidence)
    finally:
        cleanup = _teardown(provider, runtime, state)
    return evidence, coverage, cleanup


def _teardown(
    provider: ScenarioProvider, runtime: RuntimeContext, state: FixtureState
) -> CleanupEvidence:
    return validate_provider_output(
        provider.teardown(runtime, state), CleanupEvidence
    )


def _require_passed(status: str, message: str) -> None:
    if status != "passed":
        raise ProviderContractError(message)


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
    _require_equal(
        state.provider_revision,
        plan.provider_revision,
        "fixture state provider revision mismatch",
    )
    _require_equal(state.run_id, runtime.run_id, "fixture state run identity mismatch")
    _validate_target_ids(
        plan,
        (item.target_id for item in state.journal),
        "fixture state references an unplanned target",
    )


def _validate_cleanup_identity(
    plan: ScenarioPlan,
    runtime: RuntimeContext,
    state: FixtureState,
    cleanup: CleanupEvidence,
) -> None:
    _require_equal(
        cleanup.provider_revision,
        plan.provider_revision,
        "cleanup provider revision mismatch",
    )
    _require_equal(cleanup.run_id, runtime.run_id, "cleanup run identity mismatch")
    _require_equal(
        cleanup.state_digest, model_digest(state), "cleanup state digest mismatch"
    )
    _validate_target_ids(
        plan,
        (item.target_id for item in cleanup.results),
        "cleanup references an unplanned target",
    )


def _require_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise ProviderContractError(message)


def _validate_target_ids(
    plan: ScenarioPlan, target_ids: Iterable[str], message: str
) -> None:
    selected_targets = {item.target_id for item in plan.selected}
    if not set(target_ids) <= selected_targets:
        raise ProviderContractError(message)


def _validate_descriptor_registry(
    descriptors: tuple[ScenarioDescriptor, ...],
) -> None:
    if not descriptors:
        raise ProviderContractError("describe returned no scenario descriptors")
    scenario_ids = [descriptor.scenario_id for descriptor in descriptors]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ProviderContractError("describe returned a duplicate scenario ID")
