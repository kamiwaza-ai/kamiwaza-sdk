"""Live SDK proof for DiffusionEngine deployment and image generation."""

from __future__ import annotations

import base64
import struct
import time
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, Iterator
from uuid import UUID, uuid4

import pytest
from openai import BadRequestError, OpenAI

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.models.model import CreateModel, CreateModelConfig

from tests.integration.diffusion_targets import DiffusionTarget, load_diffusion_target

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.diffusion,
    pytest.mark.withoutresponses,
]


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
            name=f"sdk-diffusion-{suffix}",
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
    return client.models.create_model_config(
        CreateModelConfig(
            m_id=model_id,
            name=f"sdk-diffusion-{uuid4().hex[:8]}",
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


@pytest.fixture(scope="module")
def live_diffusion_deployment(
    live_kamiwaza_session_client: KamiwazaClient,
) -> Iterator[LiveDiffusionDeployment]:
    client = live_kamiwaza_session_client
    target = load_diffusion_target()
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


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError("Diffusion response is not a PNG")
    return struct.unpack(">II", payload[16:24])


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
        prompt="a small blue cube on a white background",
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
    expected_size = tuple(int(part) for part in live.target.size.split("x"))
    metadata = response.model_extra or {}
    assert response.created is not None
    assert metadata["engine"] == "diffusion"
    assert metadata["family"] == live.target.family
    assert metadata["backend"] == live.target.backend
    assert [image["seed"] for image in metadata["images"]] == [1729, 1730]
    assert len(response.data) == 2
    for generated in response.data:
        assert generated.b64_json
        payload = base64.b64decode(generated.b64_json, validate=True)
        assert _png_dimensions(payload) == expected_size


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
