from __future__ import annotations

import pytest

from tests.integration.diffusion_targets import (
    DEFAULT_DIFFUSION_REPO,
    load_diffusion_target,
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
    ):
        monkeypatch.delenv(name, raising=False)

    target = load_diffusion_target()

    assert target.repo_id == DEFAULT_DIFFUSION_REPO
    assert target.family == "sd15"
    assert target.backend == "auto"
    assert target.fake is False
    assert target.size == "64x64"
    assert target.steps == 2
    assert target.model_path == f"hf://{DEFAULT_DIFFUSION_REPO}"


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
