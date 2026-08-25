"""Public lifecycle interface for scenario providers."""

from __future__ import annotations

from typing import Protocol, Sequence

from kamiwaza_sdk.validation.models import (
    CleanupEvidence,
    FixtureState,
    RuntimeContext,
    ScenarioDescriptor,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
)


class ProviderContractError(ValueError):
    """Provider input cannot resolve to a valid, complete scenario plan."""


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
    for snapshot in snapshots:
        if snapshot.run_id != final_state.run_id:
            raise ProviderContractError("fixture snapshot run identity changed")
        if snapshot.provider_revision != final_state.provider_revision:
            raise ProviderContractError("fixture snapshot provider revision changed")
        if snapshot.owner_token_digest != final_state.owner_token_digest:
            raise ProviderContractError("fixture snapshot ownership changed")
