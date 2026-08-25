"""Strict SDK-owned local model lifecycle scenario provider."""

from __future__ import annotations

import hashlib
import re
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
from kamiwaza_sdk.validation.models import (
    CaseResult,
    CleanupEvidence,
    CleanupResult,
    FactMatcher,
    FixtureMutation,
    FixtureState,
    InferenceTarget,
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

INFERENCE_PROVIDER_ID = "sdk.inference"
INFERENCE_PROVIDER_REVISION = "sdk.inference.lifecycle@v1"
INFERENCE_SCENARIO_ID = "sdk.inference.lifecycle/v1"
INFERENCE_CASE_IDS = (
    "catalog-discovery",
    "download-readiness",
    "exact-model-file-selection",
    "explicit-engine-deployment",
    "deployment-readiness",
    "openai-multi-turn-chat",
    "deployment-stop",
    "residual-cleanup",
)
_PREPARE_CASE_IDS = INFERENCE_CASE_IDS[:5]
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SUPPORTED_FORMATS = {"llamacpp": "gguf", "vllm": "safetensors"}
_VLLM_ACCELERATORS = frozenset({"amd", "nvidia"})


@dataclass(frozen=True)
class _TargetParameters:
    repository: str
    engine: str
    model_format: str
    quantization: str
    runtime_profile: str
    expected_image: str | None
    accelerators: tuple[dict[str, Any], ...]


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
        return (
            ScenarioDescriptor(
                scenario_id=INFERENCE_SCENARIO_ID,
                provider_id=INFERENCE_PROVIDER_ID,
                protocol_version="v1",
                target_scope="inference_target",
                minimum_level="smoke",
                capability_ids=("inference.chat", "inference.model-lifecycle"),
                applies_when=(
                    FactMatcher(
                        path=("cluster", "roles"),
                        operator="contains",
                        value="inference",
                    ),
                    FactMatcher(
                        path=("target", "engine"),
                        operator="in",
                        value=cast(JsonValue, sorted(_SUPPORTED_FORMATS)),
                    ),
                ),
                requires=("cluster-api", "kube-api"),
                fixture_modes=("owned", "external"),
                case_ids=INFERENCE_CASE_IDS,
            ),
        )

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


def _compatibility_error(target: InferenceTarget, cluster: Any) -> str | None:
    expected_format = _SUPPORTED_FORMATS.get(target.engine)
    if expected_format != target.model_format:
        return "engine/model format mismatch"
    if target.runtime_profile != "product-default":
        return "unsupported semantic runtime profile"
    vendors = {item.vendor for item in cluster.hardware.accelerators}
    if target.engine == "vllm" and not vendors & _VLLM_ACCELERATORS:
        return "vllm requires a supported accelerator"
    return None


def _resolve_candidate(
    target: InferenceTarget, cluster: Any, explicit: bool
) -> ResolvedScenario | None:
    reason = _compatibility_error(target, cluster)
    if reason and target.required:
        raise ProviderContractError(
            f"incompatible required target: {target.id} ({reason})"
        )
    if reason or not (target.required or explicit):
        return None
    return _resolved_target(target, cluster)


def _resolved_target(target: InferenceTarget, cluster: Any) -> ResolvedScenario:
    accelerators = [
        item.model_dump(mode="json") for item in cluster.hardware.accelerators
    ]
    return ResolvedScenario(
        target_id=target.id,
        cluster_id=target.cluster_id,
        scenario_id=INFERENCE_SCENARIO_ID,
        required=target.required,
        case_ids=INFERENCE_CASE_IDS,
        redacted_parameters={
            "repository": target.repository,
            "engine": target.engine,
            "model_format": target.model_format,
            "quantization": target.quantization,
            "runtime_profile": target.runtime_profile,
            "expected_image": target.expected_image,
            "accelerators": accelerators,
        },
    )


def _install_requirements(
    selected: Sequence[ResolvedScenario],
) -> dict[str, Any]:
    images = {
        item.target_id: image
        for item in selected
        if isinstance(image := item.redacted_parameters.get("expected_image"), str)
    }
    return {"inference_images": images} if images else {}


def _parameters(values: Mapping[str, Any]) -> _TargetParameters:
    expected_image = values.get("expected_image")
    accelerators = values.get("accelerators")
    if expected_image is not None and not isinstance(expected_image, str):
        raise ProviderContractError("resolved target has invalid expected image")
    if not isinstance(accelerators, list) or not all(
        isinstance(item, dict) for item in accelerators
    ):
        raise ProviderContractError("resolved target has invalid accelerator facts")
    return _TargetParameters(
        repository=_required_text(values, "repository"),
        engine=_required_text(values, "engine"),
        model_format=_required_text(values, "model_format"),
        quantization=_required_text(values, "quantization"),
        runtime_profile=_required_text(values, "runtime_profile"),
        expected_image=expected_image,
        accelerators=tuple(dict(item) for item in accelerators),
    )


def _required_text(values: Mapping[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise ProviderContractError(f"resolved target has invalid {key}")
    return value


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
    if any(not item.ready for item in files):
        raise RuntimeError("no exact ready model files")
    if any(not item.file_id for item in files):
        raise RuntimeError("no exact ready model files")
    return files


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


def _target_state(state: FixtureState, target_id: str) -> Mapping[str, Any]:
    targets = _mapping(state.opaque.get("targets"), "fixture targets")
    return _mapping(targets.get(target_id), "fixture target")


def _target_clusters(state: FixtureState) -> dict[str, str]:
    targets = _mapping(state.opaque.get("targets"), "fixture targets")
    return {
        target_id: _required_text(_mapping(value, "fixture target"), "cluster_id")
        for target_id, value in targets.items()
    }


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderContractError(f"{label} is invalid")
    return value


def _stored_result(
    selected: ResolvedScenario,
    target_state: Mapping[str, Any],
    case_id: str,
) -> CaseResult:
    phases = _mapping(target_state.get("phases"), "target phases")
    outcome = _mapping(phases.get(case_id), "phase outcome")
    return _case_result(selected, case_id, outcome)


def _case_result(
    selected: ResolvedScenario, case_id: str, outcome: Mapping[str, Any]
) -> CaseResult:
    status = outcome.get("status")
    duration = outcome.get("duration_ms")
    detail = outcome.get("detail")
    if status not in {"passed", "failed"} or not isinstance(duration, int):
        raise ProviderContractError("phase outcome is invalid")
    if detail is not None and not isinstance(detail, str):
        raise ProviderContractError("phase detail is invalid")
    return CaseResult(
        target_id=selected.target_id,
        scenario_id=selected.scenario_id,
        case_id=case_id,
        status=status,
        duration_ms=duration,
        detail=detail,
    )


def _chat_outcome(
    cluster: InferenceCluster,
    deployment_id: str,
    target_state: Mapping[str, Any],
) -> dict[str, Any]:
    phases = _mapping(target_state.get("phases"), "target phases")
    readiness = _mapping(phases.get("deployment-readiness"), "readiness phase")
    if readiness.get("status") != "passed":
        return _failed("blocked by deployment-readiness")
    started = time.monotonic()
    try:
        first_messages = (
            {"role": "user", "content": "Reply with one short greeting."},
        )
        first = cluster.chat(deployment_id, first_messages).strip()
        if not first:
            raise RuntimeError("first chat turn was empty")
        second_messages = (
            first_messages[0],
            {"role": "assistant", "content": first},
            {"role": "user", "content": "Reply with one short farewell."},
        )
        if not cluster.chat(deployment_id, second_messages).strip():
            raise RuntimeError("second chat turn was empty")
    except Exception as exc:
        return _failure("openai-multi-turn-chat", exc, _elapsed_ms(started))
    return _passed(started)


def _stop_outcome(cluster: InferenceCluster, deployment_id: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not cluster.stop(deployment_id):
            raise RuntimeError("platform did not confirm deployment stop")
    except Exception as exc:
        return _failure("deployment-stop", exc, _elapsed_ms(started))
    return _passed(started)


def _residual_outcome(cluster: InferenceCluster, deployment_id: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if cluster.is_active(deployment_id):
            raise RuntimeError("run-owned deployment remains active")
    except Exception as exc:
        return _failure("residual-cleanup", exc, _elapsed_ms(started))
    return _passed(started)


def _runtime_evidence(target_state: Mapping[str, Any]) -> dict[str, Any]:
    return dict(_mapping(target_state.get("runtime"), "target runtime"))


def _owned_deployments(state: FixtureState) -> tuple[FixtureMutation, ...]:
    resources: dict[tuple[str, str, str], FixtureMutation] = {}
    for item in state.journal:
        if item.resource_type == "model-deployment":
            resources[(item.target_id, item.resource_type, item.resource_id)] = item
    return tuple(resources[key] for key in sorted(resources))


def _reconcile_deployment(
    cluster: InferenceCluster, mutation: FixtureMutation
) -> CleanupResult:
    try:
        if not cluster.is_active(mutation.resource_id):
            return _cleanup_result(mutation, "absent")
        stopped = cluster.stop(mutation.resource_id)
        if not stopped or cluster.is_active(mutation.resource_id):
            raise RuntimeError("owned deployment remains active")
    except Exception as exc:
        return _cleanup_failure(mutation, exc)
    return _cleanup_result(mutation, "removed")


def _cleanup_result(
    mutation: FixtureMutation,
    status: Literal["removed", "absent", "retained_foreign", "failed"],
) -> CleanupResult:
    return CleanupResult(
        target_id=mutation.target_id,
        resource_type=mutation.resource_type,
        resource_id=mutation.resource_id,
        status=status,
        detail=None,
    )


def _cleanup_failure(mutation: FixtureMutation, exc: Exception) -> CleanupResult:
    return CleanupResult(
        target_id=mutation.target_id,
        resource_type=mutation.resource_type,
        resource_id=mutation.resource_id,
        status="failed",
        detail=_safe_error("cleanup", exc),
    )


def _required_image_digest(value: str | None) -> str:
    digest = _image_digest(value)
    if digest is None:
        raise RuntimeError("actual image digest is unavailable")
    return digest


def _validate_expected_image(expected: str | None, actual: str | None) -> None:
    if expected is None:
        return
    if _image_digest(expected) != _image_digest(actual):
        raise RuntimeError("actual image does not match expected image")


def _image_digest(value: str | None) -> str | None:
    if value is None:
        return None
    match = _DIGEST_RE.search(value)
    return match.group(0) if match else None


def _passed(started: float) -> dict[str, Any]:
    return {"status": "passed", "duration_ms": _elapsed_ms(started), "detail": None}


def _failed(detail: str) -> dict[str, Any]:
    return {"status": "failed", "duration_ms": 0, "detail": detail}


def _failure(phase: str, exc: Exception, duration_ms: int) -> dict[str, Any]:
    return {
        "status": "failed",
        "duration_ms": duration_ms,
        "detail": _safe_error(phase, exc),
    }


def _safe_error(phase: str, exc: Exception) -> str:
    return f"{phase} failed ({type(exc).__name__})"


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local inference provider through the standard JSON adapter."""

    from kamiwaza_sdk.validation.cli import provider_main

    return provider_main(InferenceLifecycleProvider(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
