"""Public lifecycle interface for scenario providers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Literal, Protocol, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from kamiwaza_sdk.validation.models import (
    CleanupEvidence,
    CleanupResult,
    FixtureMutation,
    FixtureState,
    RuntimeContext,
    ResolvedScenario,
    ScenarioDescriptor,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
    mesh_edge_target_id,
)
from kamiwaza_sdk.validation.registry import model_digest

if TYPE_CHECKING:
    from kamiwaza_sdk.validation.applicability import ApplicableTarget


class ProviderContractError(ValueError):
    """Provider input cannot resolve to a valid, complete scenario plan."""


ModelT = TypeVar("ModelT", bound=BaseModel)


def validate_provider_output(value: object, model_type: type[ModelT]) -> ModelT:
    """Round-trip an untrusted provider callback through its wire model."""

    try:
        payload = (
            value.model_dump(mode="python", by_alias=True)
            if isinstance(value, BaseModel)
            else value
        )
        return model_type.model_validate(payload)
    except ValidationError:
        raise ProviderContractError(
            f"provider returned invalid {model_type.__name__}"
        ) from None
    except Exception:
        raise ProviderContractError(
            f"provider returned unreadable {model_type.__name__}"
        ) from None


def validate_plan_identity(profile: ValidationProfile, plan: ScenarioPlan) -> None:
    """Bind a resolved plan to its exact input profile and declared targets."""

    _require_equal(
        plan.profile_digest, model_digest(profile), "plan profile digest mismatch"
    )
    target_clusters = _profile_target_clusters(profile)
    _require_target_subset(
        (item.target_id for item in plan.selected),
        set(target_clusters),
        "plan references an undeclared target",
    )
    _validate_plan_target_clusters(target_clusters, plan)
    _validate_plan_required_targets(profile, plan)


def _profile_target_clusters(profile: ValidationProfile) -> dict[str, tuple[str, ...]]:
    target_clusters: dict[str, tuple[str, ...]] = {
        cluster.id: (cluster.id,) for cluster in profile.clusters
    }
    target_clusters.update(
        {target.id: (target.cluster_id,) for target in profile.inference_targets}
    )
    target_clusters.update(
        {
            mesh_edge_target_id(edge): (edge.initiator, edge.receiver)
            for edge in profile.mesh.edges
        }
    )
    return target_clusters


def _validate_plan_target_clusters(
    target_clusters: dict[str, tuple[str, ...]], plan: ScenarioPlan
) -> None:
    for item in plan.selected:
        _require_equal(
            _selected_cluster_ids(item),
            target_clusters[item.target_id],
            "plan target cluster binding mismatch",
        )


def _selected_cluster_ids(item: ResolvedScenario) -> tuple[str, ...]:
    """Return the canonical cluster binding, including both edge endpoints."""

    return item.cluster_ids or (item.cluster_id,)


def _validate_plan_required_targets(
    profile: ValidationProfile, plan: ScenarioPlan
) -> None:
    required_targets = {
        target.id for target in profile.inference_targets if target.required
    }
    if any(
        item.target_id in required_targets and not item.required
        for item in plan.selected
    ):
        raise ProviderContractError("plan downgraded a required target")


def validate_plan_completeness(
    profile: ValidationProfile,
    descriptors: Sequence[ScenarioDescriptor],
    plan: ScenarioPlan,
) -> None:
    """Fail when an active descriptor's applicable coverage is incomplete."""

    _validate_requested_descriptors(profile, descriptors)
    selected_by_scenario = _selected_targets_by_scenario(plan)
    applicable_by_scenario = _applicable_by_scenario(profile, descriptors)
    _validate_selected_applicability(applicable_by_scenario, plan)
    _validate_cluster_requiredness(descriptors, plan)
    _validate_applicable_requiredness(applicable_by_scenario, plan)
    for descriptor in descriptors:
        _validate_descriptor_resolution(
            profile,
            descriptor,
            applicable_by_scenario[descriptor.scenario_id],
            selected_by_scenario.get(descriptor.scenario_id, set()),
        )
    _validate_selected_descriptor_requirements(profile, descriptors, plan)
    excluded = set(profile.validation.exclude) & _selected_scenarios(plan)
    if excluded:
        raise ProviderContractError("plan selected an excluded scenario")


def _validate_requested_descriptors(
    profile: ValidationProfile, descriptors: Sequence[ScenarioDescriptor]
) -> None:
    catalog_ids = {descriptor.scenario_id for descriptor in descriptors}
    if set(profile.validation.include) - catalog_ids:
        raise ProviderContractError(
            "requested scenario is absent from descriptor catalog"
        )


def _selected_scenarios(plan: ScenarioPlan) -> set[str]:
    return {item.scenario_id for item in plan.selected}


def _selected_targets_by_scenario(plan: ScenarioPlan) -> dict[str, set[str]]:
    selected: dict[str, set[str]] = {}
    for item in plan.selected:
        selected.setdefault(item.scenario_id, set()).add(item.target_id)
    return selected


def _applicable_by_scenario(
    profile: ValidationProfile, descriptors: Sequence[ScenarioDescriptor]
) -> dict[str, tuple[ApplicableTarget, ...]]:
    from kamiwaza_sdk.validation.applicability import applicable_targets

    return {
        descriptor.scenario_id: applicable_targets(profile, descriptor)
        for descriptor in descriptors
    }


def _validate_descriptor_resolution(
    profile: ValidationProfile,
    descriptor: ScenarioDescriptor,
    applicable: Sequence[ApplicableTarget],
    selected: set[str],
) -> None:
    from kamiwaza_sdk.validation.applicability import descriptor_is_active

    explicitly_included = descriptor.scenario_id in profile.validation.include
    if explicitly_included and not applicable:
        raise ProviderContractError("requested scenario is not applicable")
    if not descriptor_is_active(profile, descriptor):
        _reject_inactive_selection(selected)
        return
    if explicitly_included and not selected:
        raise ProviderContractError(
            "requested scenario resolved to zero selected cases"
        )
    _validate_required_applicable_targets(descriptor, applicable, selected)


def _reject_inactive_selection(selected: set[str]) -> None:
    if selected:
        raise ProviderContractError("plan selected an inactive scenario")


def _validate_required_applicable_targets(
    descriptor: ScenarioDescriptor,
    applicable: Sequence[ApplicableTarget],
    selected: set[str],
) -> None:
    expected = {
        item.target_id
        for item in applicable
        if descriptor.target_scope == "cluster" or item.required
    }
    if expected - selected:
        raise ProviderContractError("plan omitted a required applicable target")


def _validate_selected_applicability(
    applicable_by_scenario: Mapping[str, Sequence[ApplicableTarget]],
    plan: ScenarioPlan,
) -> None:
    target_ids_by_scenario = {
        scenario_id: {target.target_id for target in targets}
        for scenario_id, targets in applicable_by_scenario.items()
    }
    if any(
        item.target_id not in target_ids_by_scenario[item.scenario_id]
        for item in plan.selected
    ):
        raise ProviderContractError(
            "plan selected a target outside descriptor scope or applicability"
        )


def _validate_cluster_requiredness(
    descriptors: Sequence[ScenarioDescriptor], plan: ScenarioPlan
) -> None:
    cluster_scenarios = {
        descriptor.scenario_id
        for descriptor in descriptors
        if descriptor.target_scope == "cluster"
    }
    if any(
        item.scenario_id in cluster_scenarios and not item.required
        for item in plan.selected
    ):
        raise ProviderContractError("plan downgraded a cluster scenario")


def _validate_applicable_requiredness(
    applicable_by_scenario: Mapping[str, Sequence[ApplicableTarget]],
    plan: ScenarioPlan,
) -> None:
    expected = {
        (scenario_id, target.target_id): target.required
        for scenario_id, targets in applicable_by_scenario.items()
        for target in targets
    }
    if any(
        item.required != expected[(item.scenario_id, item.target_id)]
        for item in plan.selected
    ):
        raise ProviderContractError("plan changed an applicable target requiredness")


def _validate_selected_descriptor_requirements(
    profile: ValidationProfile,
    descriptors: Sequence[ScenarioDescriptor],
    plan: ScenarioPlan,
) -> None:
    selected = _selected_descriptors(descriptors, plan)
    if _has_unsupported_fixture_mode(profile, selected):
        raise ProviderContractError("selected scenario does not support fixture mode")
    required_runtime = _descriptor_runtime_requirements(selected)
    if not required_runtime <= set(plan.runtime_requirements):
        raise ProviderContractError("plan omitted a selected runtime requirement")


def _selected_descriptors(
    descriptors: Sequence[ScenarioDescriptor], plan: ScenarioPlan
) -> tuple[ScenarioDescriptor, ...]:
    registry = {descriptor.scenario_id: descriptor for descriptor in descriptors}
    selected_ids = {item.scenario_id for item in plan.selected}
    return tuple(registry[scenario_id] for scenario_id in selected_ids)


def _has_unsupported_fixture_mode(
    profile: ValidationProfile, descriptors: Sequence[ScenarioDescriptor]
) -> bool:
    return any(
        profile.validation.fixture_mode not in descriptor.fixture_modes
        for descriptor in descriptors
    )


def _descriptor_runtime_requirements(
    descriptors: Sequence[ScenarioDescriptor],
) -> set[str]:
    return {
        requirement for descriptor in descriptors for requirement in descriptor.requires
    }


def validate_plan_runtime_identity(
    plan: ScenarioPlan, runtime: RuntimeContext
) -> None:
    """Require every selected target's bound cluster in the runtime context."""

    _require_target_subset(
        (cluster_id for item in plan.selected for cluster_id in _selected_cluster_ids(item)),
        {cluster.id for cluster in runtime.clusters},
        "runtime missing a selected cluster",
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
    _journal_resource_actions(state)


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
    actions: dict[ResourceKey, CleanupAction] = {}
    for item in state.journal:
        key = (item.target_id, item.resource_type, item.resource_id)
        actions[key] = _next_resource_action(actions.get(key), item.action)
    return actions


def _next_resource_action(
    previous: CleanupAction | None, current: CleanupAction
) -> CleanupAction:
    if previous is None and current != "removed":
        return current
    if previous == "created" and current == "removed":
        return current
    raise ProviderContractError(
        "fixture journal contains an invalid ownership transition"
    )


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
    from kamiwaza_sdk.validation.fact_schema import validate_descriptor_matchers

    for descriptor in descriptors:
        validate_descriptor_matchers(descriptor)


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


def validate_fixture_state_transition(
    previous: FixtureState | None, current: FixtureState
) -> None:
    """Reject an unsafe snapshot before it can replace the recovery journal."""

    if previous is None:
        _validate_initial_snapshot(current)
        return
    _validate_snapshot_transition_identity(previous, current)
    _validate_snapshot_transition_journal(previous, current)


def _validate_initial_snapshot(current: FixtureState) -> None:
    if current.journal:
        raise ProviderContractError("prepare did not persist state before mutation")


def _validate_snapshot_transition_identity(
    previous: FixtureState, current: FixtureState
) -> None:
    if _fixture_state_identity(current) != _fixture_state_identity(previous):
        raise ProviderContractError("fixture snapshot identity changed")


def _validate_snapshot_transition_journal(
    previous: FixtureState, current: FixtureState
) -> None:
    if len(current.journal) < len(previous.journal):
        raise ProviderContractError("fixture journal snapshot regressed")
    if current.journal[: len(previous.journal)] != previous.journal:
        raise ProviderContractError("fixture journal snapshot is out of order")


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
