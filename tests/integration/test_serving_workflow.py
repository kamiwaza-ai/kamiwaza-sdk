from __future__ import annotations

import uuid

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.models.model import CreateModelConfig

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

TEST_REPO_ID = "mlx-community/Qwen3-4B-4bit"
CONFIG_PREFIX = "sdk-m2"
WAIT_TIMEOUT = 600


def _sample_logs(client, deployment_id):
    try:
        lines = []
        for idx, line in enumerate(
            client.serving.stream_deployment_logs(
                deployment_id,
                poll_interval=0,
                max_empty_polls=2,
            )
        ):
            lines.append(line)
            if idx >= 4:
                break
        return lines
    except APIError:
        return []


def _ensure_model_cached(client, model):
    hub = getattr(model, "hub", None) or "hf"
    payload = {"model": model.repo_modelId, "hub": hub}
    try:
        client.post("/models/download/", json=payload)
    except APIError as exc:
        pytest.skip(f"Model download API unavailable: {exc}")


def test_deploy_qwen_and_infer_with_strip_thinking(live_kamiwaza_client):
    client = live_kamiwaza_client
    model = client.models.get_model_by_repo_id(TEST_REPO_ID)
    if not model:
        pytest.skip(f"{TEST_REPO_ID} not registered on live server")

    _ensure_model_cached(client, model)

    configs = client.models.get_model_configs(model.id)
    if not configs:
        pytest.skip("No model configs available for test model")
    default_config = next((c for c in configs if c.default), configs[0])

    unique_name = f"{CONFIG_PREFIX}-strip-{uuid.uuid4().hex[:6]}"
    strip_config = client.models.create_model_config(
        CreateModelConfig(
            m_id=model.id,
            name=unique_name,
            default=False,
            description="SDK integration test strip-thinking config",
            config={"strip_thinking": True},
            system_config={},
        )
    )

    deployments = []
    try:
        default_deployment = client.serving.deploy_model(
            model_id=str(model.id),
            m_config_id=default_config.id,
            lb_port=0,
            autoscaling=False,
            min_copies=1,
            starting_copies=1,
        )
        deployments.append(default_deployment)

        strip_deployment = client.serving.deploy_model(
            model_id=str(model.id),
            m_config_id=strip_config.id,
            lb_port=0,
            autoscaling=False,
            min_copies=1,
            starting_copies=1,
        )
        deployments.append(strip_deployment)

        default_details = client.serving.wait_for_deployment(
            default_deployment,
            poll_interval=5,
            timeout=WAIT_TIMEOUT,
        )
        strip_details = client.serving.wait_for_deployment(
            strip_deployment,
            poll_interval=5,
            timeout=WAIT_TIMEOUT,
        )

        assert default_details.instances, "Default deployment should report instances"
        assert strip_details.instances, "Strip deployment should report instances"

        _sample_logs(client, default_deployment)
        _sample_logs(client, strip_deployment)

        default_openai = client.openai.get_client(deployment_id=default_deployment)
        strip_openai = client.openai.get_client(deployment_id=strip_deployment)

        prompt = [
            {
                "role": "user",
                "content": "Think of 5 good names for a three-legged cat.",
            }
        ]

        default_resp = default_openai.chat.completions.create(model="kamiwaza", messages=prompt, temperature=0.6)
        strip_resp = strip_openai.chat.completions.create(model="kamiwaza", messages=prompt, temperature=0.6)

        assert default_resp.choices, "Default deployment returned no choices"
        assert strip_resp.choices, "Strip deployment returned no choices"

        default_text = default_resp.choices[0].message.content or ""
        strip_text = strip_resp.choices[0].message.content or ""

        default_contains = "<think>" in default_text
        strip_contains = "<think>" in strip_text
        if default_contains:
            assert not strip_contains, "Strip-thinking deployment should remove <think> blocks"
    finally:
        for dep in deployments:
            try:
                client.serving.stop_deployment(deployment_id=dep, force=True)
            except Exception:
                pass
        try:
            client.models.delete_model_config(strip_config.id)
        except Exception:
            pass
