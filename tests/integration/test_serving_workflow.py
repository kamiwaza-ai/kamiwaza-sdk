from __future__ import annotations

import uuid

import pytest
from model_targets import InferenceTarget

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.models.model import CreateModelConfig

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

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


def _wait_and_infer(client, deployment_id):
    details = client.serving.wait_for_deployment(
        deployment_id,
        poll_interval=5,
        timeout=WAIT_TIMEOUT,
    )
    assert details.instances, "Deployment should report instances"
    _sample_logs(client, deployment_id)

    openai_client = client.openai.get_client(deployment_id=deployment_id)
    response = openai_client.chat.completions.create(
        model="kamiwaza",
        messages=[
            {
                "role": "user",
                "content": "Think of 5 good names for a three-legged cat.",
            }
        ],
        temperature=0.6,
    )
    assert response.choices, "Deployment returned no choices"
    return response.choices[0].message.content or ""


@pytest.mark.requires_deployable_model
def test_deploy_qwen_and_infer_with_strip_thinking(
    live_kamiwaza_client,
    ensure_deployable_model_ready,
    deployable_model_target: InferenceTarget,
    target_model_file_id,
):
    client = live_kamiwaza_client
    model = ensure_deployable_model_ready(client)

    model_file_id = target_model_file_id(model, deployable_model_target.quantization)

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
            engine_name=deployable_model_target.engine_name,
            m_file_id=model_file_id,
            wait=False,
        )
        deployments.append(default_deployment)

        default_text = _wait_and_infer(client, default_deployment)
        if "<think>" not in default_text:
            pytest.skip(
                "target emits no <think>; strip_thinking unverifiable on this host"
            )
        stopped = client.serving.stop_deployment(
            deployment_id=default_deployment, force=True
        )
        assert stopped, "Default deployment should stop before strip deployment"
        deployments.remove(default_deployment)

        strip_deployment = client.serving.deploy_model(
            model_id=str(model.id),
            m_config_id=strip_config.id,
            lb_port=0,
            autoscaling=False,
            min_copies=1,
            starting_copies=1,
            engine_name=deployable_model_target.engine_name,
            m_file_id=model_file_id,
            wait=False,
        )
        deployments.append(strip_deployment)

        strip_text = _wait_and_infer(client, strip_deployment)
        assert (
            "<think>" not in strip_text
        ), "Strip-thinking deployment should remove <think> blocks"
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
