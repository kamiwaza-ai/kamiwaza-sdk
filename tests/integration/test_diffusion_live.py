"""Live SDK proof for DiffusionEngine deployment and image generation."""

from __future__ import annotations

from typing import Iterator

import pytest
from openai import BadRequestError

from kamiwaza_sdk import KamiwazaClient
from tests.integration.diffusion_live_support import (
    OTTER_AIRPLANE_PROMPT,
    LiveDiffusionDeployment,
    deployed_diffusion_target,
    generated_png_payloads,
    save_diffusion_evidence,
)
from tests.integration.diffusion_targets import load_diffusion_target

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.diffusion,
    pytest.mark.withoutresponses,
]


@pytest.fixture(scope="module")
def live_diffusion_deployment(
    live_kamiwaza_session_client: KamiwazaClient,
) -> Iterator[LiveDiffusionDeployment]:
    target = load_diffusion_target()
    with deployed_diffusion_target(live_kamiwaza_session_client, target) as live:
        yield live


def test_diffusion_deployment_routes_through_sdk(
    live_diffusion_deployment: LiveDiffusionDeployment,
) -> None:
    live = live_diffusion_deployment
    deployment = live.client.serving.get_deployment(live.deployment_id)
    assert deployment.status == "DEPLOYED"
    assert deployment.engine_name == "diffusion"
    assert deployment.instances
    assert all(instance.status == "DEPLOYED" for instance in deployment.instances)
    assert str(live.openai_client.base_url).rstrip("/").endswith("/v1")


def test_diffusion_generates_base64_pngs_with_extension_controls(
    live_diffusion_deployment: LiveDiffusionDeployment,
) -> None:
    live = live_diffusion_deployment
    response = live.openai_client.images.generate(
        model=live.served_model_id,
        prompt=OTTER_AIRPLANE_PROMPT,
        n=2,
        response_format="b64_json",
        size=live.target.size,  # type: ignore[arg-type]
        extra_body={
            "seed": 1729,
            "steps": live.target.steps,
            "guidance_scale": live.target.guidance_scale,
            "output_format": "png",
        },
    )
    metadata = response.model_extra or {}
    assert response.created is not None
    assert metadata["engine"] == "diffusion"
    assert metadata["family"] == live.target.family
    assert metadata["backend"] == live.target.backend
    assert [image["seed"] for image in metadata["images"]] == [1729, 1730]
    assert len(response.data) == 2
    payloads = generated_png_payloads(response, live.target.size)
    save_diffusion_evidence(
        live,
        case="portable-sd15-generation",
        prompt=OTTER_AIRPLANE_PROMPT,
        response=response,
        generated_payloads=payloads,
        request_controls={
            "n": 2,
            "seed": 1729,
            "response_format": "b64_json",
            "output_format": "png",
        },
    )


def test_diffusion_validates_source_images_through_sdk(
    live_diffusion_deployment: LiveDiffusionDeployment,
) -> None:
    live = live_diffusion_deployment
    with pytest.raises(BadRequestError, match="valid base64") as exc_info:
        live.openai_client.images.generate(
            model=live.served_model_id,
            prompt="edit the supplied image",
            response_format="b64_json",
            size=live.target.size,  # type: ignore[arg-type]
            extra_body={"images": ["not-valid-base64"]},
        )
    assert exc_info.value.status_code == 400


def test_diffusion_rejects_unsupported_url_responses(
    live_diffusion_deployment: LiveDiffusionDeployment,
) -> None:
    with pytest.raises(BadRequestError) as exc_info:
        live_diffusion_deployment.openai_client.images.generate(
            model=live_diffusion_deployment.served_model_id,
            prompt="a validation-only image request",
            response_format="url",
            size=live_diffusion_deployment.target.size,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 400
