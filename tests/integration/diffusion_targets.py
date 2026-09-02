"""Cross-platform target configuration for live DiffusionEngine validation."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from typing import Any, Final

DEFAULT_DIFFUSION_REPO: Final = "dg845/tiny-random-stable-diffusion"
DEFAULT_QWEN_IMAGE_REPO: Final = "Qwen/Qwen-Image-2512"
DEFAULT_QWEN_IMAGE_EDIT_REPO: Final = "Qwen/Qwen-Image-Edit-2509"
DEFAULT_QWEN_IMAGE_SPLIT_REPO: Final = "m9e/Qwen-Image-2512-DF11"
# Qwen Image is materially larger than the portable SD smoke.  Keep the
# accelerated lane fail-closed on cards whose inventory reports less than the
# 16 GiB floor used by the rest of the integration capability markers.
MIN_QWEN_GPU_MEM_GB: Final = 16.0
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

    case: str
    repo_id: str
    family: str
    backend: str
    image: str | None
    fake: bool
    size: str
    steps: int
    guidance_scale: float
    timeout_seconds: int
    gpu_count: int = 0

    @property
    def model_path(self) -> str:
        return f"hf://{self.repo_id}"


def qwen_acceleration_available(snapshot: Any) -> bool:
    """Recognize Metal or an accelerator with enough reported memory for Qwen."""
    if platform.system() == "Darwin" and platform.machine().lower() in {
        "arm64",
        "aarch64",
    }:
        return True
    if snapshot is None:
        return False
    if snapshot.is_apple_silicon:
        return True
    if not (snapshot.gpu_count and snapshot.gpu_vendors & {"nvidia", "amd"}):
        return False
    # Unknown memory is an unmet capability, not permission to launch a model
    # that can fail after a long download/deploy cycle.  This mirrors
    # capability_markers' fail-closed min_gpu_mem behavior.
    return bool(
        snapshot.gpu_mem_gb
        and max(snapshot.gpu_mem_gb) >= MIN_QWEN_GPU_MEM_GB
    )


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


def _image_size(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    parts = value.lower().split("x", maxsplit=1)
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"{name} must be WIDTHxHEIGHT")
    dimensions = [int(part) for part in parts]
    if any(dimension < 64 or dimension > 2048 for dimension in dimensions):
        raise ValueError(f"{name} dimensions must be between 64 and 2048")
    return value.lower()


def _load_target(
    *,
    case: str,
    env_prefix: str,
    default_repo: str,
    default_family: str,
    default_size: str,
    default_steps: int,
    default_guidance: float,
    default_timeout: int,
    default_backend: str | None = None,
    default_gpu_count: int = 0,
) -> DiffusionTarget:
    repo_name = f"{env_prefix}_REPO"
    family_name = f"{env_prefix}_FAMILY"
    backend_name = f"{env_prefix}_BACKEND"
    image_name = f"{env_prefix}_IMAGE"
    fake_name = f"{env_prefix}_FAKE"
    size_name = f"{env_prefix}_SIZE"
    steps_name = f"{env_prefix}_STEPS"
    guidance_name = f"{env_prefix}_GUIDANCE"
    timeout_name = f"{env_prefix}_TIMEOUT"
    gpu_count_name = f"{env_prefix}_GPU_COUNT"

    repo_id = os.environ.get(repo_name, default_repo).strip()
    family = os.environ.get(family_name, default_family).strip()
    inherited_backend = os.environ.get("KAMIWAZA_TEST_DIFFUSION_BACKEND", "auto")
    backend = os.environ.get(
        backend_name,
        default_backend if default_backend is not None else inherited_backend,
    ).strip()
    if not repo_id or not family:
        raise ValueError(f"{repo_name} and {family_name} must not be blank")
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"unsupported {backend_name}: {backend}")

    image = os.environ.get(image_name, "").strip()
    if not image and env_prefix != "KAMIWAZA_TEST_DIFFUSION":
        image = os.environ.get("KAMIWAZA_TEST_DIFFUSION_IMAGE", "").strip()

    return DiffusionTarget(
        case=case,
        repo_id=repo_id,
        family=family,
        backend=backend,
        image=image or None,
        fake=_env_bool(fake_name),
        size=_image_size(size_name, default_size),
        steps=_env_int(steps_name, default_steps, 1),
        guidance_scale=_env_float(guidance_name, default_guidance, 0.0),
        timeout_seconds=_env_int(timeout_name, default_timeout, 1),
        gpu_count=_env_int(gpu_count_name, default_gpu_count, 0),
    )


def load_diffusion_target() -> DiffusionTarget:
    """Load the portable real-model smoke target."""
    return _load_target(
        case="portable-sd15",
        env_prefix="KAMIWAZA_TEST_DIFFUSION",
        default_repo=DEFAULT_DIFFUSION_REPO,
        default_family="sd15",
        default_size="64x64",
        default_steps=2,
        default_guidance=1.0,
        default_timeout=900,
    )


def load_qwen_image_target() -> DiffusionTarget:
    """Load the required Qwen text-to-image acceptance target."""
    return _load_target(
        case="qwen-image",
        env_prefix="KAMIWAZA_TEST_QWEN_IMAGE",
        default_repo=DEFAULT_QWEN_IMAGE_REPO,
        default_family="qwen-image",
        default_size="512x512",
        default_steps=50,
        default_guidance=4.0,
        default_timeout=3600,
    )


def load_qwen_image_edit_target() -> DiffusionTarget:
    """Load the required Qwen masked-edit acceptance target."""
    return _load_target(
        case="qwen-image-edit-mask",
        env_prefix="KAMIWAZA_TEST_QWEN_IMAGE_EDIT",
        default_repo=DEFAULT_QWEN_IMAGE_EDIT_REPO,
        default_family="qwen-image-edit",
        default_size="512x512",
        default_steps=40,
        default_guidance=4.0,
        default_timeout=3600,
    )


def load_qwen_image_split_target() -> DiffusionTarget:
    """Load the two-NVIDIA-GPU DFloat11 split-tensor acceptance target."""
    return _load_target(
        case="qwen-image-dfloat11-split",
        env_prefix="KAMIWAZA_TEST_QWEN_IMAGE_SPLIT",
        default_repo=DEFAULT_QWEN_IMAGE_SPLIT_REPO,
        default_family="qwen-image",
        default_size="512x512",
        default_steps=30,
        default_guidance=4.0,
        default_timeout=3600,
        default_backend="nvidia",
        default_gpu_count=2,
    )
