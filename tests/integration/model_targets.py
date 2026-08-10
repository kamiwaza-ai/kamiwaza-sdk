"""Shared, platform-aware inference targets for live SDK tests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from capability_markers import ClusterCapabilitySnapshot


@dataclass(frozen=True)
class InferenceTarget:
    """A model repository paired with the engine that can load its weights."""

    repo_id: str
    engine_name: str
    quantization: str = "q6_k"


def _configured_repo(generic_name: str, legacy_name: str, default: str) -> str:
    """Resolve a non-blank suite override, retaining context-test compatibility."""
    return (
        os.environ.get(generic_name, "").strip()
        or os.environ.get(legacy_name, "").strip()
        or default
    )


_DEFAULT_MLX_LLM_REPO: Final = "mlx-community/Qwen3-4B-4bit"
_DEFAULT_VLLM_LLM_REPO: Final = "Qwen/Qwen3-0.6B"
_REASONING_REPO_MARKERS: Final = ("thinking", "reasoning", "-r1-", "qwq")
# Instruct, not Thinking. The GGUF target is the CPU fallback, and it backs
# Graphiti entity extraction in the context ontology tests. A reasoning model
# emits its thinking trace before the answer, which on a GPU-less host pushes a
# single add_knowledge call past the ~300s upstream timeout — the extraction is
# not more correct for the wait, just later. Same family, size and quant as the
# Thinking build, so the deploy path under test is unchanged.
_DEFAULT_GGUF_LLM_REPO: Final = "unsloth/Qwen3-4B-Instruct-2507-GGUF"


def _load_inference_targets() -> tuple[InferenceTarget, InferenceTarget, InferenceTarget]:
    """Resolve all target overrides without mutating module state."""
    return (
        InferenceTarget(
            repo_id=_configured_repo(
                "KAMIWAZA_TEST_MLX_LLM_REPO",
                "KAMIWAZA_CONTEXT_MLX_LLM_REPO",
                _DEFAULT_MLX_LLM_REPO,
            ),
            engine_name="mlx",
        ),
        InferenceTarget(
            repo_id=_configured_repo(
                "KAMIWAZA_TEST_VLLM_LLM_REPO",
                "KAMIWAZA_CONTEXT_VLLM_LLM_REPO",
                _DEFAULT_VLLM_LLM_REPO,
            ),
            engine_name="vllm",
        ),
        InferenceTarget(
            repo_id=_configured_repo(
                "KAMIWAZA_TEST_GGUF_LLM_REPO",
                "KAMIWAZA_CONTEXT_GGUF_LLM_REPO",
                _DEFAULT_GGUF_LLM_REPO,
            ),
            engine_name="llamacpp",
            quantization="q4_k",
        ),
    )


MLX_LLM_TARGET, VLLM_LLM_TARGET, GGUF_LLM_TARGET = _load_inference_targets()


def _nvidia_target_override() -> InferenceTarget | None:
    """Resolve an explicit NVIDIA-host engine override, or None for the default.

    The default NVIDIA target is vLLM, which needs a resolvable vllm-cuda image
    in the cluster's inference cascade. Where that image is unavailable (e.g. the
    packaged-prod turing cascade has no published/keyed vllm-cuda-turing image),
    a smoke/CI runner can set KAMIWAZA_TEST_NVIDIA_ENGINE=llamacpp to prove
    GPU-placed inference with the GGUF/llamacpp target instead. Unset preserves
    the vLLM selection, so non-smoke live suites are unchanged.
    """
    engine = os.environ.get("KAMIWAZA_TEST_NVIDIA_ENGINE", "").strip().lower()
    if engine == "llamacpp":
        return GGUF_LLM_TARGET
    if engine == "mlx":
        return MLX_LLM_TARGET
    if engine == "vllm":
        return VLLM_LLM_TARGET
    return None


def select_inference_target(
    snapshot: ClusterCapabilitySnapshot | None,
) -> InferenceTarget:
    """Choose weights and engine that match the live cluster hardware."""
    if snapshot is not None and "nvidia" in snapshot.gpu_vendors:
        return _nvidia_target_override() or VLLM_LLM_TARGET
    if snapshot is not None and snapshot.is_apple_silicon:
        return MLX_LLM_TARGET
    return GGUF_LLM_TARGET
