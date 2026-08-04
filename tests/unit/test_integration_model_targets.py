from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import capability_markers as cap  # noqa: E402
import model_targets as targets  # noqa: E402

pytestmark = pytest.mark.unit


def test_cpu_linux_selects_gguf_for_llamacpp() -> None:
    snapshot = cap.ClusterCapabilitySnapshot(
        os_names=frozenset({"linux"}),
        platforms=frozenset({"linux-5.14.0-el9.x86_64"}),
    )

    assert targets.select_inference_target(snapshot) == targets.GGUF_LLM_TARGET


def test_apple_silicon_selects_mlx() -> None:
    snapshot = cap.ClusterCapabilitySnapshot(
        os_names=frozenset({"darwin"}),
        platforms=frozenset({"macos-15.4-arm64-arm-64bit"}),
    )

    assert targets.select_inference_target(snapshot) == targets.MLX_LLM_TARGET


def test_nvidia_selects_vllm_before_host_platform() -> None:
    snapshot = cap.ClusterCapabilitySnapshot(
        gpu_count=1,
        gpu_vendors=frozenset({"nvidia"}),
        os_names=frozenset({"linux"}),
        platforms=frozenset({"linux-x86_64"}),
    )

    assert targets.select_inference_target(snapshot) == targets.VLLM_LLM_TARGET


def test_unknown_inventory_uses_portable_cpu_target() -> None:
    assert targets.select_inference_target(None) == targets.GGUF_LLM_TARGET
