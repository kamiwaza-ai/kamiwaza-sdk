from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from kamiwaza_sdk.schemas.models.external_endpoint import (
    AWSBedrockChatEndpoint,
    AWSBedrockIamCredential,
    AWSTranscribeCredential,
    AWSTranscribeEndpoint,
)
from kamiwaza_sdk.schemas.models.model import CreateModel
from kamiwaza_sdk.services.models.base import ModelService

pytestmark = pytest.mark.unit


class DummyClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def _request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.responses[(method, path)]


def test_list_models_passes_load_files_flag():
    responses = {
        ("GET", "/models/"): [
            {"id": str(uuid.uuid4()), "name": "demo", "repo_modelId": "mlx-community/Qwen3-4B-4bit"}
        ]
    }
    client = DummyClient(responses)
    service = ModelService(client)

    models = service.list_models(load_files=True)

    assert models[0].name == "demo"
    method, path, kwargs = client.calls[0]
    assert method == "GET"
    assert path == "/models/"
    assert kwargs["params"]["load_files"] is True


def test_get_model_fetches_by_uuid():
    model_id = str(uuid.uuid4())
    responses = {("GET", f"/models/{model_id}"): {"id": model_id, "name": "demo"}}
    client = DummyClient(responses)
    service = ModelService(client)

    model = service.get_model(model_id)

    assert model.id == uuid.UUID(model_id)
    assert client.calls[0][1] == f"/models/{model_id}"


def test_download_and_deploy_does_not_retry_terminal_deploy_failure(monkeypatch):
    """A deployment that reached a terminal FAILED/ERROR/MUST_REDOWNLOAD
    status will not succeed on an immediate redeploy; with deploy_model
    now blocking on readiness by default, retrying three times costs up
    to 3x the wait timeout. The typed error must propagate immediately."""
    import time as time_module
    from types import SimpleNamespace

    from kamiwaza_sdk.exceptions import DeploymentFailedError

    deploy_calls: list[dict] = []

    def failing_deploy(**kwargs):
        deploy_calls.append(kwargs)
        raise DeploymentFailedError(
            "Deployment entered failure status FAILED", status="FAILED"
        )

    client = DummyClient({})
    client.serving = SimpleNamespace(deploy_model=failing_deploy)
    service = ModelService(client)
    monkeypatch.setattr(service, "initiate_model_download", lambda *a, **k: None)
    monkeypatch.setattr(
        service,
        "get_model_by_repo_id",
        lambda repo_id: SimpleNamespace(id=uuid.uuid4(), m_files=[]),
    )
    monkeypatch.setattr(time_module, "sleep", lambda _seconds: None)

    with pytest.raises(DeploymentFailedError):
        service.download_and_deploy_model("org/model", wait_for_download=False)

    assert len(deploy_calls) == 1


def test_download_and_deploy_does_not_retry_deploy_wait_timeout(monkeypatch):
    """A hung-but-not-terminal deployment surfaces as a builtin
    TimeoutError from deploy_model(wait=True). Retrying redeploys on top
    of the still-in-flight deployment — worst case ~3x the 3600s wait and
    up to 3 orphaned deployments — and the blanket retry wrapped away the
    deployment id needed for cleanup. The timeout must propagate
    immediately with the deployment id intact."""
    import time as time_module
    from types import SimpleNamespace

    deploy_calls: list[dict] = []
    deployment_id = str(uuid.uuid4())

    def hung_deploy(**kwargs):
        deploy_calls.append(kwargs)
        timeout_error = TimeoutError(
            f"Timed out waiting for deployment {deployment_id} to reach DEPLOYED"
        )
        timeout_error.deployment_id = deployment_id
        raise timeout_error

    client = DummyClient({})
    client.serving = SimpleNamespace(deploy_model=hung_deploy)
    service = ModelService(client)
    monkeypatch.setattr(service, "initiate_model_download", lambda *a, **k: None)
    monkeypatch.setattr(
        service,
        "get_model_by_repo_id",
        lambda repo_id: SimpleNamespace(id=uuid.uuid4(), m_files=[]),
    )
    monkeypatch.setattr(time_module, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError) as exc_info:
        service.download_and_deploy_model("org/model", wait_for_download=False)

    assert len(deploy_calls) == 1
    assert exc_info.value.deployment_id == deployment_id


def test_download_and_deploy_retries_transient_deploy_error(monkeypatch):
    """Generic transient deploy errors (connection blips, 5xx) keep the
    existing backoff retries; only typed terminal/timeout errors are
    excluded from the retry loop."""
    import time as time_module
    from types import SimpleNamespace

    deploy_calls: list[dict] = []
    deployment_id = uuid.uuid4()

    def flaky_deploy(**kwargs):
        deploy_calls.append(kwargs)
        if len(deploy_calls) < 3:
            raise RuntimeError("connection reset")
        return deployment_id

    client = DummyClient({})
    client.serving = SimpleNamespace(deploy_model=flaky_deploy)
    service = ModelService(client)
    monkeypatch.setattr(service, "initiate_model_download", lambda *a, **k: None)
    monkeypatch.setattr(
        service,
        "get_model_by_repo_id",
        lambda repo_id: SimpleNamespace(id=uuid.uuid4(), m_files=[]),
    )
    monkeypatch.setattr(time_module, "sleep", lambda _seconds: None)

    result = service.download_and_deploy_model("org/model", wait_for_download=False)

    assert len(deploy_calls) == 3
    assert result["deployment_id"] == deployment_id


def test_initiate_model_download_requests_flagged_file_without_storage(monkeypatch):
    """download=True marks selection/request state; storage_location marks readiness."""
    from types import SimpleNamespace

    file_id = uuid.uuid4()
    target_file = SimpleNamespace(
        id=file_id,
        name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        download=True,
        storage_location=None,
    )
    model = SimpleNamespace(
        repo_modelId="org/model-GGUF",
        hub="hf",
        name="model-GGUF",
        m_files=[target_file],
    )
    client = DummyClient({("POST", "/models/download/"): {"result": True}})
    service = ModelService(client)
    monkeypatch.setattr(
        service,
        "search_models",
        lambda repo_id, load_files=True: [model],
    )

    result = service.initiate_model_download("org/model-GGUF", quantization="q4_k")

    assert result["download_request"] is not None
    assert client.calls[0][0:2] == ("POST", "/models/download/")
    assert client.calls[0][2]["json"]["files_to_download"] == [
        "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    ]


def test_initiate_model_download_requests_file_with_pending_redownload(monkeypatch):
    """A stale storage_location is not ready while dl_requested_at is set."""
    from types import SimpleNamespace

    file_id = uuid.uuid4()
    target_file = SimpleNamespace(
        id=file_id,
        name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        download=True,
        storage_location="oci://models/org-model/q4_k_m",
        is_downloading=False,
        dl_requested_at=datetime.now(timezone.utc),
    )
    model = SimpleNamespace(
        repo_modelId="org/model-GGUF",
        hub="hf",
        name="model-GGUF",
        m_files=[target_file],
    )
    client = DummyClient({("POST", "/models/download/"): {"result": True}})
    service = ModelService(client)
    monkeypatch.setattr(service, "search_models", lambda *a, **k: [model])

    result = service.initiate_model_download("org/model-GGUF", quantization="q4_k")

    assert result["download_request"] is not None
    assert client.calls[0][0:2] == ("POST", "/models/download/")
    assert client.calls[0][2]["json"]["files_to_download"] == [
        "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    ]


def test_initiate_model_download_skips_completed_storage_file(monkeypatch):
    from types import SimpleNamespace

    file_id = uuid.uuid4()
    target_file = SimpleNamespace(
        id=file_id,
        name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        download=True,
        storage_location="oci://models/org-model/q4_k_m",
        is_downloading=False,
        dl_requested_at=None,
    )
    model = SimpleNamespace(
        repo_modelId="org/model-GGUF",
        hub="hf",
        name="model-GGUF",
        m_files=[target_file],
    )
    client = DummyClient({})
    service = ModelService(client)
    monkeypatch.setattr(service, "search_models", lambda *a, **k: [model])

    result = service.initiate_model_download("org/model-GGUF", quantization="q4_k")

    assert result["download_request"] is None
    assert result["files"] == [target_file]
    assert client.calls == []


def test_get_model_download_status_treats_intent_without_storage_as_pending(monkeypatch):
    from types import SimpleNamespace

    target_file = SimpleNamespace(
        id=uuid.uuid4(),
        name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        download=True,
        storage_location=None,
        is_downloading=False,
        dl_requested_at=None,
        download_percentage=100,
    )
    model = SimpleNamespace(
        repo_modelId="org/model-GGUF",
        hub="hf",
        name="model-GGUF",
        m_files=[target_file],
    )
    service = ModelService(DummyClient({}))
    monkeypatch.setattr(service, "get_model_by_repo_id", lambda _repo_id: model)

    status = service.get_model_download_status("org/model-GGUF", quantization="q4_k")

    assert status["downloaded_files"] == []
    assert status["pending_files"] == [target_file]
    assert status["total_progress"] == 0
    assert status["all_downloaded"] is False


def test_get_model_download_status_treats_pending_redownload_as_pending(monkeypatch):
    from types import SimpleNamespace

    target_file = SimpleNamespace(
        id=uuid.uuid4(),
        name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        download=True,
        storage_location="oci://models/org-model/q4_k_m",
        is_downloading=False,
        dl_requested_at=datetime.now(timezone.utc),
        download_percentage=100,
    )
    model = SimpleNamespace(
        repo_modelId="org/model-GGUF",
        hub="hf",
        name="model-GGUF",
        m_files=[target_file],
    )
    service = ModelService(DummyClient({}))
    monkeypatch.setattr(service, "get_model_by_repo_id", lambda _repo_id: model)

    status = service.get_model_download_status("org/model-GGUF", quantization="q4_k")

    assert status["downloaded_files"] == []
    assert status["pending_files"] == [target_file]
    assert status["total_progress"] == 0
    assert status["all_downloaded"] is False


def test_get_model_download_status_reports_completed_storage_file(monkeypatch):
    from types import SimpleNamespace

    target_file = SimpleNamespace(
        id=uuid.uuid4(),
        name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        download=True,
        storage_location="oci://models/org-model/q4_k_m",
        is_downloading=False,
        dl_requested_at=None,
        download_percentage=100,
    )
    model = SimpleNamespace(
        repo_modelId="org/model-GGUF",
        hub="hf",
        name="model-GGUF",
        m_files=[target_file],
    )
    service = ModelService(DummyClient({}))
    monkeypatch.setattr(service, "get_model_by_repo_id", lambda _repo_id: model)

    status = service.get_model_download_status("org/model-GGUF", quantization="q4_k")

    assert status["downloaded_files"] == [target_file]
    assert status["pending_files"] == []
    assert status["total_progress"] == 100
    assert status["all_downloaded"] is True


def test_download_and_deploy_result_treats_intent_without_storage_as_pending(monkeypatch):
    import time as time_module
    from types import SimpleNamespace

    target_file = SimpleNamespace(
        id=uuid.uuid4(),
        name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        download=True,
        storage_location=None,
        is_downloading=False,
        dl_requested_at=None,
        download_percentage=100,
    )
    model = SimpleNamespace(
        repo_modelId="org/model-GGUF",
        hub="hf",
        name="model-GGUF",
        m_files=[target_file],
    )
    deployment_id = uuid.uuid4()
    client = DummyClient({})
    client.serving = SimpleNamespace(deploy_model=lambda **_kwargs: deployment_id)
    service = ModelService(client)
    monkeypatch.setattr(service, "initiate_model_download", lambda *a, **k: None)
    monkeypatch.setattr(service, "get_model_by_repo_id", lambda _repo_id: model)
    monkeypatch.setattr(time_module, "sleep", lambda _seconds: None)

    result = service.download_and_deploy_model(
        "org/model-GGUF", quantization="q4_k", wait_for_download=False
    )

    assert result["downloaded_files"] == []
    assert result["pending_files"] == [target_file]
    assert result["total_progress"] == 0
    assert result["all_downloaded"] is False
    assert result["deployment_id"] == deployment_id


def test_download_and_deploy_result_reports_completed_storage_file(monkeypatch):
    import time as time_module
    from types import SimpleNamespace

    target_file = SimpleNamespace(
        id=uuid.uuid4(),
        name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        download=True,
        storage_location="oci://models/org-model/q4_k_m",
        is_downloading=False,
        dl_requested_at=None,
        download_percentage=100,
    )
    model = SimpleNamespace(
        repo_modelId="org/model-GGUF",
        hub="hf",
        name="model-GGUF",
        m_files=[target_file],
    )
    deployment_id = uuid.uuid4()
    client = DummyClient({})
    client.serving = SimpleNamespace(deploy_model=lambda **_kwargs: deployment_id)
    service = ModelService(client)
    monkeypatch.setattr(service, "initiate_model_download", lambda *a, **k: None)
    monkeypatch.setattr(service, "get_model_by_repo_id", lambda _repo_id: model)
    monkeypatch.setattr(time_module, "sleep", lambda _seconds: None)

    result = service.download_and_deploy_model(
        "org/model-GGUF", quantization="q4_k", wait_for_download=False
    )

    assert result["downloaded_files"] == [target_file]
    assert result["pending_files"] == []
    assert result["total_progress"] == 100
    assert result["all_downloaded"] is True
    assert result["deployment_id"] == deployment_id


def test_create_and_delete_model():
    model_id = str(uuid.uuid4())
    responses = {
        ("POST", "/models/"): {"id": model_id, "name": "new"},
        ("DELETE", f"/models/{model_id}"): {"status": "deleted"},
    }
    client = DummyClient(responses)
    service = ModelService(client)

    payload = CreateModel(name="new", repo_modelId="mlx-community/Qwen3-4B-4bit", hub="hf")
    created = service.create_model(payload)
    assert created.id == uuid.UUID(model_id)

    service.delete_model(model_id)
    assert client.calls[1][0] == "DELETE"


def test_register_external_model_bedrock_builds_endpoint_blob():
    model_id = str(uuid.uuid4())
    responses = {("POST", "/models/"): {"id": model_id, "name": "bedrock-claude"}}
    client = DummyClient(responses)
    service = ModelService(client)

    created = service.register_external_model(
        name="bedrock-claude",
        endpoint=AWSBedrockChatEndpoint(
            region="us-east-1",
            model_id="anthropic.claude-3-sonnet-20240229-v1:0",
        ),
        credential=AWSBedrockIamCredential(
            aws_access_key_id="AKIAEXAMPLE",
            aws_secret_access_key="secret",
        ),
    )

    assert created.id == uuid.UUID(model_id)
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "/models/")
    # No rotation requested -> no query param sent.
    assert kwargs.get("params") is None

    # Mirrors the frontend wizard: engine_name + JSON-stringified external_endpoint
    # live under ``config`` (so the deploy path can resolve the engine), and
    # ``system_config`` is empty. aws_bedrock canonicalizes to external_chat.
    cfg = kwargs["json"]["default_config"]["config"]
    assert cfg["engine_name"] == "external_chat"
    assert kwargs["json"]["default_config"]["system_config"] == {}
    blob = json.loads(cfg["external_endpoint"])
    assert blob["protocol"] == "aws_bedrock"
    assert blob["region"] == "us-east-1"
    assert blob["model_id"] == "anthropic.claude-3-sonnet-20240229-v1:0"

    # Credential travels inline as a JSON string, never as a structured dict.
    cred = json.loads(blob["credential_secret"])
    assert cred == {
        "auth_type": "iam",
        "aws_access_key_id": "AKIAEXAMPLE",
        "aws_secret_access_key": "secret",
    }
    assert kwargs["json"]["default_config"]["default"] is True


def test_register_external_model_transcribe_force_replace_passes_query_param():
    responses = {("POST", "/models/"): {"id": str(uuid.uuid4()), "name": "transcribe"}}
    client = DummyClient(responses)
    service = ModelService(client)

    service.register_external_model(
        name="transcribe",
        endpoint=AWSTranscribeEndpoint(region="us-west-2", s3_bucket="my-bucket"),
        credential=AWSTranscribeCredential(
            aws_access_key_id="AKIAEXAMPLE",
            aws_secret_access_key="secret",
        ),
        force_replace_credentials=True,
    )

    _, _, kwargs = client.calls[0]
    assert kwargs["params"] == {"force_replace_credentials": True}
    cfg = kwargs["json"]["default_config"]["config"]
    assert cfg["engine_name"] == "external_transcribe"
    assert kwargs["json"]["default_config"]["system_config"] == {}
    blob = json.loads(cfg["external_endpoint"])
    assert blob["protocol"] == "aws_transcribe"
    assert blob["s3_bucket"] == "my-bucket"
    # Defaults are mirrored from the platform schema.
    assert blob["s3_prefix"] == "transcribe-jobs/"


def test_register_external_model_unknown_protocol_raises():
    from types import SimpleNamespace

    service = ModelService(DummyClient({}))
    # An endpoint whose blob carries a protocol with no engine mapping (only
    # reachable via an untyped/extra-allowed endpoint) must fail fast, not
    # silently register an undeployable model.
    endpoint = SimpleNamespace(
        model_dump=lambda exclude_none=True: {"protocol": "bogus", "region": "x"}
    )
    with pytest.raises(ValueError, match="Unsupported external endpoint protocol"):
        service.register_external_model(
            name="x", endpoint=endpoint, credential={"k": "v"}
        )


def test_register_external_model_accepts_raw_credential_forms():
    responses = {("POST", "/models/"): {"id": str(uuid.uuid4()), "name": "m"}}
    service = ModelService(DummyClient(responses))

    # dict credential is JSON-serialized verbatim
    assert service._serialize_credential({"api_key": "k"}) == '{"api_key": "k"}'
    # str credential passes through untouched (already-serialized)
    assert service._serialize_credential('{"api_key": "k"}') == '{"api_key": "k"}'
