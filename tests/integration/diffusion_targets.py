"""Cross-platform target configuration for live DiffusionEngine validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

DEFAULT_DIFFUSION_REPO: Final = "hf-internal-testing/tiny-stable-diffusion-pipe"
SUPPORTED_BACKENDS: Final = frozenset(
    {
        "auto",
        "cpu",
        "cuda",
        "nvidia",
        "rocm",
        "amd",
        "mlx",
        "mps",
        "intel",
        "spark",
    }
)


@dataclass(frozen=True)
class DiffusionTarget:
    """One model/runtime contract exercised by the live SDK suite."""

    repo_id: str
    family: str
    backend: str
    image: str | None
    fake: bool
    size: str
    steps: int
    guidance_scale: float
    timeout_seconds: int

    @property
    def model_path(self) -> str:
        return f"hf://{self.repo_id}"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _env_float(name: str, default: float, minimum: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _image_size() -> str:
    value = os.environ.get("KAMIWAZA_TEST_DIFFUSION_SIZE", "64x64").strip()
    parts = value.lower().split("x", maxsplit=1)
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("KAMIWAZA_TEST_DIFFUSION_SIZE must be WIDTHxHEIGHT")
    dimensions = [int(part) for part in parts]
    if any(dimension < 64 or dimension > 2048 for dimension in dimensions):
        raise ValueError(
            "KAMIWAZA_TEST_DIFFUSION_SIZE dimensions must be between 64 and 2048"
        )
    return value.lower()


def load_diffusion_target() -> DiffusionTarget:
    """Load the required live target, rejecting partial or invalid overrides."""
    repo_id = os.environ.get(
        "KAMIWAZA_TEST_DIFFUSION_REPO", DEFAULT_DIFFUSION_REPO
    ).strip()
    family = os.environ.get("KAMIWAZA_TEST_DIFFUSION_FAMILY", "sd15").strip()
    backend = os.environ.get("KAMIWAZA_TEST_DIFFUSION_BACKEND", "auto").strip()
    if not repo_id or not family:
        raise ValueError("Diffusion repo and family overrides must not be blank")
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"unsupported KAMIWAZA_TEST_DIFFUSION_BACKEND: {backend}")
    return DiffusionTarget(
        repo_id=repo_id,
        family=family,
        backend=backend,
        image=os.environ.get("KAMIWAZA_TEST_DIFFUSION_IMAGE", "").strip() or None,
        fake=_env_bool("KAMIWAZA_TEST_DIFFUSION_FAKE"),
        size=_image_size(),
        steps=_env_int("KAMIWAZA_TEST_DIFFUSION_STEPS", 2, 1),
        guidance_scale=_env_float("KAMIWAZA_TEST_DIFFUSION_GUIDANCE", 1.0, 0.0),
        timeout_seconds=_env_int("KAMIWAZA_TEST_DIFFUSION_TIMEOUT", 900, 1),
    )
