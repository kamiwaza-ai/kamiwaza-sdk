"""Concrete SDK and Kubernetes seams for inference lifecycle execution."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from kamiwaza_sdk.validation.inference_runtime import DeploymentRequest
from kamiwaza_sdk.validation.models import RuntimeCluster
from kamiwaza_sdk.validation.sdk_inference_runtime import (
    KubectlRuntimeObserver,
    SdkInferenceCluster,
    SdkInferenceClusterFactory,
)

pytestmark = pytest.mark.contract


def _client() -> Mock:
    client = Mock()
    client.models.list_models.return_value = [
        SimpleNamespace(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            repo_modelId="Qwen/Qwen3-0.6B-GGUF",
            m_files=[
                SimpleNamespace(
                    id=UUID("22222222-2222-2222-2222-222222222222"),
                    name="qwen-q8_0.gguf",
                    storage_location="/models/qwen-q8_0.gguf",
                    is_downloading=False,
                    dl_requested_at=None,
                )
            ],
        )
    ]
    client.models.get_model_configs.return_value = [
        SimpleNamespace(id=UUID("33333333-3333-3333-3333-333333333333"), default=True)
    ]
    client.serving.deploy_model.return_value = UUID(
        "44444444-4444-4444-4444-444444444444"
    )
    client.serving.wait_for_deployment.return_value = SimpleNamespace(
        engine_name="llamacpp",
        instances=[SimpleNamespace(status="DEPLOYED")],
    )
    client.serving.list_deployments.return_value = []
    return client


def test_sdk_cluster_uses_exact_file_config_and_explicit_engine() -> None:
    client = _client()
    observer = Mock()
    cluster = SdkInferenceCluster(client, Path("/tmp/kubeconfig"), observer)

    model = cluster.discover("Qwen/Qwen3-0.6B-GGUF")
    downloaded = cluster.ensure_download("Qwen/Qwen3-0.6B-GGUF", "q8_0")
    configs = cluster.list_configs(model.model_id)
    deployment_id = cluster.deploy(
        DeploymentRequest(
            model_id=model.model_id,
            config_id=configs[0].config_id,
            model_file_id=downloaded.files[0].file_id,
            engine="llamacpp",
            runtime_profile="product-default",
        )
    )
    ready = cluster.wait_ready(deployment_id)

    client.models.initiate_model_download.assert_not_called()
    client.serving.deploy_model.assert_called_once_with(
        model_id="11111111-1111-1111-1111-111111111111",
        m_config_id="33333333-3333-3333-3333-333333333333",
        m_file_id="22222222-2222-2222-2222-222222222222",
        engine_name="llamacpp",
        lb_port=0,
        autoscaling=False,
        min_copies=1,
        starting_copies=1,
        wait=False,
    )
    assert deployment_id == "44444444-4444-4444-4444-444444444444"
    assert ready.engine == "llamacpp"
    assert ready.instance_count == 1


def test_discovery_accepts_exact_remote_result_before_platform_registration() -> None:
    client = _client()
    client.models.list_models.return_value = []
    client.models.search_models.return_value = [
        SimpleNamespace(
            id=None,
            repo_modelId="Qwen/Qwen3-0.6B-GGUF",
            m_files=[],
        )
    ]
    cluster = SdkInferenceCluster(client, Path("/tmp/kubeconfig"), Mock())

    model = cluster.discover("Qwen/Qwen3-0.6B-GGUF")

    assert model.repository == "Qwen/Qwen3-0.6B-GGUF"
    assert model.model_id is None


def test_download_waits_for_exact_quantized_file_to_become_ready() -> None:
    client = _client()
    pending = SimpleNamespace(
        id=UUID("22222222-2222-2222-2222-222222222222"),
        name="qwen-q8_0.gguf",
        storage_location=None,
        is_downloading=True,
        dl_requested_at=None,
    )
    ready_model = client.models.list_models.return_value[0]
    pending_model = SimpleNamespace(
        id=ready_model.id,
        repo_modelId=ready_model.repo_modelId,
        m_files=[pending],
    )
    client.models.list_models.side_effect = [[pending_model], [ready_model]]
    cluster = SdkInferenceCluster(client, Path("/tmp/kubeconfig"), Mock())

    model = cluster.ensure_download("Qwen/Qwen3-0.6B-GGUF", "q8_0")

    client.models.initiate_model_download.assert_called_once_with(
        "Qwen/Qwen3-0.6B-GGUF", quantization="q8_0"
    )
    client.models.wait_for_download.assert_called_once_with(
        "Qwen/Qwen3-0.6B-GGUF", timeout=900, show_progress=False
    )
    assert model.files[0].ready is True


def test_sdk_cluster_chat_targets_exact_deployment_and_returns_text() -> None:
    client = _client()
    openai_client = Mock()
    openai_client.models.list.return_value = SimpleNamespace(
        data=[SimpleNamespace(id="served-qwen")]
    )
    openai_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))]
    )
    client.openai.get_client.return_value = openai_client
    cluster = SdkInferenceCluster(client, Path("/tmp/kubeconfig"), Mock())
    messages = ({"role": "user", "content": "hello"},)

    content = cluster.chat("44444444-4444-4444-4444-444444444444", messages)

    client.openai.get_client.assert_called_once_with(
        deployment_id="44444444-4444-4444-4444-444444444444"
    )
    openai_client.chat.completions.create.assert_called_once_with(
        model="served-qwen",
        messages=list(messages),
        temperature=0.0,
    )
    openai_client.close.assert_called_once_with()
    assert content == "hello"


def test_kubectl_observer_binds_full_deployment_label_and_actual_digest() -> None:
    calls: list[tuple[str, ...]] = []
    payload = {
        "items": [
            {
                "metadata": {"name": "llamacpp-abcd-pod"},
                "spec": {
                    "containers": [
                        {
                            "name": "llamacpp",
                            "image": "ghcr.io/kamiwaza/llamacpp:candidate",
                            "args": ["--model", "/models/qwen.gguf", "-ngl", "999"],
                        }
                    ]
                },
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {
                            "name": "llamacpp",
                            "imageID": "ghcr.io/kamiwaza/llamacpp@sha256:" + "a" * 64,
                        }
                    ],
                },
            }
        ]
    }

    def runner(command: tuple[str, ...]) -> str:
        calls.append(command)
        return json.dumps(payload)

    observer = KubectlRuntimeObserver(Path("/secure/evo.kubeconfig"), runner)

    result = observer.observe("44444444-4444-4444-4444-444444444444", "llamacpp")

    assert calls == [
        (
            "kubectl",
            "--kubeconfig",
            "/secure/evo.kubeconfig",
            "get",
            "pods",
            "--namespace",
            "kamiwaza",
            "--selector",
            "kamiwaza.io/deployment-id=44444444-4444-4444-4444-444444444444",
            "--output",
            "json",
        )
    ]
    assert result.image_digest == "sha256:" + "a" * 64
    assert result.effective_args[-2:] == ("-ngl", "999")


def test_kubectl_observer_rejects_inconsistent_replica_commands() -> None:
    first = {
        "metadata": {"name": "vllm-a"},
        "spec": {"containers": [{"name": "vllm", "args": ["serve", "a"]}]},
        "status": {
            "phase": "Running",
            "containerStatuses": [{"name": "vllm", "imageID": "sha256:" + "a" * 64}],
        },
    }
    second = json.loads(json.dumps(first))
    second["metadata"]["name"] = "vllm-b"
    second["spec"]["containers"][0]["args"] = ["serve", "b"]
    observer = KubectlRuntimeObserver(
        Path("/secure/evo.kubeconfig"),
        lambda _command: json.dumps({"items": [first, second]}),
    )

    with pytest.raises(RuntimeError, match="inconsistent runtime observations"):
        observer.observe("44444444-4444-4444-4444-444444444444", "vllm")


def test_kubectl_observer_redacts_secret_bearing_runtime_arguments() -> None:
    payload = {
        "items": [
            {
                "metadata": {"name": "vllm-a"},
                "spec": {
                    "containers": [
                        {
                            "name": "vllm",
                            "args": [
                                "vllm",
                                "serve",
                                "--api-key",
                                "top-secret",
                                "--hf-token=also-secret",
                                "--tokenizer",
                                "Qwen/tokenizer",
                            ],
                        }
                    ]
                },
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {"name": "vllm", "imageID": "sha256:" + "a" * 64}
                    ],
                },
            }
        ]
    }
    observer = KubectlRuntimeObserver(
        Path("/secure/evo.kubeconfig"),
        lambda _command: json.dumps(payload),
    )

    result = observer.observe("44444444-4444-4444-4444-444444444444", "vllm")

    assert "top-secret" not in result.effective_args
    assert "also-secret" not in " ".join(result.effective_args)
    assert result.effective_args == (
        "vllm",
        "serve",
        "--api-key",
        "<redacted>",
        "--hf-token=<redacted>",
        "--tokenizer",
        "Qwen/tokenizer",
    )


def test_factory_materializes_file_references_without_exposing_token(
    tmp_path: Path,
) -> None:
    token = tmp_path / "admin.pat"
    token.write_text("top-secret-token\n")
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n")
    built: list[tuple[str, str]] = []

    def client_builder(base_url: str, api_key: str) -> Mock:
        built.append((base_url, api_key))
        return _client()

    factory = SdkInferenceClusterFactory(client_builder=client_builder)
    runtime = RuntimeCluster(
        id="evo-x2-2",
        base_url="https://evo-x2-2.test/api",
        api_key_ref=f"file://{token}",
        kubeconfig_ref=f"file://{kubeconfig}",
    )

    cluster = factory(runtime)

    assert built == [("https://evo-x2-2.test/api", "top-secret-token")]
    assert cluster.kubeconfig_path == kubeconfig


def test_factory_rejects_unmaterialized_secret_reference() -> None:
    factory = SdkInferenceClusterFactory(client_builder=lambda _url, _key: _client())
    runtime = RuntimeCluster(
        id="evo-x2-2",
        base_url="https://evo-x2-2.test/api",
        api_key_ref="secret://evo-x2-2/admin-pat",
        kubeconfig_ref="file:///run/secrets/evo.kubeconfig",
    )

    with pytest.raises(RuntimeError) as error:
        factory(runtime)

    assert "evo-x2-2/admin-pat" not in str(error.value)
