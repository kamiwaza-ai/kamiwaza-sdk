"""Reusable behavioral contract kit for scenario-provider implementations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from kamiwaza_sdk.validation.models import (
    CleanupEvidence,
    CoverageSummary,
    FixtureState,
    RuntimeContext,
    ScenarioCatalog,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import (
    ProviderContractError,
    ScenarioProvider,
    require_passed,
    validate_cleanup_identity,
    validate_descriptor_registry,
    validate_evidence_identity,
    validate_fixture_state_snapshots,
    validate_plan_identity,
    validate_plan_registry,
    validate_provider_output,
    validate_state_identity,
)
from kamiwaza_sdk.validation.registry import evaluate_coverage

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
    validate_descriptor_registry(descriptors)
    plan = _resolve_deterministically(provider, profile)
    validate_plan_registry(descriptors, plan)
    validate_plan_identity(profile, plan)
    state = _prepare(provider, plan, runtime)
    validate_state_identity(plan, runtime, state)
    evidence, coverage, cleanup = _run_and_cleanup(provider, plan, runtime, state)
    validate_evidence_identity(plan, state, evidence)
    validate_cleanup_identity(runtime, state, cleanup)
    require_passed(coverage.status, "provider evidence failed exact coverage")
    require_passed(cleanup.status, "provider semantic cleanup failed")
    repeated_cleanup = _teardown(provider, runtime, state)
    validate_cleanup_identity(runtime, state, repeated_cleanup)
    require_passed(repeated_cleanup.status, "provider teardown is not idempotent")
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
    return _require_deterministic(plan, lambda: _resolve(provider, profile), "resolve")


def _resolve(provider: ScenarioProvider, profile: ValidationProfile) -> ScenarioPlan:
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
    return validate_provider_output(provider.teardown(runtime, state), CleanupEvidence)
