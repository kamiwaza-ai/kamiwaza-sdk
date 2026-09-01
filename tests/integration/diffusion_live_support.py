"""Shared deployment and evidence helpers for live DiffusionEngine UAT."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import struct
import time
import zlib
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from uuid import UUID, uuid4

from openai import OpenAI

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.models.model import CreateModel, CreateModelConfig
from tests.integration.diffusion_targets import DiffusionTarget

OTTER_AIRPLANE_PROMPT = "otter on a plane using wifi"


@dataclass(frozen=True)
class LiveDiffusionDeployment:
    client: KamiwazaClient
    target: DiffusionTarget
    deployment_id: UUID
    served_model_id: str
    openai_client: OpenAI


def _find_or_create_model(
    client: KamiwazaClient, target: DiffusionTarget
) -> tuple[Any, bool]:
    existing = client.models.get_model_by_repo_id(target.repo_id)
    if existing is not None:
        return existing, False
    suffix = uuid4().hex[:8]
    model = client.models.create_model(
        CreateModel(
            repo_modelId=target.repo_id,
            modelfamily=target.family,
            purpose="image_generation",
            name=f"sdk-diffusion-{target.case}-{suffix}",
            hub="HubsHf",
            description="Harness-owned SDK DiffusionEngine integration target",
        )
    )
    if model.id is None:
        raise AssertionError("Created diffusion model has no id")
    return model, True


def _create_config(
    client: KamiwazaClient, model_id: UUID, target: DiffusionTarget
) -> Any:
    config: dict[str, Any] = {
        "model_purpose": "image_generation",
        "model_path": target.model_path,
        "model_name": target.repo_id,
        "diffusion_family": target.family,
        "diffusion_backend": target.backend,
        "diffusion_fake_engine": target.fake,
        "diffusion_lazy_load": True,
    }
    if target.image:
        config["diffusion_image"] = target.image
    if target.gpu_count:
        config["gpu_count"] = target.gpu_count
    return client.models.create_model_config(
        CreateModelConfig(
            m_id=model_id,
            name=f"sdk-diffusion-{target.case}-{uuid4().hex[:8]}",
            default=False,
            description="Harness-owned SDK diffusion live config",
            config=config,
            system_config={"engine_name": "diffusion"},
        )
    )


def _openai_client_when_routed(
    client: KamiwazaClient, deployment_id: UUID, timeout_seconds: int
) -> OpenAI:
    deadline = time.monotonic() + min(timeout_seconds, 60)
    while True:
        try:
            return client.openai.get_client(deployment_id=deployment_id)
        except ValueError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)


@contextmanager
def deployed_diffusion_target(
    client: KamiwazaClient, target: DiffusionTarget
) -> Iterator[LiveDiffusionDeployment]:
    """Deploy one target and always remove harness-owned platform state."""
    with ExitStack() as cleanup:
        model, created_model = _find_or_create_model(client, target)
        if model.id is None:
            raise AssertionError("Diffusion target model has no id")
        if created_model:
            cleanup.callback(client.models.delete_model, model.id)
        config = _create_config(client, model.id, target)
        cleanup.callback(client.models.delete_model_config, config.id)
        deployment_id = client.serving.deploy_model(
            model_id=model.id,
            m_config_id=config.id,
            wait=False,
            min_copies=1,
            starting_copies=1,
            autoscaling=False,
        )
        if not isinstance(deployment_id, UUID):
            raise AssertionError("Diffusion deployment did not return an id")
        cleanup.callback(
            client.serving.stop_deployment,
            deployment_id=deployment_id,
            force=True,
        )
        client.serving.wait_deployment_ready(
            deployment_id,
            timeout_seconds=target.timeout_seconds,
            poll_interval_seconds=2,
        )
        openai_client = _openai_client_when_routed(
            client, deployment_id, target.timeout_seconds
        ).with_options(timeout=target.timeout_seconds)
        served_models = openai_client.models.list().data
        if len(served_models) != 1:
            raise AssertionError("Diffusion deployment did not expose one model")
        yield LiveDiffusionDeployment(
            client=client,
            target=target,
            deployment_id=deployment_id,
            served_model_id=served_models[0].id,
            openai_client=openai_client,
        )


def png_dimensions(payload: bytes) -> tuple[int, int]:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError("Diffusion response is not a PNG")
    return struct.unpack(">II", payload[16:24])


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _png_rgb_bytes(payload: bytes) -> tuple[int, int, bytes]:
    """Decode non-interlaced 8-bit RGB/RGBA PNGs without an image dependency."""
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError("Diffusion response is not a PNG")
    offset = 8
    width = height = color_type = interlace = None
    compressed = bytearray()
    while offset < len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        chunk = payload[offset + 8 : offset + 8 + length]
        offset += length + 12
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
            if bit_depth != 8 or color_type not in {2, 6} or interlace != 0:
                raise AssertionError(
                    "Mask validation requires a non-interlaced 8-bit RGB/RGBA PNG"
                )
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            break
    if width is None or height is None or color_type is None or interlace is None:
        raise AssertionError("PNG is missing its IHDR chunk")

    channels = 3 if color_type == 2 else 4
    stride = width * channels
    encoded = zlib.decompress(bytes(compressed))
    if len(encoded) != height * (stride + 1):
        raise AssertionError("PNG scanline length does not match its dimensions")
    previous = bytearray(stride)
    rgb = bytearray()
    for row_index in range(height):
        start = row_index * (stride + 1)
        filter_type = encoded[start]
        row = bytearray(encoded[start + 1 : start + 1 + stride])
        for index, value in enumerate(row):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                row[index] = (value + left) & 0xFF
            elif filter_type == 2:
                row[index] = (value + above) & 0xFF
            elif filter_type == 3:
                row[index] = (value + ((left + above) // 2)) & 0xFF
            elif filter_type == 4:
                row[index] = (value + _paeth_predictor(left, above, upper_left)) & 0xFF
            elif filter_type != 0:
                raise AssertionError(f"Unsupported PNG filter type: {filter_type}")
        if channels == 3:
            rgb.extend(row)
        else:
            for index in range(0, len(row), 4):
                rgb.extend(row[index : index + 3])
        previous = row
    return width, height, bytes(rgb)


def unmasked_pixel_change_fraction(
    source_payload: bytes, mask_payload: bytes, generated_payload: bytes
) -> float:
    """Return the fraction of black-mask pixels changed by an edit response."""
    source_width, source_height, source = _png_rgb_bytes(source_payload)
    mask_width, mask_height, mask = _png_rgb_bytes(mask_payload)
    generated_width, generated_height, generated = _png_rgb_bytes(generated_payload)
    dimensions = {
        (source_width, source_height),
        (mask_width, mask_height),
        (generated_width, generated_height),
    }
    if len(dimensions) != 1:
        raise AssertionError("Source, mask, and generated PNG dimensions must match")

    unmasked = changed = 0
    for index in range(0, len(source), 3):
        if any(mask[index : index + 3]):
            continue
        unmasked += 1
        changed += source[index : index + 3] != generated[index : index + 3]
    if not unmasked:
        raise AssertionError("Mask fixture has no unmasked pixels")
    return changed / unmasked


def generated_png_payloads(response: Any, expected_size: str) -> list[bytes]:
    dimensions = tuple(int(part) for part in expected_size.split("x"))
    payloads: list[bytes] = []
    for generated in response.data:
        if not generated.b64_json:
            raise AssertionError("Diffusion response has no base64 image payload")
        payload = base64.b64decode(generated.b64_json, validate=True)
        if png_dimensions(payload) != dimensions:
            raise AssertionError(
                f"Diffusion PNG dimensions {png_dimensions(payload)} != {dimensions}"
            )
        payloads.append(payload)
    return payloads


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)
    )


def _rgb_png(width: int, height: int, pixels: bytes) -> bytes:
    expected = width * height * 3
    if len(pixels) != expected:
        raise ValueError(f"expected {expected} RGB bytes, received {len(pixels)}")
    stride = width * 3
    scanlines = b"".join(
        b"\x00" + pixels[offset : offset + stride]
        for offset in range(0, len(pixels), stride)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(scanlines, level=9))
        + _png_chunk(b"IEND", b"")
    )


def masked_edit_fixture(size: str) -> tuple[bytes, bytes]:
    """Build a deterministic airplane-screen source and center-screen mask."""
    width, height = (int(part) for part in size.split("x"))
    left, right = width * 3 // 10, width * 7 // 10
    top, bottom = height * 3 // 10, height * 7 // 10
    source = bytearray()
    mask = bytearray()
    for y in range(height):
        for x in range(width):
            inside = left <= x < right and top <= y < bottom
            border = (
                left - 6 <= x < right + 6 and top - 6 <= y < bottom + 6 and not inside
            )
            if inside:
                source.extend((35, 42, 52))
            elif border:
                source.extend((205, 210, 218))
            elif x < width // 4 and height // 5 < y < height * 4 // 5:
                source.extend((95, 175, 215))
            else:
                source.extend((28, 83, 112))
            mask.extend((255, 255, 255) if inside else (0, 0, 0))
    return _rgb_png(width, height, bytes(source)), _rgb_png(width, height, bytes(mask))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "diffusion"


def evidence_root() -> Path:
    configured = os.environ.get("KAMIWAZA_TEST_DIFFUSION_ARTIFACT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path("/tmp") / f"kzsdk-diffusion-evidence-{os.getpid()}"


def _artifact_record(path: Path, payload: bytes, *, role: str) -> dict[str, Any]:
    width, height = png_dimensions(payload)
    return {
        "file": path.name,
        "role": role,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "width": width,
        "height": height,
    }


def _safe_response_metadata(response: Any) -> dict[str, Any]:
    metadata = dict(response.model_extra or {})
    legacy_images = metadata.pop("images", [])
    if isinstance(legacy_images, Sequence) and not isinstance(
        legacy_images, (str, bytes)
    ):
        metadata["images"] = [
            {
                key: value
                for key, value in dict(image).items()
                if key not in {"b64_json", "url"}
            }
            for image in legacy_images
            if isinstance(image, Mapping)
        ]
    return metadata


def save_diffusion_evidence(
    live: LiveDiffusionDeployment,
    *,
    case: str,
    prompt: str,
    response: Any,
    generated_payloads: Sequence[bytes],
    request_controls: Mapping[str, Any],
    source_payload: bytes | None = None,
    mask_payload: bytes | None = None,
) -> Path:
    """Persist generated PNGs plus a redacted, hash-addressed JSON manifest."""
    case_dir = evidence_root() / (
        f"{_slug(case)}-{str(live.deployment_id).split('-', maxsplit=1)[0]}"
    )
    case_dir.mkdir(parents=True, exist_ok=False)
    artifacts: list[dict[str, Any]] = []

    for index, payload in enumerate(generated_payloads):
        path = case_dir / f"generated-{index:02d}.png"
        path.write_bytes(payload)
        artifacts.append(_artifact_record(path, payload, role="generated"))
    for name, role, payload in (
        ("source.png", "source", source_payload),
        ("mask.png", "mask", mask_payload),
    ):
        if payload is None:
            continue
        path = case_dir / name
        path.write_bytes(payload)
        artifacts.append(_artifact_record(path, payload, role=role))

    manifest = {
        "schema": "kamiwaza-sdk-diffusion-evidence.v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "case": case,
        "deployment_id": str(live.deployment_id),
        "target": {
            "repo_id": live.target.repo_id,
            "family": live.target.family,
            "backend": live.target.backend,
            "runtime_image": live.target.image,
            "gpu_count": live.target.gpu_count,
            "fake": live.target.fake,
        },
        "request": {
            "prompt": prompt,
            "size": live.target.size,
            "steps": live.target.steps,
            "guidance_scale": live.target.guidance_scale,
            **dict(request_controls),
        },
        "response": {
            "created": response.created,
            "metadata": _safe_response_metadata(response),
        },
        "artifacts": artifacts,
    }
    manifest_path = case_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Diffusion evidence: {manifest_path}")
    return manifest_path
