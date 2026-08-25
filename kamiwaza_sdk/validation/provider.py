"""Public lifecycle interface for scenario providers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from kamiwaza_sdk.validation.models import (
    CleanupEvidence,
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


def validate_evidence_identity(plan: ScenarioPlan, evidence: ScenarioEvidence) -> None:
    """Bind execution evidence to the exact plan that produced it."""

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
    journal_resources = {
        (item.target_id, item.resource_type, item.resource_id) for item in state.journal
    }
    cleanup_resources = {
        (item.target_id, item.resource_type, item.resource_id)
        for item in cleanup.results
    }
    _require_equal(
        cleanup_resources,
        journal_resources,
        "cleanup resource inventory mismatch",
    )


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
    expected_journals = [
        final_state.journal[:length] for length in range(len(final_state.journal) + 1)
    ]
    actual_journals = [snapshot.journal for snapshot in snapshots]
    for expected in expected_journals:
        if expected not in actual_journals:
            raise ProviderContractError("prepare skipped a fixture journal snapshot")


def _validate_snapshot_identity(
    snapshots: Sequence[FixtureState], final_state: FixtureState
) -> None:
    final_identity = _fixture_state_identity(final_state)
    for snapshot in snapshots:
        if _fixture_state_identity(snapshot) != final_identity:
            raise ProviderContractError("fixture snapshot identity changed")


def _fixture_state_identity(state: FixtureState) -> tuple[str, str, str, str]:
    return (
        state.run_id,
        state.provider_revision,
        state.plan_digest,
        state.owner_token_digest,
    )
