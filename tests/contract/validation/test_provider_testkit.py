"""Reusable provider test kit exercised by the golden provider."""

from __future__ import annotations

import pytest

from kamiwaza_sdk.validation import RuntimeContext, ValidationProfile
from kamiwaza_sdk.validation.golden_provider import GoldenProvider
from kamiwaza_sdk.validation.provider import (
    ProviderContractError,
    validate_cleanup_identity,
    validate_fixture_state_snapshots,
    validate_state_identity,
)
from kamiwaza_sdk.validation.testkit import (
    RecordingFixtureStateWriter,
    exercise_provider_contract,
)

from .support import profile_payload

pytestmark = pytest.mark.contract


def _profile(fixture_mode: str = "owned") -> ValidationProfile:
    payload = profile_payload()
    payload["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    payload["validation"]["fixture_mode"] = fixture_mode  # type: ignore[index]
    return ValidationProfile.model_validate(payload)


def _runtime() -> RuntimeContext:
    return RuntimeContext.model_validate(
        {
            "schema": "kamiwaza.runtime-context/v1",
            "run_id": "contract-run",
            "clusters": [
                {
                    "id": "evo-x2-2",
                    "base_url": "https://evo-x2-2.example.test/api",
                    "api_key_ref": "secret://evo-x2-2/admin-pat",
                    "kubeconfig_ref": "file:///run/secrets/evo-x2-2.kubeconfig",
                }
            ],
        }
    )


def test_golden_provider_passes_the_shared_contract_kit() -> None:
    result = exercise_provider_contract(GoldenProvider(), _profile(), _runtime())

    assert result.coverage.status == "passed"
    assert result.cleanup.status == "passed"
    assert result.plan.provider_revision == "sdk.golden@v1"


def test_golden_provider_snapshots_state_before_and_after_each_mutation() -> None:
    provider = GoldenProvider()
    writer = RecordingFixtureStateWriter()

    state = provider.prepare(provider.resolve(_profile()), _runtime(), writer)

    assert [len(snapshot.journal) for snapshot in writer.snapshots] == [0, 1]
    assert writer.snapshots[-1] == state


class NondeterministicGoldenProvider(GoldenProvider):
    calls = 0

    def describe(self):  # type: ignore[no-untyped-def]
        self.calls += 1
        descriptors = super().describe()
        if self.calls == 1:
            return descriptors
        return ()


def test_contract_kit_rejects_nondeterministic_describe() -> None:
    with pytest.raises(ProviderContractError, match="describe is not deterministic"):
        exercise_provider_contract(
            NondeterministicGoldenProvider(), _profile(), _runtime()
        )


class UndescribedCaseGoldenProvider(GoldenProvider):
    def resolve(self, profile):  # type: ignore[no-untyped-def]
        plan = super().resolve(profile)
        selected = plan.selected[0].model_copy(update={"case_ids": ("other",)})
        return plan.model_copy(update={"selected": (selected,)})


class WrongProfileDigestGoldenProvider(GoldenProvider):
    def resolve(self, profile):  # type: ignore[no-untyped-def]
        return (
            super()
            .resolve(profile)
            .model_copy(update={"profile_digest": "sha256:" + "0" * 64})
        )


class ForeignPlanTargetGoldenProvider(GoldenProvider):
    def resolve(self, profile):  # type: ignore[no-untyped-def]
        plan = super().resolve(profile)
        selected = plan.selected[0].model_copy(update={"target_id": "foreign-target"})
        return plan.model_copy(update={"selected": (selected,)})


class UnplannedStateTargetGoldenProvider(GoldenProvider):
    def prepare(self, plan, runtime, state_writer):  # type: ignore[no-untyped-def]
        state = super().prepare(plan, runtime, RecordingFixtureStateWriter())
        mutation = state.journal[0].model_copy(update={"target_id": "foreign-target"})
        changed = state.model_copy(update={"journal": (mutation,)})
        state_writer.write(changed.model_copy(update={"journal": ()}))
        state_writer.write(changed)
        return changed


class WrongStatePlanDigestGoldenProvider(GoldenProvider):
    def prepare(self, plan, runtime, state_writer):  # type: ignore[no-untyped-def]
        state = super().prepare(plan, runtime, RecordingFixtureStateWriter())
        changed = state.model_copy(update={"plan_digest": "sha256:" + "0" * 64})
        state_writer.write(changed.model_copy(update={"journal": ()}))
        state_writer.write(changed)
        return changed


class WrongCleanupDigestGoldenProvider(GoldenProvider):
    def teardown(self, runtime, state):  # type: ignore[no-untyped-def]
        return (
            super()
            .teardown(runtime, state)
            .model_copy(update={"state_digest": "sha256:" + "0" * 64})
        )


class WrongEvidenceStateDigestGoldenProvider(GoldenProvider):
    def run(self, plan, runtime, state):  # type: ignore[no-untyped-def]
        return (
            super()
            .run(plan, runtime, state)
            .model_copy(update={"state_digest": "sha256:" + "0" * 64})
        )


class MissingCleanupResultGoldenProvider(GoldenProvider):
    def teardown(self, runtime, state):  # type: ignore[no-untyped-def]
        return super().teardown(runtime, state).model_copy(update={"results": ()})


class DuplicateCleanupResultGoldenProvider(GoldenProvider):
    def teardown(self, runtime, state):  # type: ignore[no-untyped-def]
        cleanup = super().teardown(runtime, state)
        return cleanup.model_copy(update={"results": cleanup.results * 2})


class RetainedCreatedResourceGoldenProvider(GoldenProvider):
    def teardown(self, runtime, state):  # type: ignore[no-untyped-def]
        cleanup = super().teardown(runtime, state)
        retained = cleanup.results[0].model_copy(update={"status": "retained_foreign"})
        return cleanup.model_copy(update={"results": (retained,)})


class RemovedAdoptedResourceGoldenProvider(GoldenProvider):
    def teardown(self, runtime, state):  # type: ignore[no-untyped-def]
        cleanup = super().teardown(runtime, state)
        removed = cleanup.results[0].model_copy(update={"status": "removed"})
        return cleanup.model_copy(update={"results": (removed,)})


def test_contract_kit_rejects_cases_absent_from_descriptor_registry() -> None:
    with pytest.raises(ProviderContractError, match="undescribed case"):
        exercise_provider_contract(
            UndescribedCaseGoldenProvider(), _profile(), _runtime()
        )


def test_contract_kit_binds_plan_to_input_profile() -> None:
    with pytest.raises(ProviderContractError, match="plan profile digest mismatch"):
        exercise_provider_contract(
            WrongProfileDigestGoldenProvider(), _profile(), _runtime()
        )


def test_contract_kit_rejects_plan_target_absent_from_profile() -> None:
    with pytest.raises(ProviderContractError, match="undeclared target"):
        exercise_provider_contract(
            ForeignPlanTargetGoldenProvider(), _profile(), _runtime()
        )


def test_contract_kit_binds_fixture_mutations_to_selected_targets() -> None:
    with pytest.raises(ProviderContractError, match="unplanned target"):
        exercise_provider_contract(
            UnplannedStateTargetGoldenProvider(), _profile(), _runtime()
        )


def test_contract_kit_binds_fixture_state_to_exact_plan() -> None:
    with pytest.raises(
        ProviderContractError, match="fixture state plan digest mismatch"
    ):
        exercise_provider_contract(
            WrongStatePlanDigestGoldenProvider(), _profile(), _runtime()
        )


def test_contract_kit_binds_cleanup_to_runtime_plan_and_state() -> None:
    with pytest.raises(ProviderContractError, match="cleanup state digest mismatch"):
        exercise_provider_contract(
            WrongCleanupDigestGoldenProvider(), _profile(), _runtime()
        )


def test_contract_kit_binds_evidence_to_exact_fixture_state() -> None:
    with pytest.raises(ProviderContractError, match="evidence state digest mismatch"):
        exercise_provider_contract(
            WrongEvidenceStateDigestGoldenProvider(), _profile(), _runtime()
        )


def test_contract_kit_requires_cleanup_for_every_journaled_resource() -> None:
    with pytest.raises(ProviderContractError, match="resource inventory mismatch"):
        exercise_provider_contract(
            MissingCleanupResultGoldenProvider(), _profile(), _runtime()
        )


@pytest.mark.parametrize(
    ("provider", "fixture_mode"),
    [
        (RetainedCreatedResourceGoldenProvider(), "owned"),
        (RemovedAdoptedResourceGoldenProvider(), "external"),
    ],
)
def test_contract_kit_rejects_cleanup_that_violates_fixture_ownership(
    provider: GoldenProvider, fixture_mode: str
) -> None:
    with pytest.raises(ProviderContractError, match="cleanup ownership outcome"):
        exercise_provider_contract(provider, _profile(fixture_mode), _runtime())


def test_contract_kit_rejects_duplicate_cleanup_resource_evidence() -> None:
    with pytest.raises(ProviderContractError, match="duplicate resource"):
        exercise_provider_contract(
            DuplicateCleanupResultGoldenProvider(), _profile(), _runtime()
        )


def test_fixture_state_is_bound_to_exact_runtime_content() -> None:
    provider = GoldenProvider()
    plan = provider.resolve(_profile())
    runtime = _runtime()
    state = provider.prepare(plan, runtime, RecordingFixtureStateWriter())
    changed_cluster = runtime.clusters[0].model_copy(
        update={"base_url": "https://other.example.test/api"}
    )
    changed_runtime = runtime.model_copy(update={"clusters": (changed_cluster,)})

    with pytest.raises(ProviderContractError, match="runtime digest mismatch"):
        validate_state_identity(plan, changed_runtime, state)


def test_fixture_snapshot_journal_cannot_regress() -> None:
    provider = GoldenProvider()
    writer = RecordingFixtureStateWriter()
    state = provider.prepare(provider.resolve(_profile()), _runtime(), writer)
    initial = writer.snapshots[0]

    with pytest.raises(ProviderContractError, match="journal snapshot regressed"):
        validate_fixture_state_snapshots(
            (initial, state, initial, state),
            state,
        )


def test_cleanup_identity_rejects_duplicate_results_directly() -> None:
    provider = GoldenProvider()
    runtime = _runtime()
    plan = provider.resolve(_profile())
    state = provider.prepare(plan, runtime, RecordingFixtureStateWriter())
    cleanup = provider.teardown(runtime, state)

    with pytest.raises(ProviderContractError, match="duplicate resource"):
        validate_cleanup_identity(
            runtime,
            state,
            cleanup.model_copy(update={"results": cleanup.results * 2}),
        )


def test_golden_provider_refuses_tampered_fixture_ownership() -> None:
    provider = GoldenProvider()
    plan = provider.resolve(_profile())
    runtime = _runtime()
    state = provider.prepare(plan, runtime, RecordingFixtureStateWriter()).model_copy(
        update={"owner_token_digest": "sha256:" + "0" * 64}
    )

    with pytest.raises(ProviderContractError, match="ownership digest mismatch"):
        provider.teardown(runtime, state)
