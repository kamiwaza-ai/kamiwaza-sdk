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


def _configured_repo(generic_name: str, legacy_name: str, default: str) -> str:
    """Resolve a non-blank suite override, retaining context-test compatibility."""
    return (
        os.environ.get(generic_name, "").strip()
        or os.environ.get(legacy_name, "").strip()
        or default
    )


MLX_LLM_TARGET: Final[InferenceTarget] = InferenceTarget(
    repo_id=_configured_repo(
        "KAMIWAZA_TEST_MLX_LLM_REPO",
        "KAMIWAZA_CONTEXT_MLX_LLM_REPO",
        "mlx-community/Qwen3-4B-4bit",
    ),
    engine_name="mlx",
)
VLLM_LLM_TARGET: Final[InferenceTarget] = InferenceTarget(
    repo_id=_configured_repo(
        "KAMIWAZA_TEST_VLLM_LLM_REPO",
        "KAMIWAZA_CONTEXT_VLLM_LLM_REPO",
        "Qwen/Qwen3-0.6B",
    ),
    engine_name="vllm",
)
GGUF_LLM_TARGET: Final[InferenceTarget] = InferenceTarget(
    repo_id=_configured_repo(
        "KAMIWAZA_TEST_GGUF_LLM_REPO",
        "KAMIWAZA_CONTEXT_GGUF_LLM_REPO",
        "unsloth/Qwen3-4B-Instruct-2507-GGUF",
    ),
    engine_name="llamacpp",
)


def select_inference_target(
    snapshot: ClusterCapabilitySnapshot | None,
) -> InferenceTarget:
    """Choose weights and engine that match the live cluster hardware."""
    if snapshot is not None and "nvidia" in snapshot.gpu_vendors:
        return VLLM_LLM_TARGET
    if snapshot is not None and snapshot.is_apple_silicon:
        return MLX_LLM_TARGET
    return GGUF_LLM_TARGET
