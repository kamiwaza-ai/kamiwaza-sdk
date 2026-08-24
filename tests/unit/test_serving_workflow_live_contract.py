from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

INTEGRATION_TESTS = str(Path(__file__).parents[1] / "integration")
if INTEGRATION_TESTS not in sys.path:
    sys.path.insert(0, INTEGRATION_TESTS)

model_targets = importlib.import_module("model_targets")
serving_workflow = importlib.import_module("test_serving_workflow")

pytestmark = pytest.mark.unit


class _WorkflowBodyReached(Exception):
    pass


def _workflow_client(events: list[str]) -> Mock:
    client = Mock()
    client.models.get_model_configs.side_effect = lambda _model_id: [
        SimpleNamespace(id="default-config", default=True)
    ]
    client.models.create_model_config.side_effect = lambda _payload: (
        events.append("create:strip-config") or SimpleNamespace(id="strip-config")
    )
    client.models.delete_model_config.side_effect = lambda _config_id: events.append(
        "delete:strip-config"
    )

    def deploy_model(**kwargs: object) -> str:
        deployment = (
            "default-deployment"
            if kwargs["m_config_id"] == "default-config"
            else "strip-deployment"
        )
        events.append(f"deploy:{deployment}")
        return deployment

    client.serving.deploy_model.side_effect = deploy_model
    client.serving.wait_for_deployment.side_effect = lambda deployment_id, **_kwargs: (
        events.append(f"wait:{deployment_id}") or SimpleNamespace(instances=[object()])
    )
    client.serving.stream_deployment_logs.side_effect = (
        lambda deployment_id, **_kwargs: (
            events.append(f"logs:{deployment_id}") or iter(())
        )
    )
    client.serving.stop_deployment.side_effect = lambda *, deployment_id, force: (
        events.append(f"stop:{deployment_id}") or force
    )

    def get_openai_client(*, deployment_id: str) -> Mock:
        openai_client = Mock()
        content = "<think>reasoning</think>answer"
        if deployment_id == "strip-deployment":
            content = "answer"
        openai_client.chat.completions.create.side_effect = lambda **_kwargs: (
            events.append(f"infer:{deployment_id}")
            or SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )
        )
        return openai_client

    client.openai.get_client.side_effect = get_openai_client
    return client


def test_vllm_target_skips_before_model_or_config_work() -> None:
    client = object()
    ensure_model_ready = Mock(
        side_effect=AssertionError("vLLM must skip before model readiness")
    )
    target_model_file_id = Mock(
        side_effect=AssertionError("vLLM must skip before model-file selection")
    )
    target = model_targets.InferenceTarget(
        repo_id="Qwen/Qwen3-0.6B",
        engine_name="vllm",
    )

    with pytest.raises(
        pytest.skip.Exception,
        match="strip_thinking live coverage requires the MLX engine",
    ):
        serving_workflow.test_deploy_mlx_qwen_and_infer_with_strip_thinking(
            client,
            ensure_model_ready,
            target,
            target_model_file_id,
        )

    ensure_model_ready.assert_not_called()
    target_model_file_id.assert_not_called()


def test_mlx_target_reaches_workflow_body() -> None:
    client = object()
    ensure_model_ready = Mock(side_effect=_WorkflowBodyReached)
    target = model_targets.InferenceTarget(
        repo_id="mlx-community/Qwen3-4B-4bit",
        engine_name="mlx",
    )

    with pytest.raises(_WorkflowBodyReached):
        serving_workflow.test_deploy_mlx_qwen_and_infer_with_strip_thinking(
            client,
            ensure_model_ready,
            target,
            Mock(),
        )

    ensure_model_ready.assert_called_once_with(client)


def test_required_target_fails_when_model_has_no_config() -> None:
    client = Mock()
    client.models.get_model_configs.return_value = []
    target = model_targets.InferenceTarget(
        repo_id="mlx-community/Qwen3-4B-4bit",
        engine_name="mlx",
        required=True,
    )

    with pytest.raises(pytest.fail.Exception, match="No model configs available"):
        serving_workflow._default_config(
            client,
            SimpleNamespace(id="model-1"),
            target,
        )


def test_mlx_workflow_has_no_pre_body_capability_gate() -> None:
    workflow = serving_workflow.test_deploy_mlx_qwen_and_infer_with_strip_thinking
    marker_names = {marker.name for marker in getattr(workflow, "pytestmark", ())}

    assert "requires_deployable_model" not in marker_names
    assert "min_gpu_count" not in marker_names


def test_mlx_workflow_stops_default_before_deploying_strip_config() -> None:
    events: list[str] = []
    client = _workflow_client(events)
    target = model_targets.InferenceTarget(
        repo_id="mlx-community/Qwen3-4B-4bit",
        engine_name="mlx",
    )

    serving_workflow.test_deploy_mlx_qwen_and_infer_with_strip_thinking(
        client,
        lambda _client: SimpleNamespace(id=uuid4()),
        target,
        lambda _model, _quantization: "model-file",
    )

    assert events.index("stop:default-deployment") < events.index(
        "deploy:strip-deployment"
    )
    assert events.count("stop:default-deployment") == 1
    assert events.count("stop:strip-deployment") == 1
    assert events[-1] == "delete:strip-config"
