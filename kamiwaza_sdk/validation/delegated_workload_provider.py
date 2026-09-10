"""SDK-owned delegated-workload scenario provider."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from kamiwaza_sdk.validation.delegated_workload_cases import run_edge
from kamiwaza_sdk.validation.delegated_workload_spec import (
    DELEGATED_PROVIDER_REVISION,
    DelegatedPackageConfig,
    install_requirements_for,
    package_config_from_values,
    resolve_candidates,
    scenario_descriptor,
)
from kamiwaza_sdk.validation.applicability import (
    applicable_targets,
    descriptor_is_active,
)
from kamiwaza_sdk.validation.federation_provider import (
    FederationLifecycleProvider,
    _RunContext,
)
from kamiwaza_sdk.validation.federation_setup import EdgeContext, prepare_edge
from kamiwaza_sdk.validation.federation_runtime import AdminFactory, ClusterFactory
from kamiwaza_sdk.validation.models import (
    CaseResult,
    FixtureState,
    ResolvedScenario,
    RuntimeContext,
    ScenarioDescriptor,
    ScenarioPlan,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import (
    FixtureStateWriter,
    ProviderContractError,
)
from kamiwaza_sdk.validation.registry import model_digest


class DelegatedWorkloadLifecycleProvider(FederationLifecycleProvider):
    """Run the strict approved-package delegated workload edge."""

    def __init__(
        self,
        cluster_factory: ClusterFactory | None = None,
        admin_factory: AdminFactory | None = None,
        provider_revision: str = DELEGATED_PROVIDER_REVISION,
    ) -> None:
        if provider_revision != DELEGATED_PROVIDER_REVISION:
            raise ProviderContractError("delegated-workload provider revision is fixed")
        super().__init__(
            cluster_factory=cluster_factory,
            admin_factory=admin_factory,
            provider_revision=DELEGATED_PROVIDER_REVISION,
        )

    def describe(self) -> tuple[ScenarioDescriptor, ...]:
        return (scenario_descriptor(),)

    def resolve(self, profile: ValidationProfile) -> ScenarioPlan:
        descriptor = self.describe()[0]
        selected: tuple[ResolvedScenario, ...] = ()
        requirements: dict[str, Any] = {}
        if descriptor_is_active(profile, descriptor):
            if profile.validation.fixture_mode not in descriptor.fixture_modes:
                raise ProviderContractError(
                    "delegated-workload scenario does not support fixture mode"
                )
            candidates = applicable_targets(profile, descriptor)
            selected = resolve_candidates(
                profile,
                candidates,
                explicit=descriptor.scenario_id in profile.validation.include,
            )
            requirements = install_requirements_for(selected)
        return ScenarioPlan(
            schema="kamiwaza.scenario-plan/v1",
            profile_digest=model_digest(profile),
            provider_revision=DELEGATED_PROVIDER_REVISION,
            selected=selected,
            install_requirements=requirements,
            runtime_requirements=descriptor.requires,
        )

    def prepare(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        state_writer: FixtureStateWriter,
    ) -> FixtureState:
        for selected in plan.selected:
            delegated_package_config_from_selection(selected)
        return super().prepare(plan, runtime, state_writer)

    def _prepare_edge(self, context: EdgeContext) -> FixtureState:
        state = prepare_edge(context)
        config = delegated_package_config_from_selection(context.selected)
        return context.store.update_edge(
            state,
            context.selected.target_id,
            {
                "python_packages": list(config.coordinates),
                "package_imports": list(config.import_names),
                "expected_package_versions": dict(config.expected_versions),
            },
        )

    def _run_edge(self, context: _RunContext) -> list[CaseResult]:
        return run_edge(context)


def delegated_package_config_from_selection(
    selected: object,
) -> DelegatedPackageConfig:
    values = getattr(selected, "redacted_parameters", None)
    if not isinstance(values, dict):
        raise ProviderContractError("delegated selection parameters are invalid")
    return package_config_from_values(values)


def main(argv: Sequence[str] | None = None) -> int:
    from kamiwaza_sdk.validation.cli import provider_main

    return provider_main(DelegatedWorkloadLifecycleProvider(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
