"""Required Qwen Image generation and masked-edit user-space acceptance."""

from __future__ import annotations

import base64
import hashlib

import pytest

from kamiwaza_sdk import KamiwazaClient
from tests.integration.capability_markers import ClusterCapabilitySnapshot
from tests.integration.diffusion_live_support import (
    OTTER_AIRPLANE_PROMPT,
    deployed_diffusion_target,
    generated_png_payloads,
    masked_edit_fixture,
    save_diffusion_evidence,
    unmasked_pixel_change_fraction,
)
from tests.integration.diffusion_targets import (
    MIN_QWEN_GPU_MEM_GB,
    load_qwen_image_edit_target,
    load_qwen_image_target,
    qwen_acceleration_available,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.diffusion,
    pytest.mark.withoutresponses,
]


def _require_qwen_acceleration(
    snapshot: ClusterCapabilitySnapshot | None,
) -> None:
    if qwen_acceleration_available(snapshot):
        return
    if snapshot is None:
        pytest.skip("Qwen diffusion requires a readable hardware inventory")
    pytest.skip(
        "Qwen diffusion requires Apple Silicon MPS or an NVIDIA/AMD accelerator "
        f"with at least {MIN_QWEN_GPU_MEM_GB:g} GiB on one GPU; "
        f"cluster reports {snapshot.gpu_count} GPU(s) and "
        f"vendors={sorted(snapshot.gpu_vendors)}, "
        f"memory_gb={list(snapshot.gpu_mem_gb) if snapshot.gpu_mem_gb else 'unknown'}"
    )


def test_qwen_image_generates_mollick_otter_benchmark(
    live_kamiwaza_session_client: KamiwazaClient,
    cluster_capability_snapshot: ClusterCapabilitySnapshot | None,
) -> None:
    _require_qwen_acceleration(cluster_capability_snapshot)
    target = load_qwen_image_target()
    with deployed_diffusion_target(live_kamiwaza_session_client, target) as live:
        response = live.openai_client.images.generate(
            model=live.served_model_id,
            prompt=OTTER_AIRPLANE_PROMPT,
            n=1,
            response_format="b64_json",
            size=target.size,  # type: ignore[arg-type]
            extra_body={
                "seed": 1729,
                "steps": target.steps,
                "guidance_scale": target.guidance_scale,
                "output_format": "png",
            },
        )
        metadata = response.model_extra or {}
        assert response.created is not None
        assert metadata["engine"] == "diffusion"
        assert metadata["family"] == "qwen-image"
        assert metadata["backend"] == target.backend
        payloads = generated_png_payloads(response, target.size)
        save_diffusion_evidence(
            live,
            case="qwen-image-otter-generation",
            prompt=OTTER_AIRPLANE_PROMPT,
            response=response,
            generated_payloads=payloads,
            request_controls={
                "n": 1,
                "seed": 1729,
                "response_format": "b64_json",
                "output_format": "png",
            },
        )


def test_qwen_image_edit_applies_a_real_mask(
    live_kamiwaza_session_client: KamiwazaClient,
    cluster_capability_snapshot: ClusterCapabilitySnapshot | None,
) -> None:
    _require_qwen_acceleration(cluster_capability_snapshot)
    target = load_qwen_image_edit_target()
    source_payload, mask_payload = masked_edit_fixture(target.size)
    prompt = (
        "Replace only the masked laptop screen with a bright Wi-Fi signal icon; "
        "preserve every unmasked pixel."
    )
    with deployed_diffusion_target(live_kamiwaza_session_client, target) as live:
        response = live.openai_client.images.generate(
            model=live.served_model_id,
            prompt=prompt,
            n=1,
            response_format="b64_json",
            size=target.size,  # type: ignore[arg-type]
            extra_body={
                "images": [base64.b64encode(source_payload).decode("ascii")],
                "mask": base64.b64encode(mask_payload).decode("ascii"),
                "seed": 1730,
                "steps": target.steps,
                "guidance_scale": target.guidance_scale,
                "output_format": "png",
            },
        )
        metadata = response.model_extra or {}
        assert response.created is not None
        assert metadata["engine"] == "diffusion"
        assert metadata["family"] == "qwen-image-edit"
        assert metadata["backend"] == target.backend
        payloads = generated_png_payloads(response, target.size)
        save_diffusion_evidence(
            live,
            case="qwen-image-edit-mask",
            prompt=prompt,
            response=response,
            generated_payloads=payloads,
            source_payload=source_payload,
            mask_payload=mask_payload,
            request_controls={
                "n": 1,
                "seed": 1730,
                "response_format": "b64_json",
                "output_format": "png",
                "source_sha256": hashlib.sha256(source_payload).hexdigest(),
                "mask_sha256": hashlib.sha256(mask_payload).hexdigest(),
            },
        )
        changed_fraction = unmasked_pixel_change_fraction(
            source_payload, mask_payload, payloads[0]
        )
        assert changed_fraction <= 0.01, (
            "Qwen Image Edit changed "
            f"{changed_fraction:.2%} of pixels outside the supplied mask"
        )
        if metadata.get("mask_applied") is not True:
            pytest.fail(
                "Qwen Image Edit returned an image but did not attest mask "
                "application; the runtime must not silently pass by ignoring this "
                "input",
                pytrace=False,
            )
