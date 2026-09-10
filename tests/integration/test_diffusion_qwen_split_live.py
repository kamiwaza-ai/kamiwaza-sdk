"""Two-NVIDIA-GPU Qwen DFloat11 split-tensor user-space acceptance."""

from __future__ import annotations

import pytest

from kamiwaza_sdk import KamiwazaClient
from tests.integration.diffusion_live_support import (
    OTTER_AIRPLANE_PROMPT,
    deployed_diffusion_target,
    generated_png_payloads,
    save_diffusion_evidence,
)
from tests.integration.diffusion_targets import load_qwen_image_split_target

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.diffusion,
    pytest.mark.withoutresponses,
    pytest.mark.gpu_vendor("nvidia"),
    pytest.mark.min_gpu_count(2),
]


def test_qwen_dfloat11_uses_split_tensor_layout_on_two_nvidia_gpus(
    live_kamiwaza_session_client: KamiwazaClient,
) -> None:
    target = load_qwen_image_split_target()
    assert target.backend in {"nvidia", "cuda"}
    assert target.gpu_count >= 2
    with deployed_diffusion_target(live_kamiwaza_session_client, target) as live:
        response = live.openai_client.images.generate(
            model=live.served_model_id,
            prompt=OTTER_AIRPLANE_PROMPT,
            n=1,
            response_format="b64_json",
            size=target.size,  # type: ignore[arg-type]
            extra_body={
                "seed": 1731,
                "steps": target.steps,
                "guidance_scale": target.guidance_scale,
                "output_format": "png",
            },
        )
        metadata = response.model_extra or {}
        payloads = generated_png_payloads(response, target.size)
        save_diffusion_evidence(
            live,
            case="qwen-image-dfloat11-split",
            prompt=OTTER_AIRPLANE_PROMPT,
            response=response,
            generated_payloads=payloads,
            request_controls={
                "n": 1,
                "seed": 1731,
                "response_format": "b64_json",
                "output_format": "png",
                "required_gpu_count": 2,
            },
        )
        device_map = metadata.get("device_map")
        assert isinstance(device_map, dict), (
            "A successful two-GPU generation is not sufficient evidence of tensor "
            "splitting; the runtime must attest its effective device_map"
        )
        assert device_map.get("transformer_input") == "cuda:0"
        assert device_map.get("transformer_tail") == "cuda:1"
        assert device_map["transformer_input"] != device_map["transformer_tail"]
