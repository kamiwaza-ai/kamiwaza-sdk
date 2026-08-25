"""Public lifecycle interface for scenario providers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, Protocol, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from kamiwaza_sdk.validation.models import (
    CleanupEvidence,
    CleanupResult,
    FixtureMutation,
    FixtureState,
    RuntimeContext,
    ScenarioDescriptor,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
)
from kamiwaza_sdk.validation.registry import model_digest


class ProviderContractError(ValueError):
    """Provider input cannot resolve to a valid, complete scenario plan."""


ModelT = TypeVar("ModelT", bound=BaseModel)


def validate_provider_output(value: object, model_type: type[ModelT]) -> ModelT:
    """Round-trip an untrusted provider callback through its wire model."""

    payload = (
        value.model_dump(mode="python", by_alias=True)
        if isinstance(value, BaseModel)
        else value
    )
    try:
        return model_type.model_validate(payload)
    except ValidationError as error:
        fields = {
            ".".join(str(part) for part in item["loc"])
            for item in error.errors(include_url=False, include_input=False)
        }
        locations = ", ".join(sorted(fields)) or "unknown field"
        raise ProviderContractError(
            f"provider returned invalid {model_type.__name__}: {locations}"
        ) from None


def validate_plan_identity(profile: ValidationProfile, plan: ScenarioPlan) -> None:
    """Bind a resolved plan to its exact input profile and declared targets."""

    _require_equal(
        plan.profile_digest, model_digest(profile), "plan profile digest mismatch"
    )
    declared_targets = {cluster.id for cluster in profile.clusters}
    declared_targets.update(target.id for target in profile.inference_targets)
    _require_target_subset(
        (item.target_id for item in plan.selected),
        declared_targets,
        "plan references an undeclared target",
    )


def validate_state_runtime_identity(
    runtime: RuntimeContext, state: FixtureState
) -> None:
    """Bind fixture state to the runtime that owns its cleanup lifecycle."""

    _require_equal(state.run_id, runtime.run_id, "fixture state run identity mismatch")
    _require_equal(
        state.runtime_digest,
        model_digest(runtime),
        "fixture state runtime digest mismatch",
    )


def validate_state_identity(
    plan: ScenarioPlan, runtime: RuntimeContext, state: FixtureState
) -> None:
    """Bind fixture state to its plan, runtime, and selected targets."""

    _require_equal(
        state.provider_revision,
        plan.provider_revision,
        "fixture state provider revision mismatch",
    )
    _require_equal(
        state.plan_digest, model_digest(plan), "fixture state plan digest mismatch"
    )
    validate_state_runtime_identity(runtime, state)
    _require_target_subset(
        (item.target_id for item in state.journal),
        {item.target_id for item in plan.selected},
        "fixture state references an unplanned target",
    )


def validate_evidence_identity(
    plan: ScenarioPlan, state: FixtureState, evidence: ScenarioEvidence
) -> None:
    """Bind execution evidence to the exact plan and state that produced it."""

    _require_equal(
        evidence.provider_revision,
        plan.provider_revision,
        "evidence provider revision mismatch",
    )
    _require_equal(
        evidence.profile_digest, plan.profile_digest, "evidence profile digest mismatch"
    )
    _require_equal(
        evidence.plan_digest, model_digest(plan), "evidence plan digest mismatch"
    )
    _require_equal(
        evidence.state_digest, model_digest(state), "evidence state digest mismatch"
    )
    _require_target_subset(
        (item.target_id for item in evidence.results),
        {item.target_id for item in plan.selected},
        "evidence references an unplanned target",
    )


def validate_cleanup_identity(
    runtime: RuntimeContext, state: FixtureState, cleanup: CleanupEvidence
) -> None:
    """Bind cleanup evidence to state and require complete resource reconciliation."""

    validate_state_runtime_identity(runtime, state)
    _require_equal(
        cleanup.provider_revision,
        state.provider_revision,
        "cleanup provider revision mismatch",
    )
    _require_equal(cleanup.run_id, state.run_id, "cleanup run identity mismatch")
    _require_equal(
        cleanup.state_digest, model_digest(state), "cleanup state digest mismatch"
    )
    journal_actions = _journal_resource_actions(state)
    cleanup_results = _cleanup_resource_results(cleanup)
    _require_equal(
        set(cleanup_results),
        set(journal_actions),
        "cleanup resource inventory mismatch",
    )
    _validate_cleanup_outcomes(journal_actions, cleanup_results)


ResourceKey = tuple[str, str, str]
CleanupAction = Literal["created", "adopted", "removed"]


def _journal_resource_actions(state: FixtureState) -> dict[ResourceKey, CleanupAction]:
    return {
        (item.target_id, item.resource_type, item.resource_id): item.action
        for item in state.journal
    }


def _cleanup_resource_results(
    cleanup: CleanupEvidence,
) -> dict[ResourceKey, CleanupResult]:
    keys = [
        (item.target_id, item.resource_type, item.resource_id)
        for item in cleanup.results
    ]
    if len(keys) != len(set(keys)):
        raise ProviderContractError("cleanup contains a duplicate resource result")
    return dict(zip(keys, cleanup.results, strict=True))


def _validate_cleanup_outcomes(
    actions: dict[ResourceKey, CleanupAction],
    results: dict[ResourceKey, CleanupResult],
) -> None:
    allowed = {
        "created": {"removed", "absent"},
        "adopted": {"retained_foreign", "absent"},
        "removed": {"removed", "absent"},
    }
    for key, result in results.items():
        if result.status == "failed":
            continue
        if result.status not in allowed[actions[key]]:
            raise ProviderContractError("cleanup ownership outcome mismatch")


def validate_plan_registry(
    descriptors: Sequence[ScenarioDescriptor], plan: ScenarioPlan
) -> None:
    """Require every resolved scenario and case to exist in describe output."""

    registry = {
        descriptor.scenario_id: set(descriptor.case_ids) for descriptor in descriptors
    }
    for selected in plan.selected:
        registered = registry.get(selected.scenario_id)
        if registered is None:
            raise ProviderContractError("plan selected an undescribed scenario")
        if not set(selected.case_ids) <= registered:
            raise ProviderContractError("plan selected an undescribed case")


def validate_descriptor_registry(descriptors: Sequence[ScenarioDescriptor]) -> None:
    """Require a nonempty catalog with unique scenario identifiers."""

    if not descriptors:
        raise ProviderContractError("describe returned no scenario descriptors")
    scenario_ids = [descriptor.scenario_id for descriptor in descriptors]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ProviderContractError("describe returned a duplicate scenario ID")


def require_passed(status: str, message: str) -> None:
    """Turn a semantic failure status into a provider contract failure."""

    if status != "passed":
        raise ProviderContractError(message)


def _require_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise ProviderContractError(message)


def _require_target_subset(
    target_ids: Iterable[str], allowed: set[str], message: str
) -> None:
    if not set(target_ids) <= allowed:
        raise ProviderContractError(message)


class FixtureStateWriter(Protocol):
    """Persist one ownership-guarded state snapshot durably."""

    def write(self, state: FixtureState) -> None: ...


class ScenarioProvider(Protocol):
    """JSON-serializable provider lifecycle owned by the scenario repository."""

    def describe(self) -> Sequence[ScenarioDescriptor]: ...

    def resolve(self, profile: ValidationProfile) -> ScenarioPlan: ...

    def prepare(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        state_writer: FixtureStateWriter,
    ) -> FixtureState: ...

    def run(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        state: FixtureState,
    ) -> ScenarioEvidence: ...

    def teardown(
        self, runtime: RuntimeContext, state: FixtureState
    ) -> CleanupEvidence: ...


def validate_fixture_state_snapshots(
    snapshots: Sequence[FixtureState], final_state: FixtureState
) -> None:
    """Fail unless prepare durably exposed its complete mutation journal."""

    if not snapshots:
        raise ProviderContractError("prepare did not persist fixture state")
    _validate_snapshot_bounds(snapshots, final_state)
    _validate_snapshot_journals(snapshots, final_state)
    _validate_snapshot_identity(snapshots, final_state)


def _validate_snapshot_bounds(
    snapshots: Sequence[FixtureState], final_state: FixtureState
) -> None:
    if snapshots[0].journal:
        raise ProviderContractError("prepare did not persist state before mutation")
    if snapshots[-1] != final_state:
        raise ProviderContractError("prepare did not persist its final fixture state")


def _validate_snapshot_journals(
    snapshots: Sequence[FixtureState], final_state: FixtureState
) -> None:
    actual_journals = [snapshot.journal for snapshot in snapshots]
    _validate_snapshot_prefixes(actual_journals, final_state)
    expected_journals = [
        final_state.journal[:length] for length in range(len(final_state.journal) + 1)
    ]
    for expected in expected_journals:
        if expected not in actual_journals:
            raise ProviderContractError("prepare skipped a fixture journal snapshot")


def _validate_snapshot_prefixes(
    journals: Sequence[tuple[FixtureMutation, ...]], final_state: FixtureState
) -> None:
    previous_length = 0
    for journal in journals:
        if len(journal) < previous_length:
            raise ProviderContractError("fixture journal snapshot regressed")
        if journal != final_state.journal[: len(journal)]:
            raise ProviderContractError("fixture journal snapshot is out of order")
        previous_length = len(journal)


def _validate_snapshot_identity(
    snapshots: Sequence[FixtureState], final_state: FixtureState
) -> None:
    final_identity = _fixture_state_identity(final_state)
    for snapshot in snapshots:
        if _fixture_state_identity(snapshot) != final_identity:
            raise ProviderContractError("fixture snapshot identity changed")


def _fixture_state_identity(state: FixtureState) -> tuple[str, str, str, str, str]:
    return (
        state.run_id,
        state.provider_revision,
        state.plan_digest,
        state.runtime_digest,
        state.owner_token_digest,
    )
