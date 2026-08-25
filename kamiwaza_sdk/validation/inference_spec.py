"""Static contract and deterministic resolution for inference validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import JsonValue

from kamiwaza_sdk.validation.models import (
    FactMatcher,
    InferenceTarget,
    ResolvedScenario,
    ScenarioDescriptor,
)
from kamiwaza_sdk.validation.provider import ProviderContractError

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
SUPPORTED_FORMATS = {"llamacpp": "gguf", "vllm": "safetensors"}
VLLM_ACCELERATORS = frozenset({"amd", "nvidia"})


@dataclass(frozen=True)
class TargetParameters:
    repository: str
    engine: str
    model_format: str
    quantization: str
    runtime_profile: str
    expected_image: str | None
    accelerators: tuple[dict[str, Any], ...]


def scenario_descriptor() -> ScenarioDescriptor:
    return ScenarioDescriptor(
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
                value=cast(JsonValue, sorted(SUPPORTED_FORMATS)),
            ),
        ),
        requires=("cluster-api", "kube-api"),
        fixture_modes=("owned", "external"),
        case_ids=INFERENCE_CASE_IDS,
    )


def resolve_candidate(
    target: InferenceTarget, cluster: Any, explicit: bool
) -> ResolvedScenario | None:
    reason = _compatibility_error(target, cluster)
    if reason:
        if target.required:
            raise ProviderContractError(
                f"incompatible required target: {target.id} ({reason})"
            )
        return None
    if target.required:
        return _resolved_target(target, cluster)
    if explicit:
        return _resolved_target(target, cluster)
    return None


def _compatibility_error(target: InferenceTarget, cluster: Any) -> str | None:
    expected_format = SUPPORTED_FORMATS.get(target.engine)
    if expected_format != target.model_format:
        return "engine/model format mismatch"
    if target.runtime_profile != "product-default":
        return "unsupported semantic runtime profile"
    vendors = {item.vendor for item in cluster.hardware.accelerators}
    if target.engine == "vllm" and not vendors & VLLM_ACCELERATORS:
        return "vllm requires a supported accelerator"
    return None


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


def install_requirements(selected: Sequence[ResolvedScenario]) -> dict[str, Any]:
    images = {
        item.target_id: image
        for item in selected
        if isinstance(image := item.redacted_parameters.get("expected_image"), str)
    }
    return {"inference_images": images} if images else {}


def parameters(values: Mapping[str, Any]) -> TargetParameters:
    return TargetParameters(
        repository=_required_text(values, "repository"),
        engine=_required_text(values, "engine"),
        model_format=_required_text(values, "model_format"),
        quantization=_required_text(values, "quantization"),
        runtime_profile=_required_text(values, "runtime_profile"),
        expected_image=_expected_image(values.get("expected_image")),
        accelerators=_accelerator_facts(values.get("accelerators")),
    )


def _expected_image(value: Any) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ProviderContractError("resolved target has invalid expected image")
    return value


def _accelerator_facts(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise ProviderContractError("resolved target has invalid accelerator facts")
    if any(not isinstance(item, dict) for item in value):
        raise ProviderContractError("resolved target has invalid accelerator facts")
    return tuple(dict(item) for item in value)


def _required_text(values: Mapping[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise ProviderContractError(f"resolved target has invalid {key}")
    return value
