"""Deterministic provider used by cross-repository contract tests."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence

from kamiwaza_sdk.validation.applicability import descriptor_is_active
from kamiwaza_sdk.validation.models import (
    CaseResult,
    CleanupEvidence,
    CleanupResult,
    FactMatcher,
    FixtureMutation,
    FixtureState,
    InferenceTarget,
    ResolvedScenario,
    RuntimeContext,
    ScenarioDescriptor,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import FixtureStateWriter, ProviderContractError
from kamiwaza_sdk.validation.registry import model_digest

GOLDEN_PROVIDER_ID = "sdk.golden"
GOLDEN_PROVIDER_REVISION = "sdk.golden@v1"
GOLDEN_SCENARIO_ID = "sdk.golden.echo/v1"
GOLDEN_CASE_ID = "echo"


class GoldenProvider:
    """Small provider that exercises every protocol phase without a cluster."""

    def describe(self) -> tuple[ScenarioDescriptor, ...]:
        return (
            ScenarioDescriptor(
                scenario_id=GOLDEN_SCENARIO_ID,
                provider_id=GOLDEN_PROVIDER_ID,
                protocol_version="v1",
                target_scope="inference_target",
                minimum_level="smoke",
                capability_ids=("inference.chat",),
                applies_when=(
                    FactMatcher(
                        path=("target", "engine"),
                        operator="eq",
                        value="llamacpp",
                    ),
                ),
                requires=("cluster-api",),
                fixture_modes=("owned", "external"),
                case_ids=(GOLDEN_CASE_ID,),
            ),
        )

    def resolve(self, profile: ValidationProfile) -> ScenarioPlan:
        self._validate_requested_scenarios(profile)
        active = descriptor_is_active(profile, self.describe()[0])
        selected = (
            self._resolve_targets(
                profile.inference_targets, profile.validation.fixture_mode
            )
            if active
            else ()
        )
        if active:
            self._validate_required_targets(profile.inference_targets)
            self._validate_required_selection(profile, selected)
        return ScenarioPlan(
            schema="kamiwaza.scenario-plan/v1",
            profile_digest=model_digest(profile),
            provider_revision=GOLDEN_PROVIDER_REVISION,
            selected=selected,
            install_requirements={},
            runtime_requirements=("cluster-api",),
        )

    @classmethod
    def _resolve_targets(
        cls,
        targets: Sequence[InferenceTarget],
        fixture_mode: str,
    ) -> tuple[ResolvedScenario, ...]:
        return tuple(
            cls._resolve_target(target, fixture_mode)
            for target in targets
            if target.engine == "llamacpp"
        )

    @staticmethod
    def _validate_required_targets(targets: Sequence[InferenceTarget]) -> None:
        required_inapplicable = [
            target.id
            for target in targets
            if target.required and target.engine != "llamacpp"
        ]
        if required_inapplicable:
            raise ProviderContractError(
                f"inapplicable required target: {required_inapplicable[0]}"
            )

    @staticmethod
    def _validate_required_selection(
        profile: ValidationProfile, selected: Sequence[ResolvedScenario]
    ) -> None:
        if GOLDEN_SCENARIO_ID in profile.validation.include and not selected:
            raise ProviderContractError(
                "requested scenario resolved to zero selected cases"
            )

    def prepare(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        state_writer: FixtureStateWriter,
    ) -> FixtureState:
        self._validate_revision(plan.provider_revision)
        target_ids = {item.target_id for item in plan.selected}
        owner_digest = hashlib.sha256(runtime.run_id.encode()).hexdigest()
        state = FixtureState(
            schema="kamiwaza.fixture-state/v1",
            provider_revision=GOLDEN_PROVIDER_REVISION,
            plan_digest=model_digest(plan),
            runtime_digest=model_digest(runtime),
            run_id=runtime.run_id,
            owner_token_digest=f"sha256:{owner_digest}",
            journal=(),
            opaque={},
        )
        state_writer.write(state)
        fixture_modes = self._fixture_modes(plan)
        for index, target_id in enumerate(sorted(target_ids), start=1):
            mutation = FixtureMutation(
                sequence=index,
                target_id=target_id,
                resource_type="golden-fixture",
                resource_id=f"{runtime.run_id}:{target_id}",
                action=(
                    "adopted" if fixture_modes[target_id] == "external" else "created"
                ),
            )
            state = state.model_copy(update={"journal": (*state.journal, mutation)})
            state_writer.write(state)
        return state

    def run(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        state: FixtureState,
    ) -> ScenarioEvidence:
        self._validate_revision(plan.provider_revision)
        self._validate_state(runtime, state)
        results = tuple(
            CaseResult(
                target_id=scenario.target_id,
                scenario_id=scenario.scenario_id,
                case_id=case_id,
                status="passed",
                duration_ms=0,
                detail=None,
            )
            for scenario in plan.selected
            for case_id in scenario.case_ids
        )
        return ScenarioEvidence(
            schema="kamiwaza.scenario-evidence/v1",
            provider_revision=GOLDEN_PROVIDER_REVISION,
            profile_digest=plan.profile_digest,
            plan_digest=model_digest(plan),
            state_digest=model_digest(state),
            results=results,
            resolved_runtime={"provider": GOLDEN_PROVIDER_ID},
        )

    def teardown(self, runtime: RuntimeContext, state: FixtureState) -> CleanupEvidence:
        self._validate_state(runtime, state)
        results = tuple(
            CleanupResult(
                target_id=item.target_id,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                status=("retained_foreign" if item.action == "adopted" else "removed"),
                detail=None,
            )
            for item in reversed(state.journal)
        )
        return CleanupEvidence(
            schema="kamiwaza.cleanup-evidence/v1",
            provider_revision=GOLDEN_PROVIDER_REVISION,
            run_id=runtime.run_id,
            state_digest=model_digest(state),
            status="passed",
            results=results,
        )

    @staticmethod
    def _resolve_target(target: InferenceTarget, fixture_mode: str) -> ResolvedScenario:
        return ResolvedScenario(
            target_id=target.id,
            cluster_id=target.cluster_id,
            scenario_id=GOLDEN_SCENARIO_ID,
            required=target.required,
            case_ids=(GOLDEN_CASE_ID,),
            redacted_parameters={
                "engine": target.engine,
                "fixture_mode": fixture_mode,
            },
        )

    @staticmethod
    def _fixture_modes(plan: ScenarioPlan) -> dict[str, str]:
        modes: dict[str, str] = {}
        for item in plan.selected:
            mode = item.redacted_parameters.get("fixture_mode")
            if not isinstance(mode, str) or mode not in {"owned", "external"}:
                raise ProviderContractError("resolved target has invalid fixture mode")
            modes[item.target_id] = mode
        return modes

    @staticmethod
    def _validate_requested_scenarios(profile: ValidationProfile) -> None:
        requested = set(profile.validation.include)
        unknown = requested - {GOLDEN_SCENARIO_ID}
        if unknown:
            raise ProviderContractError(
                f"unknown requested scenario: {sorted(unknown)[0]}"
            )

    @staticmethod
    def _validate_revision(revision: str) -> None:
        if revision != GOLDEN_PROVIDER_REVISION:
            raise ProviderContractError("provider revision mismatch")

    @staticmethod
    def _validate_state(runtime: RuntimeContext, state: FixtureState) -> None:
        if state.run_id != runtime.run_id:
            raise ProviderContractError("fixture state run does not match runtime")
        if state.runtime_digest != model_digest(runtime):
            raise ProviderContractError("fixture state runtime digest mismatch")
        GoldenProvider._validate_revision(state.provider_revision)
        owner_digest = hashlib.sha256(runtime.run_id.encode()).hexdigest()
        expected = f"sha256:{owner_digest}"
        if not hmac.compare_digest(state.owner_token_digest, expected):
            raise ProviderContractError("fixture state ownership digest mismatch")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the golden provider through the standard JSON command adapter."""

    from kamiwaza_sdk.validation.cli import provider_main

    return provider_main(GoldenProvider(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
