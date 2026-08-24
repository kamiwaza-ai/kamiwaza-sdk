from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import capability_markers as cap  # noqa: E402
import model_targets as targets  # noqa: E402

pytestmark = pytest.mark.unit


def test_suite_repo_override_wins_and_rejects_blank_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAMIWAZA_TEST_TARGET_REPO", "  org/suite-target  ")
    monkeypatch.setenv("KAMIWAZA_CONTEXT_TARGET_REPO", "org/legacy-target")

    assert (
        targets._configured_repo(
            "KAMIWAZA_TEST_TARGET_REPO",
            "KAMIWAZA_CONTEXT_TARGET_REPO",
            "org/default-target",
        )
        == "org/suite-target"
    )

    monkeypatch.setenv("KAMIWAZA_TEST_TARGET_REPO", "  ")
    assert (
        targets._configured_repo(
            "KAMIWAZA_TEST_TARGET_REPO",
            "KAMIWAZA_CONTEXT_TARGET_REPO",
            "org/default-target",
        )
        == "org/legacy-target"
    )


def test_cpu_linux_selects_gguf_for_llamacpp() -> None:
    snapshot = cap.ClusterCapabilitySnapshot(
        os_platforms=frozenset({("linux", "linux-5.14.0-el9.x86_64")}),
    )

    selected = targets.select_inference_target(snapshot)

    assert selected == targets.GGUF_LLM_TARGET
    # The CPU fallback must not be a reasoning build; see _DEFAULT_GGUF_LLM_REPO.
    repo_id = targets._DEFAULT_GGUF_LLM_REPO.lower()
    assert not any(marker in repo_id for marker in targets._REASONING_REPO_MARKERS)


def test_apple_silicon_selects_mlx() -> None:
    snapshot = cap.ClusterCapabilitySnapshot(
        os_platforms=frozenset({("darwin", "macos-15.4-arm64-arm-64bit")}),
    )

    assert targets.select_inference_target(snapshot) == targets.MLX_LLM_TARGET


def test_nvidia_selects_vllm_before_host_platform() -> None:
    snapshot = cap.ClusterCapabilitySnapshot(
        gpu_count=1,
        gpu_vendors=frozenset({"nvidia"}),
        os_platforms=frozenset({("darwin", "macos-15.4-arm64-arm-64bit")}),
    )

    assert targets.select_inference_target(snapshot) == targets.VLLM_LLM_TARGET


def test_unknown_inventory_uses_portable_cpu_target() -> None:
    assert targets.select_inference_target(None) == targets.GGUF_LLM_TARGET


@pytest.mark.parametrize(
    ("target", "engine_name", "quantization"),
    [
        (targets.MLX_LLM_TARGET, "mlx", "q6_k"),
        (targets.VLLM_LLM_TARGET, "vllm", "q6_k"),
        (targets.GGUF_LLM_TARGET, "llamacpp", "q4_k"),
    ],
)
def test_target_engine_names_and_quantization(
    target: targets.InferenceTarget,
    engine_name: str,
    quantization: str,
) -> None:
    assert target.engine_name == engine_name
    assert target.quantization == quantization


def test_mixed_apple_and_linux_cluster_uses_portable_cpu_target() -> None:
    snapshot = cap.ClusterCapabilitySnapshot(
        os_platforms=frozenset(
            {
                ("darwin", "macos-15.4-arm64-arm-64bit"),
                ("linux", "linux-5.14.0-el9.x86_64"),
            }
        ),
    )

    assert targets.select_inference_target(snapshot) == targets.GGUF_LLM_TARGET


@pytest.mark.parametrize(
    ("env_name", "target_name"),
    [
        ("KAMIWAZA_TEST_MLX_LLM_REPO", "MLX_LLM_TARGET"),
        ("KAMIWAZA_CONTEXT_MLX_LLM_REPO", "MLX_LLM_TARGET"),
        ("KAMIWAZA_TEST_VLLM_LLM_REPO", "VLLM_LLM_TARGET"),
        ("KAMIWAZA_CONTEXT_VLLM_LLM_REPO", "VLLM_LLM_TARGET"),
        ("KAMIWAZA_TEST_GGUF_LLM_REPO", "GGUF_LLM_TARGET"),
        ("KAMIWAZA_CONTEXT_GGUF_LLM_REPO", "GGUF_LLM_TARGET"),
    ],
)
def test_real_repo_override_names_are_honored(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    target_name: str,
) -> None:
    override_names = {
        "KAMIWAZA_TEST_MLX_LLM_REPO",
        "KAMIWAZA_CONTEXT_MLX_LLM_REPO",
        "KAMIWAZA_TEST_VLLM_LLM_REPO",
        "KAMIWAZA_CONTEXT_VLLM_LLM_REPO",
        "KAMIWAZA_TEST_GGUF_LLM_REPO",
        "KAMIWAZA_CONTEXT_GGUF_LLM_REPO",
    }
    for name in override_names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_name, "org/override")

    mlx_target, vllm_target, gguf_target = targets._load_inference_targets()
    resolved_targets = {
        "MLX_LLM_TARGET": mlx_target,
        "VLLM_LLM_TARGET": vllm_target,
        "GGUF_LLM_TARGET": gguf_target,
    }

    assert resolved_targets[target_name].repo_id == "org/override"
