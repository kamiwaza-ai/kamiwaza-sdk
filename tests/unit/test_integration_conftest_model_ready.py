from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit

CONFTEST_PATH = (
    Path(__file__).resolve().parents[1] / "integration" / "conftest.py"
)


@pytest.fixture(scope="module")
def integration_conftest():
    spec = importlib.util.spec_from_file_location(
        "_integration_conftest_model_ready_under_test", CONFTEST_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _EmbeddingProbeClient:
    class Embedding:
        def get_providers(self) -> list[str]:
            return ["sentence_transformers"]

    class Serving:
        def __init__(self, *, fail_stop: bool = False) -> None:
            self.stopped: list[tuple[str, bool]] = []
            self._fail_stop = fail_stop

        def stop_deployment(self, *, deployment_id: str, force: bool) -> None:
            if self._fail_stop:
                raise RuntimeError("stop failed")
            self.stopped.append((deployment_id, force))

    def __init__(self, integration_conftest, *, fail_stop: bool = False) -> None:
        self.embedding = self.Embedding()
        self.serving = self.Serving(fail_stop=fail_stop)
        self._integration_conftest = integration_conftest

    def post(self, *_args: object, **_kwargs: object) -> object:
        raise self._integration_conftest.APIError(
            "runtime unavailable",
            status_code=500,
            response_data={"detail": "runtime unavailable"},
        )


def test_ensure_repo_ready_fast_path_loads_model_files(integration_conftest) -> None:
    ready_file = SimpleNamespace(
        name="model.safetensors",
        storage_location="oci://models/org-model/model.safetensors",
        is_downloading=False,
        dl_requested_at=None,
    )
    files_loaded_model = SimpleNamespace(
        repo_modelId="org/model",
        m_files=[ready_file],
    )

    class Models:
        def __init__(self) -> None:
            self.list_calls: list[bool] = []

        def list_models(self, *, load_files: bool = False) -> list[Any]:
            self.list_calls.append(load_files)
            return [files_loaded_model]

        def get_model_by_repo_id(self, _repo_id: str) -> Any:
            pytest.fail("ensure_repo_ready must not use files-unloaded model fetch")

        def initiate_model_download(self, *_args: Any, **_kwargs: Any) -> None:
            pytest.fail("ready files should fast-path without re-queueing download")

        def wait_for_download(self, *_args: Any, **_kwargs: Any) -> None:
            pytest.fail("ready files should not wait for a new download")

    client = SimpleNamespace(models=Models())
    ensure_repo_ready = integration_conftest.ensure_repo_ready.__wrapped__()

    model = ensure_repo_ready(client, "org/model")

    assert model is files_loaded_model
    assert client.models.list_calls == [True]


def test_ensure_repo_ready_post_download_poll_loads_model_files(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready_file = SimpleNamespace(
        name="model.safetensors",
        storage_location="oci://models/org-model/model.safetensors",
        is_downloading=False,
        dl_requested_at=None,
    )
    files_loaded_model = SimpleNamespace(
        repo_modelId="org/model",
        m_files=[ready_file],
    )

    class Models:
        def __init__(self) -> None:
            self.list_calls: list[bool] = []
            self.download_calls = 0
            self.wait_calls = 0

        def list_models(self, *, load_files: bool = False) -> list[Any]:
            self.list_calls.append(load_files)
            if len(self.list_calls) == 1:
                return []
            return [files_loaded_model]

        def get_model_by_repo_id(self, _repo_id: str) -> Any:
            pytest.fail("ensure_repo_ready must not use files-unloaded model fetch")

        def initiate_model_download(self, *_args: Any, **_kwargs: Any) -> None:
            self.download_calls += 1

        def wait_for_download(self, *_args: Any, **_kwargs: Any) -> None:
            self.wait_calls += 1

    client = SimpleNamespace(models=Models())
    monkeypatch.setattr(integration_conftest.time, "sleep", lambda _seconds: None)
    ensure_repo_ready = integration_conftest.ensure_repo_ready.__wrapped__()

    model = ensure_repo_ready(client, "org/model")

    assert model is files_loaded_model
    assert client.models.list_calls == [True, True]
    assert client.models.download_calls == 1
    assert client.models.wait_calls == 1


def test_requires_embedding_model_marker_requires_functional_embedding_probe(
    integration_conftest,
) -> None:
    class Request:
        def __init__(self, keywords: dict[str, object]) -> None:
            self.keywords = keywords
            self.requested: list[str] = []

        def getfixturevalue(self, name: str) -> object:
            self.requested.append(name)
            return object()

    request = Request({"requires_embedding_model": object()})

    integration_conftest._require_embedding_model_for_marked_tests.__wrapped__(request)

    assert request.requested == ["embedding_test_target"]


def test_requires_embedding_model_marker_leaves_unmarked_tests_alone(
    integration_conftest,
) -> None:
    class Request:
        keywords: dict[str, object] = {}

        def __init__(self) -> None:
            self.requested: list[str] = []

        def getfixturevalue(self, name: str) -> object:
            self.requested.append(name)
            return object()

    request = Request()

    integration_conftest._require_embedding_model_for_marked_tests.__wrapped__(request)

    assert request.requested == []


def test_embedding_model_prerequisite_marks_harness_provisioned_deployment(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = {
        "deployment_id": "dep-123",
        "model_id": "model-123",
        "model_name": "all-MiniLM-L6-v2",
        "repo_model_id": "sentence-transformers/all-MiniLM-L6-v2",
    }
    active_calls = 0

    def active_embedding_deployment(_client: object) -> dict[str, str] | None:
        nonlocal active_calls
        active_calls += 1
        return None

    monkeypatch.setattr(
        integration_conftest,
        "_active_embedding_deployment",
        active_embedding_deployment,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_maybe_request_embedding_download_and_deploy",
        lambda *_args, **_kwargs: "queued",
    )
    monkeypatch.setattr(
        integration_conftest,
        "_wait_for_active_embedding_deployment",
        lambda *_args, **_kwargs: (deployment, None),
    )

    result = integration_conftest.embedding_model_prerequisite.__wrapped__(object())

    assert active_calls == 1
    assert result["deployment_id"] == "dep-123"
    assert result[integration_conftest._HARNESS_PROVISIONED_KEY] == "true"
    assert integration_conftest._HARNESS_PROVISIONED_KEY not in deployment


def test_embedding_model_prerequisite_marks_initial_active_deployment_preexisting(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = {
        "deployment_id": "dep-existing",
        "model_id": "model-existing",
        "model_name": "all-MiniLM-L6-v2",
        "repo_model_id": "sentence-transformers/all-MiniLM-L6-v2",
    }

    monkeypatch.setattr(
        integration_conftest,
        "_active_embedding_deployment",
        lambda _client: deployment,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_maybe_request_embedding_download_and_deploy",
        lambda *_args, **_kwargs: pytest.fail("pre-existing deployment should fast-path"),
    )

    result = integration_conftest.embedding_model_prerequisite.__wrapped__(object())

    assert result["deployment_id"] == "dep-existing"
    assert result[integration_conftest._HARNESS_PROVISIONED_KEY] == "false"
    assert integration_conftest._HARNESS_PROVISIONED_KEY not in deployment


def test_embedding_model_prerequisite_marks_raced_foreign_deployment_preexisting(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = {
        "deployment_id": "dep-foreign",
        "model_id": "model-foreign",
        "model_name": "nomic-embed-text",
        "repo_model_id": "nomic-ai/nomic-embed-text-v1.5",
    }

    monkeypatch.setattr(
        integration_conftest,
        "_active_embedding_deployment",
        lambda _client: None,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_maybe_request_embedding_download_and_deploy",
        lambda *_args, **_kwargs: "queued",
    )
    monkeypatch.setattr(
        integration_conftest,
        "_wait_for_active_embedding_deployment",
        lambda *_args, **_kwargs: (deployment, None),
    )

    result = integration_conftest.embedding_model_prerequisite.__wrapped__(object())

    assert result["deployment_id"] == "dep-foreign"
    assert result[integration_conftest._HARNESS_PROVISIONED_KEY] == "false"


def test_embedding_test_target_stops_harness_provisioned_failed_deployment(
    integration_conftest,
) -> None:
    client = _EmbeddingProbeClient(integration_conftest)
    deployment = {
        "deployment_id": "dep-456",
        "model_name": "all-MiniLM-L6-v2",
        "repo_model_id": "sentence-transformers/all-MiniLM-L6-v2",
        integration_conftest._HARNESS_PROVISIONED_KEY: "true",
    }

    with pytest.raises(pytest.skip.Exception) as exc_info:
        integration_conftest.embedding_test_target.__wrapped__(client, deployment)

    assert client.serving.stopped == [("dep-456", True)]
    assert integration_conftest._HARNESS_EMBEDDING_STOPPED_NOTE in str(exc_info.value)


def test_embedding_test_target_does_not_stop_preexisting_failed_deployment(
    integration_conftest,
) -> None:
    client = _EmbeddingProbeClient(integration_conftest)
    deployment = {
        "deployment_id": "dep-existing",
        "model_name": "all-MiniLM-L6-v2",
        "repo_model_id": "sentence-transformers/all-MiniLM-L6-v2",
        integration_conftest._HARNESS_PROVISIONED_KEY: "false",
    }

    with pytest.raises(pytest.skip.Exception):
        integration_conftest.embedding_test_target.__wrapped__(client, deployment)

    assert client.serving.stopped == []


def test_embedding_test_target_reports_attempted_stop_when_cleanup_fails(
    integration_conftest,
) -> None:
    client = _EmbeddingProbeClient(integration_conftest, fail_stop=True)
    deployment = {
        "deployment_id": "dep-789",
        "model_name": "all-MiniLM-L6-v2",
        "repo_model_id": "sentence-transformers/all-MiniLM-L6-v2",
        integration_conftest._HARNESS_PROVISIONED_KEY: "true",
    }

    with pytest.raises(pytest.skip.Exception) as exc_info:
        integration_conftest.embedding_test_target.__wrapped__(client, deployment)

    assert client.serving.stopped == []
    assert integration_conftest._HARNESS_EMBEDDING_STOP_ATTEMPTED_NOTE in str(
        exc_info.value
    )


def test_embedding_test_target_reports_missing_deployment_id_for_cleanup(
    integration_conftest,
) -> None:
    client = _EmbeddingProbeClient(integration_conftest)
    deployment = {
        "model_name": "all-MiniLM-L6-v2",
        "repo_model_id": "sentence-transformers/all-MiniLM-L6-v2",
        integration_conftest._HARNESS_PROVISIONED_KEY: "true",
    }

    with pytest.raises(pytest.skip.Exception) as exc_info:
        integration_conftest.embedding_test_target.__wrapped__(client, deployment)

    assert client.serving.stopped == []
    assert integration_conftest._HARNESS_EMBEDDING_NO_DEPLOYMENT_ID_NOTE in str(
        exc_info.value
    )
