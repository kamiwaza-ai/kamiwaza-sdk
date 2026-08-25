"""Strict SDK-owned local model lifecycle scenario provider."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import JsonValue

from kamiwaza_sdk.utils.quant_manager import QuantizationManager
from kamiwaza_sdk.validation.applicability import (
    applicable_targets,
    descriptor_is_active,
)
from kamiwaza_sdk.validation.inference_runtime import (
    CatalogConfig,
    CatalogFile,
    CatalogModel,
    DeploymentRequest,
    InferenceCluster,
    InferenceClusterFactory,
    SelectedModel,
)
from kamiwaza_sdk.validation.inference_spec import (
    INFERENCE_CASE_IDS,
    INFERENCE_PROVIDER_ID as INFERENCE_PROVIDER_ID,
    INFERENCE_PROVIDER_REVISION,
    INFERENCE_SCENARIO_ID as INFERENCE_SCENARIO_ID,
    TargetParameters as _TargetParameters,
    install_requirements as _install_requirements,
    parameters as _parameters,
    resolve_candidate as _resolve_candidate,
    scenario_descriptor as _scenario_descriptor,
)
from kamiwaza_sdk.validation.inference_evidence import (
    case_result as _case_result,
    chat_outcome as _chat_outcome,
    cleanup_failure as _cleanup_failure,
    elapsed_ms as _elapsed_ms,
    failed as _failed,
    failure as _failure,
    mapping as _mapping,
    optional_text as _optional_text,
    owned_deployments as _owned_deployments,
    passed as _passed,
    reconcile_deployment as _reconcile_deployment,
    required_image_digest as _required_image_digest,
    residual_outcome as _residual_outcome,
    runtime_evidence as _runtime_evidence,
    stop_outcome as _stop_outcome,
    stored_result as _stored_result,
    target_clusters as _target_clusters,
    target_state as _target_state,
    validate_expected_image as _validate_expected_image,
)
from kamiwaza_sdk.validation.models import (
    CaseResult,
    CleanupEvidence,
    CleanupResult,
    FixtureMutation,
    FixtureState,
    ResolvedScenario,
    RuntimeCluster,
    RuntimeContext,
    ScenarioDescriptor,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import (
    FixtureStateWriter,
    ProviderContractError,
    validate_plan_runtime_identity,
    validate_state_identity,
    validate_state_runtime_identity,
)
from kamiwaza_sdk.validation.registry import model_digest

_PREPARE_CASE_IDS = INFERENCE_CASE_IDS[:5]


@dataclass(frozen=True)
class _PrepareInput:
    selected: ResolvedScenario
    parameters: _TargetParameters
    runtime_cluster: RuntimeCluster
    writer: FixtureStateWriter


@dataclass(frozen=True)
class _PreparationContext:
    target_id: str
    parameters: _TargetParameters
    cluster: InferenceCluster | None
    writer: FixtureStateWriter


class InferenceLifecycleProvider:
    """Execute one exact, fail-closed text-generation lifecycle per target."""

    def __init__(self, cluster_factory: InferenceClusterFactory | None = None) -> None:
        self._cluster_factory = cluster_factory or _default_cluster_factory()

    def describe(self) -> tuple[ScenarioDescriptor, ...]:
        return (_scenario_descriptor(),)

    def resolve(self, profile: ValidationProfile) -> ScenarioPlan:
        descriptor = self.describe()[0]
        selected: tuple[ResolvedScenario, ...] = ()
        if descriptor_is_active(profile, descriptor):
            selected = self._resolve_active(profile, descriptor)
        return ScenarioPlan(
            schema="kamiwaza.scenario-plan/v1",
            profile_digest=model_digest(profile),
            provider_revision=INFERENCE_PROVIDER_REVISION,
            selected=selected,
            install_requirements=_install_requirements(selected),
            runtime_requirements=descriptor.requires,
        )

    def prepare(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        state_writer: FixtureStateWriter,
    ) -> FixtureState:
        self._validate_revision(plan.provider_revision)
        validate_plan_runtime_identity(plan, runtime)
        parameters = {
            item.target_id: _parameters(item.redacted_parameters)
            for item in plan.selected
        }
        state = _initial_state(plan, runtime, parameters)
        state_writer.write(state)
        runtime_clusters = {item.id: item for item in runtime.clusters}
        for selected in plan.selected:
            state = self._prepare_target(
                state,
                _PrepareInput(
                    selected=selected,
                    parameters=parameters[selected.target_id],
                    runtime_cluster=runtime_clusters[selected.cluster_id],
                    writer=state_writer,
                ),
            )
        return state

    def run(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        state: FixtureState,
    ) -> ScenarioEvidence:
        self._validate_revision(plan.provider_revision)
        validate_state_identity(plan, runtime, state)
        clusters = {item.id: item for item in runtime.clusters}
        results: list[CaseResult] = []
        resolved_runtime: dict[str, Any] = {}
        for selected in plan.selected:
            target_state = _target_state(state, selected.target_id)
            target_results, observation = self._run_target(
                selected,
                clusters[selected.cluster_id],
                target_state,
            )
            results.extend(target_results)
            resolved_runtime[selected.target_id] = observation
        return ScenarioEvidence(
            schema="kamiwaza.scenario-evidence/v1",
            provider_revision=INFERENCE_PROVIDER_REVISION,
            profile_digest=plan.profile_digest,
            plan_digest=model_digest(plan),
            state_digest=model_digest(state),
            results=tuple(results),
            resolved_runtime=resolved_runtime,
        )

    def teardown(self, runtime: RuntimeContext, state: FixtureState) -> CleanupEvidence:
        self._validate_revision(state.provider_revision)
        validate_state_runtime_identity(runtime, state)
        clusters = {item.id: item for item in runtime.clusters}
        target_clusters = _target_clusters(state)
        results = tuple(
            self._cleanup_resource(item, clusters[target_clusters[item.target_id]])
            for item in _owned_deployments(state)
        )
        status: Literal["passed", "failed"] = (
            "failed" if any(item.status == "failed" for item in results) else "passed"
        )
        return CleanupEvidence(
            schema="kamiwaza.cleanup-evidence/v1",
            provider_revision=INFERENCE_PROVIDER_REVISION,
            run_id=runtime.run_id,
            state_digest=model_digest(state),
            status=status,
            results=results,
        )

    def _resolve_active(
        self, profile: ValidationProfile, descriptor: ScenarioDescriptor
    ) -> tuple[ResolvedScenario, ...]:
        profile_targets = {item.id: item for item in profile.inference_targets}
        clusters = {item.id: item for item in profile.clusters}
        applicable = applicable_targets(profile, descriptor)
        explicit = descriptor.scenario_id in profile.validation.include
        selected: list[ResolvedScenario] = []
        for candidate in applicable:
            target = profile_targets[candidate.target_id]
            resolved = _resolve_candidate(target, clusters[target.cluster_id], explicit)
            if resolved is not None:
                selected.append(resolved)
        if explicit and not selected:
            raise ProviderContractError("requested scenario has no compatible target")
        return tuple(selected)

    def _prepare_target(
        self,
        state: FixtureState,
        inputs: _PrepareInput,
    ) -> FixtureState:
        started = time.monotonic()
        try:
            cluster = self._cluster_factory(inputs.runtime_cluster)
        except Exception as exc:
            context = _PreparationContext(
                inputs.selected.target_id, inputs.parameters, None, inputs.writer
            )
            failure = _failure("catalog-discovery", exc, _elapsed_ms(started))
            return _fail_remaining(state, context, 0, failure)
        context = _PreparationContext(
            inputs.selected.target_id, inputs.parameters, cluster, inputs.writer
        )
        try:
            return _prepare_with_cluster(state, context)
        finally:
            cluster.close()

    def _run_target(
        self,
        selected: ResolvedScenario,
        runtime_cluster: RuntimeCluster,
        target_state: Mapping[str, Any],
    ) -> tuple[list[CaseResult], dict[str, Any]]:
        outcomes = [
            _stored_result(selected, target_state, case) for case in _PREPARE_CASE_IDS
        ]
        deployment_id = _optional_text(target_state.get("deployment_id"))
        observation = _runtime_evidence(target_state)
        run_outcomes = self._execute_run_phases(
            runtime_cluster, target_state, deployment_id
        )
        outcomes.extend(
            _case_result(selected, case_id, outcome)
            for case_id, outcome in zip(
                INFERENCE_CASE_IDS[5:], run_outcomes, strict=True
            )
        )
        observation["timings_ms"] = {
            item.case_id: item.duration_ms for item in outcomes
        }
        return outcomes, observation

    def _execute_run_phases(
        self,
        runtime_cluster: RuntimeCluster,
        target_state: Mapping[str, Any],
        deployment_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if deployment_id is None:
            blocked = _failed("blocked: no owned deployment")
            return blocked, blocked, blocked
        try:
            cluster = self._cluster_factory(runtime_cluster)
        except Exception as exc:
            failed = _failure("runtime-client", exc, 0)
            return failed, failed, failed
        try:
            chat = _chat_outcome(cluster, deployment_id, target_state)
            stopped = _stop_outcome(cluster, deployment_id)
            residual = _residual_outcome(cluster, deployment_id)
            return chat, stopped, residual
        finally:
            cluster.close()

    def _cleanup_resource(
        self, mutation: FixtureMutation, runtime_cluster: RuntimeCluster
    ) -> CleanupResult:
        try:
            cluster = self._cluster_factory(runtime_cluster)
        except Exception as exc:
            return _cleanup_failure(mutation, exc)
        try:
            return _reconcile_deployment(cluster, mutation)
        finally:
            cluster.close()

    @staticmethod
    def _validate_revision(revision: str) -> None:
        if revision != INFERENCE_PROVIDER_REVISION:
            raise ProviderContractError("provider revision mismatch")


def _default_cluster_factory() -> InferenceClusterFactory:
    from kamiwaza_sdk.validation.sdk_inference_runtime import SdkInferenceClusterFactory

    return SdkInferenceClusterFactory()


def _initial_state(
    plan: ScenarioPlan,
    runtime: RuntimeContext,
    parameters: Mapping[str, _TargetParameters],
) -> FixtureState:
    targets = {
        item.target_id: {
            "cluster_id": item.cluster_id,
            "parameters": _parameter_payload(parameters[item.target_id]),
            "phases": {},
            "runtime": _parameter_payload(parameters[item.target_id]),
        }
        for item in plan.selected
    }
    owner = hashlib.sha256(
        f"{runtime.run_id}:{INFERENCE_PROVIDER_REVISION}".encode()
    ).hexdigest()
    return FixtureState(
        schema="kamiwaza.fixture-state/v1",
        provider_revision=INFERENCE_PROVIDER_REVISION,
        plan_digest=model_digest(plan),
        runtime_digest=model_digest(runtime),
        run_id=runtime.run_id,
        owner_token_digest=f"sha256:{owner}",
        journal=(),
        opaque={"targets": cast(JsonValue, targets)},
    )


def _parameter_payload(parameters: _TargetParameters) -> dict[str, Any]:
    return {
        "repository": parameters.repository,
        "engine": parameters.engine,
        "model_format": parameters.model_format,
        "quantization": parameters.quantization,
        "runtime_profile": parameters.runtime_profile,
        "expected_image": parameters.expected_image,
        "accelerators": [dict(item) for item in parameters.accelerators],
    }


def _prepare_with_cluster(
    state: FixtureState, context: _PreparationContext
) -> FixtureState:
    state, model = _catalog_phase(state, context)
    if model is None:
        return state
    state, model = _download_phase(state, context)
    if model is None:
        return state
    state, selection = _selection_phase(state, context, model)
    if selection is None:
        return state
    state, deployment_id = _deployment_phase(state, context, selection)
    if deployment_id is None:
        return state
    return _readiness_phase(state, context, deployment_id)


def _cluster(context: _PreparationContext) -> InferenceCluster:
    if context.cluster is None:
        raise ProviderContractError("inference cluster is unavailable")
    return context.cluster


def _catalog_phase(
    state: FixtureState, context: _PreparationContext
) -> tuple[FixtureState, CatalogModel | None]:
    started = time.monotonic()
    try:
        model = _cluster(context).discover(context.parameters.repository)
        if model.repository != context.parameters.repository:
            raise RuntimeError("catalog returned a different repository")
    except Exception as exc:
        failure = _failure(_PREPARE_CASE_IDS[0], exc, _elapsed_ms(started))
        return _fail_remaining(state, context, 0, failure), None
    state = _record_phase(state, context, _PREPARE_CASE_IDS[0], _passed(started))
    return state, model


def _download_phase(
    state: FixtureState, context: _PreparationContext
) -> tuple[FixtureState, CatalogModel | None]:
    started = time.monotonic()
    try:
        model = _cluster(context).ensure_download(
            context.parameters.repository, context.parameters.quantization
        )
        if not model.model_id:
            raise RuntimeError("downloaded model has no stable id")
        files = _target_files(model, context.parameters)
        if not files or not all(item.ready for item in files):
            raise RuntimeError("target files are not ready")
    except Exception as exc:
        failure = _failure(_PREPARE_CASE_IDS[1], exc, _elapsed_ms(started))
        return _fail_remaining(state, context, 1, failure), None
    state = _record_phase(state, context, _PREPARE_CASE_IDS[1], _passed(started))
    return state, model


def _selection_phase(
    state: FixtureState,
    context: _PreparationContext,
    model: CatalogModel,
) -> tuple[FixtureState, SelectedModel | None]:
    started = time.monotonic()
    try:
        if model.model_id is None:
            raise RuntimeError("downloaded model has no stable id")
        selection = _select_model(
            model,
            _cluster(context).list_configs(model.model_id),
            context.parameters,
        )
    except Exception as exc:
        failure = _failure(_PREPARE_CASE_IDS[2], exc, _elapsed_ms(started))
        return _fail_remaining(state, context, 2, failure), None
    state = _merge_runtime(state, context.target_id, _selection_payload(selection))
    state = _record_phase(state, context, _PREPARE_CASE_IDS[2], _passed(started))
    return state, selection


def _deployment_phase(
    state: FixtureState,
    context: _PreparationContext,
    selection: SelectedModel,
) -> tuple[FixtureState, str | None]:
    started = time.monotonic()
    request = DeploymentRequest(
        model_id=selection.model_id,
        config_id=selection.config_id,
        model_file_id=selection.model_file_id,
        engine=context.parameters.engine,
        runtime_profile=context.parameters.runtime_profile,
    )
    try:
        deployment_id = _cluster(context).deploy(request)
        if not deployment_id:
            raise RuntimeError("deployment did not return an id")
    except Exception as exc:
        failure = _failure(_PREPARE_CASE_IDS[3], exc, _elapsed_ms(started))
        return _fail_remaining(state, context, 3, failure), None
    state = _record_created_deployment(state, context, deployment_id)
    state = _record_phase(state, context, _PREPARE_CASE_IDS[3], _passed(started))
    return state, deployment_id


def _readiness_phase(
    state: FixtureState,
    context: _PreparationContext,
    deployment_id: str,
) -> FixtureState:
    started = time.monotonic()
    try:
        ready = _cluster(context).wait_ready(deployment_id)
        if ready.engine != context.parameters.engine or ready.instance_count < 1:
            raise RuntimeError("ready deployment does not match requested engine")
        observation = _cluster(context).observe_runtime(
            deployment_id, context.parameters.engine
        )
        state = _merge_runtime(
            state,
            context.target_id,
            {
                "actual_engine": ready.engine,
                "actual_image_digest": _required_image_digest(observation.image_digest),
                "effective_runtime_args": list(observation.effective_args),
            },
        )
        _validate_expected_image(
            context.parameters.expected_image, observation.image_digest
        )
        if not observation.effective_args:
            raise RuntimeError("effective runtime arguments are unavailable")
    except Exception as exc:
        failure = _failure(_PREPARE_CASE_IDS[4], exc, _elapsed_ms(started))
        return _fail_remaining(state, context, 4, failure)
    return _record_phase(state, context, _PREPARE_CASE_IDS[4], _passed(started))


def _target_files(
    model: CatalogModel, parameters: _TargetParameters
) -> tuple[CatalogFile, ...]:
    if parameters.model_format == "gguf":
        gguf = [item for item in model.files if item.name.lower().endswith(".gguf")]
        selected = QuantizationManager().filter_files_by_quantization(
            gguf, parameters.quantization, apply_fallback=False
        )
        return tuple(sorted(selected, key=lambda item: item.name.lower()))
    suffix = ".safetensors" if parameters.model_format == "safetensors" else ""
    selected = [item for item in model.files if item.name.lower().endswith(suffix)]
    return tuple(sorted(selected, key=lambda item: item.name.lower()))


def _select_model(
    model: CatalogModel,
    configs: Sequence[CatalogConfig],
    parameters: _TargetParameters,
) -> SelectedModel:
    if model.model_id is None:
        raise RuntimeError("downloaded model has no stable id")
    files = _ready_target_files(model, parameters)
    config = _select_config(configs)
    model_file_id = files[0].file_id if parameters.model_format == "gguf" else None
    return SelectedModel(
        model_id=model.model_id,
        config_id=config.config_id,
        model_file_id=model_file_id,
        model_files=files,
    )


def _ready_target_files(
    model: CatalogModel, parameters: _TargetParameters
) -> tuple[CatalogFile, ...]:
    files = _target_files(model, parameters)
    if not files:
        raise RuntimeError("no exact ready model files")
    _validate_ready_files(files)
    return files


def _validate_ready_files(files: Sequence[CatalogFile]) -> None:
    for item in files:
        if not item.ready:
            raise RuntimeError("no exact ready model files")
        if not item.file_id:
            raise RuntimeError("no exact ready model files")


def _select_config(configs: Sequence[CatalogConfig]) -> CatalogConfig:
    defaults = [item for item in configs if item.default]
    candidates = defaults or list(configs)
    if not candidates:
        raise RuntimeError("model has no deployment configuration")
    return min(candidates, key=lambda item: item.config_id)


def _selection_payload(selection: SelectedModel) -> dict[str, Any]:
    return {
        "model_id": selection.model_id,
        "config_id": selection.config_id,
        "model_file_id": selection.model_file_id,
        "model_files": [
            {"id": item.file_id, "name": item.name} for item in selection.model_files
        ],
    }


def _record_created_deployment(
    state: FixtureState,
    context: _PreparationContext,
    deployment_id: str,
) -> FixtureState:
    state = _set_target_value(state, context.target_id, "deployment_id", deployment_id)
    mutation = FixtureMutation(
        sequence=len(state.journal) + 1,
        target_id=context.target_id,
        resource_type="model-deployment",
        resource_id=deployment_id,
        action="created",
    )
    state = state.model_copy(update={"journal": (*state.journal, mutation)})
    context.writer.write(state)
    return state


def _fail_remaining(
    state: FixtureState,
    context: _PreparationContext,
    start_index: int,
    failure: Mapping[str, Any],
) -> FixtureState:
    for index, case_id in enumerate(_PREPARE_CASE_IDS[start_index:], start=start_index):
        outcome = (
            dict(failure)
            if index == start_index
            else _failed(f"blocked by {_PREPARE_CASE_IDS[start_index]}")
        )
        state = _record_phase(state, context, case_id, outcome)
    return state


def _record_phase(
    state: FixtureState,
    context: _PreparationContext,
    case_id: str,
    outcome: Mapping[str, Any],
) -> FixtureState:
    target = dict(_target_state(state, context.target_id))
    phases = dict(_mapping(target.get("phases"), "target phases"))
    phases[case_id] = dict(outcome)
    target["phases"] = phases
    state = _replace_target_state(state, context.target_id, target)
    context.writer.write(state)
    return state


def _merge_runtime(
    state: FixtureState, target_id: str, values: Mapping[str, Any]
) -> FixtureState:
    target = dict(_target_state(state, target_id))
    runtime = dict(_mapping(target.get("runtime"), "target runtime"))
    runtime.update(values)
    target["runtime"] = runtime
    return _replace_target_state(state, target_id, target)


def _set_target_value(
    state: FixtureState, target_id: str, key: str, value: Any
) -> FixtureState:
    target = dict(_target_state(state, target_id))
    target[key] = value
    return _replace_target_state(state, target_id, target)


def _replace_target_state(
    state: FixtureState, target_id: str, target: Mapping[str, Any]
) -> FixtureState:
    opaque = dict(state.opaque)
    targets = dict(_mapping(opaque.get("targets"), "fixture targets"))
    targets[target_id] = dict(target)
    opaque["targets"] = targets
    return state.model_copy(update={"opaque": opaque})


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local inference provider through the standard JSON adapter."""

    from kamiwaza_sdk.validation.cli import provider_main

    return provider_main(InferenceLifecycleProvider(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
