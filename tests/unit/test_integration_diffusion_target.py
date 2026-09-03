from __future__ import annotations

import os

import pytest

from tests.integration.capability_markers import ClusterCapabilitySnapshot
from tests.integration.diffusion_targets import (
    DEFAULT_DIFFUSION_REPO,
    DEFAULT_QWEN_IMAGE_EDIT_REPO,
    DEFAULT_QWEN_IMAGE_REPO,
    DEFAULT_QWEN_IMAGE_SPLIT_REPO,
    load_diffusion_target,
    load_qwen_image_edit_target,
    load_qwen_image_split_target,
    load_qwen_image_target,
    qwen_acceleration_available,
)


def test_default_diffusion_target_runs_real_cross_platform_smoke(monkeypatch) -> None:
    for name in (
        "KAMIWAZA_TEST_DIFFUSION_REPO",
        "KAMIWAZA_TEST_DIFFUSION_FAMILY",
        "KAMIWAZA_TEST_DIFFUSION_BACKEND",
        "KAMIWAZA_TEST_DIFFUSION_IMAGE",
        "KAMIWAZA_TEST_DIFFUSION_FAKE",
        "KAMIWAZA_TEST_DIFFUSION_SIZE",
        "KAMIWAZA_TEST_DIFFUSION_STEPS",
        "KAMIWAZA_TEST_DIFFUSION_GUIDANCE",
        "KAMIWAZA_TEST_DIFFUSION_TIMEOUT",
        "KAMIWAZA_TEST_DIFFUSION_GPU_COUNT",
    ):
        monkeypatch.delenv(name, raising=False)

    target = load_diffusion_target()

    assert target.repo_id == DEFAULT_DIFFUSION_REPO
    assert target.case == "portable-sd15"
    assert target.family == "sd15"
    assert target.backend == "auto"
    assert target.fake is False
    assert target.size == "64x64"
    assert target.steps == 2
    assert target.model_path == f"hf://{DEFAULT_DIFFUSION_REPO}"
    assert target.gpu_count == 0


def test_diffusion_target_accepts_fleet_overrides(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_REPO", "Qwen/Qwen-Image")
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_FAMILY", "qwen-image")
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_BACKEND", "nvidia")
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_IMAGE", "registry/diffusion:test")
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_FAKE", "true")
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_SIZE", "128x96")
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_STEPS", "4")
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_GUIDANCE", "1.5")
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_TIMEOUT", "1200")
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_GPU_COUNT", "2")

    target = load_diffusion_target()

    assert target.repo_id == "Qwen/Qwen-Image"
    assert target.family == "qwen-image"
    assert target.backend == "nvidia"
    assert target.image == "registry/diffusion:test"
    assert target.fake is True
    assert target.size == "128x96"
    assert target.steps == 4
    assert target.guidance_scale == 1.5
    assert target.timeout_seconds == 1200
    assert target.gpu_count == 2


def test_qwen_targets_are_distinct_required_model_families(monkeypatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("KAMIWAZA_TEST_QWEN_IMAGE"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("KAMIWAZA_TEST_DIFFUSION_BACKEND", raising=False)
    monkeypatch.delenv("KAMIWAZA_TEST_DIFFUSION_IMAGE", raising=False)

    generation = load_qwen_image_target()
    edit = load_qwen_image_edit_target()
    split = load_qwen_image_split_target()

    assert generation.repo_id == DEFAULT_QWEN_IMAGE_REPO
    assert generation.family == "qwen-image"
    assert generation.backend == "auto"
    assert generation.size == "512x512"
    assert generation.steps == 50
    assert generation.guidance_scale == 4.0
    assert edit.repo_id == DEFAULT_QWEN_IMAGE_EDIT_REPO
    assert edit.family == "qwen-image-edit"
    assert edit.steps == 40
    assert edit.guidance_scale == 4.0
    assert split.repo_id == DEFAULT_QWEN_IMAGE_SPLIT_REPO
    assert split.family == "qwen-image"
    assert split.backend == "nvidia"
    assert split.gpu_count == 2
    assert split.steps == 30
    assert split.guidance_scale == 4.0
    assert split.timeout_seconds == 3600


def test_qwen_targets_inherit_prepared_backend_and_image(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_BACKEND", "mps")
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_IMAGE", "registry/diffusion:uat")

    target = load_qwen_image_target()

    assert target.backend == "mps"
    assert target.image == "registry/diffusion:uat"


def test_split_target_does_not_inherit_a_non_nvidia_backend(monkeypatch) -> None:
    monkeypatch.setenv("KAMIWAZA_TEST_DIFFUSION_BACKEND", "cpu")

    target = load_qwen_image_split_target()

    assert target.backend == "nvidia"


def test_qwen_acceleration_recognizes_host_spawned_apple_metal(monkeypatch) -> None:
    monkeypatch.setattr(
        "tests.integration.diffusion_targets.platform.system", lambda: "Darwin"
    )
    monkeypatch.setattr(
        "tests.integration.diffusion_targets.platform.machine", lambda: "arm64"
    )
    linux_lima_inventory = ClusterCapabilitySnapshot(
        gpu_count=0,
        gpu_vendors=frozenset(),
        os_platforms=frozenset({("linux", "aarch64")}),
    )

    assert qwen_acceleration_available(linux_lima_inventory) is True


def test_qwen_acceleration_requires_sufficient_nvidia_vram(monkeypatch) -> None:
    monkeypatch.setattr(
        "tests.integration.diffusion_targets.platform.system", lambda: "Linux"
    )
    t4_inventory = ClusterCapabilitySnapshot(
        gpu_count=1,
        gpu_mem_gb=(15.0,),
        gpu_vendors=frozenset({"nvidia"}),
    )
    a10_inventory = ClusterCapabilitySnapshot(
        gpu_count=1,
        gpu_mem_gb=(24.0,),
        gpu_vendors=frozenset({"nvidia"}),
    )

    assert qwen_acceleration_available(t4_inventory) is False
    assert qwen_acceleration_available(a10_inventory) is True


def test_qwen_acceleration_fails_closed_when_gpu_memory_is_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        "tests.integration.diffusion_targets.platform.system", lambda: "Linux"
    )
    inventory = ClusterCapabilitySnapshot(
        gpu_count=1,
        gpu_vendors=frozenset({"nvidia"}),
    )

    assert qwen_acceleration_available(inventory) is False


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("KAMIWAZA_TEST_DIFFUSION_FAKE", "maybe", "boolean"),
        ("KAMIWAZA_TEST_DIFFUSION_STEPS", "zero", "integer"),
        ("KAMIWAZA_TEST_DIFFUSION_STEPS", "0", "at least 1"),
        ("KAMIWAZA_TEST_DIFFUSION_GUIDANCE", "none", "number"),
        ("KAMIWAZA_TEST_DIFFUSION_GUIDANCE", "-1", "at least 0.0"),
        ("KAMIWAZA_TEST_DIFFUSION_SIZE", "large", "WIDTHxHEIGHT"),
        ("KAMIWAZA_TEST_DIFFUSION_SIZE", "32x64", "between 64 and 2048"),
        ("KAMIWAZA_TEST_DIFFUSION_BACKEND", "tpu", "unsupported"),
        ("KAMIWAZA_TEST_DIFFUSION_GPU_COUNT", "-1", "at least 0"),
    ],
)
def test_diffusion_target_rejects_invalid_overrides(
    monkeypatch, name: str, value: str, message: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        load_diffusion_target()


@pytest.mark.parametrize(
    "name", ["KAMIWAZA_TEST_DIFFUSION_REPO", "KAMIWAZA_TEST_DIFFUSION_FAMILY"]
)
def test_diffusion_target_rejects_blank_identity(monkeypatch, name: str) -> None:
    monkeypatch.setenv(name, " ")

    with pytest.raises(ValueError, match="must not be blank"):
        load_diffusion_target()
