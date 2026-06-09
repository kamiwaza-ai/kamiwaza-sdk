from __future__ import annotations

import uuid

import pytest

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
