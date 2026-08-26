"""SDK-owned shared-IdP federation provider with remote model validation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from kamiwaza_sdk.validation.applicability import (
    applicable_targets,
    descriptor_is_active,
)
from kamiwaza_sdk.validation.federation_common import (
    edge_state,
    read_persona_password,
    resource_map,
    required_text,
    selected_endpoints,
)
from kamiwaza_sdk.validation.federation_provider import (
    FederationLifecycleProvider,
    _RunContext,
    _close,
)
from kamiwaza_sdk.validation.federation_setup import (
    EdgeContext,
    _bind_edge,
    _configure_edge,
    _seed_brokered_users,
)
from kamiwaza_sdk.validation.federation_state import validate_state
from kamiwaza_sdk.validation.inference_provider import _select_model
from kamiwaza_sdk.validation.inference_runtime import DeploymentRequest
from kamiwaza_sdk.validation.inference_spec import TargetParameters
from kamiwaza_sdk.validation.model_mesh_cases import run_edge
from kamiwaza_sdk.validation.model_mesh_spec import (
    MODEL_MESH_PROVIDER_REVISION,
    model_target_parameters,
    resolve_candidates,
    scenario_descriptor,
)
from kamiwaza_sdk.validation.models import (
    FixtureState,
    ResolvedScenario,
    RuntimeContext,
    ScenarioDescriptor,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import (
    ProviderContractError,
    validate_plan_runtime_identity,
)
from kamiwaza_sdk.validation.registry import model_digest


@dataclass(frozen=True)
class _DeploymentCapture:
    config: Any
    deployment_id: str
    served_model_id: str
    ready: Any
    observation: Any


class ModelMeshLifecycleProvider(FederationLifecycleProvider):
    """Run shared-IdP setup plus an exact receiver model-mesh inventory."""

    def __init__(
        self,
        cluster_factory: Any = None,
        admin_factory: Any = None,
        *,
        inference_factory: Any = None,
        provider_revision: str = MODEL_MESH_PROVIDER_REVISION,
    ) -> None:
        if provider_revision != MODEL_MESH_PROVIDER_REVISION:
            raise ProviderContractError("model-mesh provider revision is fixed")
        super().__init__(
            cluster_factory=cluster_factory,
            admin_factory=admin_factory,
            provider_revision=MODEL_MESH_PROVIDER_REVISION,
        )
        if inference_factory is None:
            from kamiwaza_sdk.validation.sdk_inference_runtime import (
                SdkInferenceClusterFactory,
            )

            inference_factory = SdkInferenceClusterFactory()
        self._inference_factory = inference_factory

    def describe(self) -> tuple[ScenarioDescriptor, ...]:
        return (scenario_descriptor(),)

    def resolve(self, profile: ValidationProfile) -> ScenarioPlan:
        descriptor = self.describe()[0]
        selected: tuple[ResolvedScenario, ...] = ()
        requirements: dict[str, Any] = {}
        if descriptor_is_active(profile, descriptor):
            if profile.validation.fixture_mode not in descriptor.fixture_modes:
                raise ProviderContractError(
                    "model-mesh scenario does not support fixture mode"
                )
            candidates = applicable_targets(profile, descriptor)
            selected = resolve_candidates(
                profile,
                candidates,
                explicit=descriptor.scenario_id in profile.validation.include,
            )
            issuers = sorted(
                {
                    str(item.redacted_parameters["issuer"])
                    for item in selected
                    if item.redacted_parameters.get("issuer")
                }
            )
            requirements = {
                "scheduler": {"trustedSharedIssuers": issuers},
                "modelTargets": sorted(
                    {
                        str(item.redacted_parameters["model_target_id"])
                        for item in selected
                    }
                ),
            }
        return ScenarioPlan(
            schema="kamiwaza.scenario-plan/v1",
            profile_digest=model_digest(profile),
            provider_revision=MODEL_MESH_PROVIDER_REVISION,
            selected=selected,
            install_requirements=requirements,
            runtime_requirements=descriptor.requires,
        )

    def _prepare_edge(self, context: EdgeContext) -> FixtureState:
        # Bind and configure the edge first, then provision the model so the
        # model UUID can be included in the receiver allowlist's initial tuple.
        _bind_edge(context)
        _configure_edge(context, include_dataset_fixture=False)
        target = model_target_parameters(context.selected)
        receiver_runtime = context.runtime_clusters[context.receiver_id]
        inference = self._inference_factory(receiver_runtime)
        try:
            model_id_for_grant = _deploy_model(context, target, inference)
        finally:
            _close(inference)
        _seed_brokered_users(context, model_id=model_id_for_grant)
        return context.state

    def run(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        state: FixtureState,
    ) -> ScenarioEvidence:
        self._validate_revision(plan.provider_revision)
        validate_plan_runtime_identity(plan, runtime)
        validate_state(runtime, state, MODEL_MESH_PROVIDER_REVISION)
        if not plan.selected:
            return ScenarioEvidence(
                schema="kamiwaza.scenario-evidence/v1",
                provider_revision=MODEL_MESH_PROVIDER_REVISION,
                profile_digest=plan.profile_digest,
                plan_digest=model_digest(plan),
                state_digest=model_digest(state),
                results=(),
                resolved_runtime={},
            )
        runtime_clusters = {item.id: item for item in runtime.clusters}
        clusters = self._open_clusters(runtime_clusters, plan)
        results = []
        resolved: dict[str, Any] = {}
        try:
            for selected in plan.selected:
                edge = edge_state(state, selected.target_id)
                params = resource_map(edge)
                initiator_id, receiver_id = selected_endpoints(selected)
                admin = self._admin_factory(runtime, runtime_clusters[receiver_id])
                results.extend(
                    run_edge(
                        _RunContext(
                            selected=selected,
                            params=params,
                            initiator=clusters[initiator_id].client,
                            receiver=clusters[receiver_id].client,
                            admin=admin,
                            password=read_persona_password(runtime),
                            initiator_base=runtime_clusters[initiator_id].base_url,
                        )
                    )
                )
                resolved[selected.target_id] = {
                    key: params.get(key)
                    for key in (
                        "initiator_cluster_id",
                        "receiver_cluster_id",
                        "model_target_id",
                        "model_id",
                        "deployment_id",
                        "served_model_id",
                        "actual_engine",
                        "actual_image_digest",
                    )
                }
        finally:
            for cluster in clusters.values():
                _close(cluster)
        return ScenarioEvidence(
            schema="kamiwaza.scenario-evidence/v1",
            provider_revision=MODEL_MESH_PROVIDER_REVISION,
            profile_digest=plan.profile_digest,
            plan_digest=model_digest(plan),
            state_digest=model_digest(state),
            results=tuple(results),
            resolved_runtime=resolved,
        )


def _mutation(target_id: str, resource_type: str, resource_id: str) -> Any:
    from kamiwaza_sdk.validation.federation_state import MutationSpec

    return MutationSpec(target_id, resource_type, resource_id)


def _deploy_model(context: EdgeContext, target: dict[str, Any], inference: Any) -> str:
    """Download, deploy, observe, and journal the selected receiver model."""

    deployment_id: str | None = None
    try:
        catalog = inference.ensure_download(
            required_text(target, "model_repository"),
            required_text(target, "model_quantization"),
        )
        model_id = str(catalog.model_id or "").strip()
        if not model_id:
            raise ProviderContractError("model target discovery returned no model id")
        config = _select_model(
            catalog,
            inference.list_configs(model_id),
            TargetParameters(
                repository=required_text(target, "model_repository"),
                engine=required_text(target, "model_engine"),
                model_format=required_text(target, "model_format"),
                quantization=required_text(target, "model_quantization"),
                runtime_profile=required_text(target, "model_runtime_profile"),
                expected_image=target.get("model_expected_image"),
                accelerators=tuple(),
            ),
        )
        engine = required_text(target, "model_engine")
        deployment_id = inference.deploy(
            DeploymentRequest(
                model_id=config.model_id,
                config_id=config.config_id,
                model_file_id=config.model_file_id,
                engine=engine,
                runtime_profile=required_text(target, "model_runtime_profile"),
            )
        )
        ready = inference.wait_ready(deployment_id)
        _assert_ready(ready, engine)
        observation = inference.observe_runtime(deployment_id, engine)
        served_model_id = _served_model_id(inference, deployment_id)
        _record_deployment(
            context,
            target,
            _DeploymentCapture(
                config, deployment_id, served_model_id, ready, observation
            ),
        )
        return model_id
    except Exception:
        _stop_failed_deployment(inference, deployment_id)
        raise


def _assert_ready(ready: Any, engine: str) -> None:
    if ready.engine != engine:
        raise ProviderContractError("model-mesh deployment used an unexpected engine")
    if ready.instance_count < 1:
        raise ProviderContractError("model-mesh deployment did not become ready")


def _record_deployment(
    context: EdgeContext,
    target: dict[str, Any],
    capture: _DeploymentCapture,
) -> None:
    model_target_id = required_text(target, "model_target_id")
    resource = {
        "model_id": capture.config.model_id,
        "model_config_id": capture.config.config_id,
        "model_file_id": capture.config.model_file_id,
        "deployment_id": capture.deployment_id,
        "served_model_id": capture.served_model_id,
        "model_target_id": model_target_id,
        "actual_engine": capture.ready.engine,
        "actual_image_digest": capture.observation.image_digest,
        "effective_runtime_args": list(capture.observation.effective_args),
    }
    context.state = context.store.record(
        context.state,
        _mutation(
            context.selected.target_id, "model-deployment", capture.deployment_id
        ),
        resource,
    )
    context.state = context.store.update_edge(
        context.state,
        context.selected.target_id,
        {
            "model_id": capture.config.model_id,
            "model_config_id": capture.config.config_id,
            "model_file_id": capture.config.model_file_id,
            "deployment_id": capture.deployment_id,
            "served_model_id": capture.served_model_id,
            "model_target_id": model_target_id,
        },
    )


def _stop_failed_deployment(inference: Any, deployment_id: str | None) -> None:
    if not deployment_id:
        return
    try:
        inference.stop(deployment_id)
    except Exception:
        pass


def _served_model_id(inference: Any, deployment_id: str) -> str:
    client = getattr(inference, "_client", None)
    if client is None:
        return _fallback_served_model_id(inference, deployment_id)
    openai_client = client.openai.get_client(deployment_id=deployment_id)
    try:
        served_model_id = _first_served_model_id(openai_client.models.list())
    finally:
        openai_client.close()
    if served_model_id:
        return served_model_id
    raise ProviderContractError("model-mesh runtime did not expose a served model id")


def _fallback_served_model_id(inference: Any, deployment_id: str) -> str:
    method = getattr(inference, "served_model_id", None)
    if callable(method):
        value = method(deployment_id)
        if value:
            return str(value)
    raise ProviderContractError("model-mesh runtime did not expose a served model id")


def _first_served_model_id(models: Any) -> str | None:
    for item in getattr(models, "data", None) or []:
        value = str(getattr(item, "id", "") or "").strip()
        if value:
            return value
    return None


def main(argv: Sequence[str] | None = None) -> int:
    from kamiwaza_sdk.validation.cli import provider_main

    return provider_main(ModelMeshLifecycleProvider(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
