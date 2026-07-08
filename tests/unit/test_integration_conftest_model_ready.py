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
