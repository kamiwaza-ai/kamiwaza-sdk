from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from kamiwaza_sdk.exceptions import APIError

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


@pytest.fixture(autouse=True)
def _clear_explicit_fleet_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep fixture unit tests independent of the invoking fleet environment."""
    for name in (
        "KAMIWAZA_TEST_LLM_REPO",
        "KAMIWAZA_TEST_LLM_ENGINE",
        "KAMIWAZA_TEST_LLM_QUANT",
    ):
        monkeypatch.delenv(name, raising=False)


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


def test_deployable_model_probe_uses_shared_repo_and_engine(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/model.gguf",
        engine_name="llamacpp",
    )
    deploy_calls: list[dict[str, object]] = []

    class Serving:
        def list_active_deployments(self) -> list[object]:
            return []

        def deploy_model(self, **kwargs: object) -> str:
            deploy_calls.append(kwargs)
            return "dep-1"

        def wait_for_deployment(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                status="DEPLOYED",
                instances=[SimpleNamespace(status="DEPLOYED")],
            )

        def stop_deployment(self, **_kwargs: object) -> None:
            return None

    client = SimpleNamespace(
        serving=Serving(),
        models=SimpleNamespace(
            get_model_configs=lambda _model_id: [
                SimpleNamespace(id="cfg-1", default=True)
            ]
        ),
    )
    model_file_id = uuid4()
    ensure_repo_ready = lambda _client, repo_id, **_kwargs: SimpleNamespace(  # noqa: E731
        id=f"model-for-{repo_id}",
        m_files=[
            SimpleNamespace(
                id=model_file_id,
                name="model-Q4_K_M.gguf",
                storage_location="oci://model-Q4_K_M.gguf",
                is_downloading=False,
                dl_requested_at=None,
            )
        ],
    )

    integration_conftest.deployable_model_prerequisite.__wrapped__(
        client,
        ensure_repo_ready,
        target,
    )

    assert deploy_calls[0]["model_id"] == "model-for-org/model.gguf"
    # engine_name is intentionally NOT forced: the platform auto-selects it from
    # the model's weight format (GGUF -> llamacpp; kamiwaza.serving.engine_selector),
    # which equals the target's engine by construction. The probe passes only the
    # repo + file; forcing the engine was redundant with the model choice. (ENG-9872)
    assert "engine_name" not in deploy_calls[0]
    assert deploy_calls[0]["m_file_id"] == str(model_file_id)


@pytest.mark.parametrize(
    ("snapshot_kwargs", "target_name"),
    [
        (
            {"gpu_count": 1, "gpu_vendors": frozenset({"nvidia"})},
            "VLLM_LLM_TARGET",
        ),
        (
            {
                "os_platforms": frozenset(
                    {("darwin", "macos-15.4-arm64-arm-64bit")}
                )
            },
            "MLX_LLM_TARGET",
        ),
        (
            {
                "os_platforms": frozenset(
                    {("linux", "linux-5.14.0-el9.x86_64")}
                )
            },
            "GGUF_LLM_TARGET",
        ),
    ],
)
def test_deployable_model_target_follows_cluster_inventory(
    integration_conftest,
    snapshot_kwargs: dict[str, object],
    target_name: str,
) -> None:
    snapshot = integration_conftest._cap.ClusterCapabilitySnapshot(
        **snapshot_kwargs
    )

    target = integration_conftest.deployable_model_target.__wrapped__(snapshot)

    assert target is getattr(integration_conftest._model_targets, target_name)


def test_ensure_deployable_model_ready_forwards_selected_target(
    integration_conftest,
) -> None:
    selected_target = integration_conftest._model_targets.VLLM_LLM_TARGET
    calls: list[tuple[object, str, str]] = []

    ensure = integration_conftest.ensure_deployable_model_ready.__wrapped__(
        lambda client, repo_id, *, quantization: calls.append(
            (client, repo_id, quantization)
        )
        or "ready-model",
        selected_target,
    )
    client = object()

    assert ensure(client) == "ready-model"
    assert calls == [
        (client, selected_target.repo_id, selected_target.quantization)
    ]


def test_ensure_deployable_target_ready_pins_target_quantization(
    integration_conftest,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/model.gguf",
        engine_name="llamacpp",
        quantization="q4_k",
    )
    calls: list[tuple[object, str, str]] = []
    client = object()

    result = integration_conftest._ensure_deployable_target_ready(
        client,
        lambda actual_client, repo_id, *, quantization: calls.append(
            (actual_client, repo_id, quantization)
        )
        or "ready-model",
        target,
    )

    assert result == "ready-model"
    assert calls == [(client, "org/model.gguf", "q4_k")]


def test_ensure_deployable_target_ready_skips_download_timeout(
    integration_conftest,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/model.gguf",
        engine_name="llamacpp",
        quantization="q4_k",
    )

    def time_out(*_args: object, **_kwargs: object) -> object:
        raise TimeoutError("download stalled")

    with pytest.raises(pytest.skip.Exception, match="download stalled"):
        integration_conftest._ensure_deployable_target_ready(
            object(),
            time_out,
            target,
        )


def test_ensure_required_deployable_target_ready_fails_download_timeout(
    integration_conftest,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/required-model.gguf",
        engine_name="llamacpp",
        quantization="q4_k",
        required=True,
    )

    def time_out(*_args: object, **_kwargs: object) -> object:
        raise TimeoutError("required download stalled")

    with pytest.raises(TimeoutError, match="required download stalled"):
        integration_conftest._ensure_deployable_target_ready(
            object(),
            time_out,
            target,
        )


def test_ensure_deployable_target_ready_skips_missing_quantization(
    integration_conftest,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/model.gguf",
        engine_name="llamacpp",
        quantization="q4_k",
    )

    def reject_quantization(*_args: object, **_kwargs: object) -> object:
        raise ValueError("q4_k is unavailable")

    with pytest.raises(pytest.skip.Exception, match="q4_k is unavailable"):
        integration_conftest._ensure_deployable_target_ready(
            object(),
            reject_quantization,
            target,
        )


def test_ensure_deployable_target_ready_skips_runtime_failure(
    integration_conftest,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/model.gguf",
        engine_name="llamacpp",
        quantization="q4_k",
    )

    def fail_runtime(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("deployment failed")

    with pytest.raises(pytest.skip.Exception, match="deployment failed"):
        integration_conftest._ensure_deployable_target_ready(
            object(), fail_runtime, target
        )


def test_ensure_deployable_target_ready_reraises_client_error(
    integration_conftest,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/model.gguf",
        engine_name="llamacpp",
        quantization="q4_k",
    )
    error = integration_conftest.APIError("invalid request", status_code=400)

    def reject(*_args: object, **_kwargs: object) -> object:
        raise error

    try:
        integration_conftest._ensure_deployable_target_ready(
            object(),
            reject,
            target,
        )
    except pytest.skip.Exception as skipped:
        pytest.fail(f"a 4xx must not be masked as a skip: {skipped}")
    except integration_conftest.APIError as raised:
        assert raised is error
    else:
        pytest.fail("expected the 4xx to propagate")


def test_ensure_deployable_target_ready_skips_server_error(
    integration_conftest,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/model.gguf",
        engine_name="llamacpp",
        quantization="q4_k",
    )
    error = integration_conftest.APIError("runtime unavailable", status_code=500)

    def reject(*_args: object, **_kwargs: object) -> object:
        raise error

    with pytest.raises(pytest.skip.Exception, match="APIError 500"):
        integration_conftest._ensure_deployable_target_ready(
            object(),
            reject,
            target,
        )


def test_ensure_required_deployable_target_ready_reraises_server_error(
    integration_conftest,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/required-model.gguf",
        engine_name="llamacpp",
        quantization="q4_k",
        required=True,
    )
    error = integration_conftest.APIError(
        "required runtime unavailable",
        status_code=500,
    )

    def reject(*_args: object, **_kwargs: object) -> object:
        raise error

    with pytest.raises(integration_conftest.APIError) as excinfo:
        integration_conftest._ensure_deployable_target_ready(
            object(),
            reject,
            target,
        )

    assert excinfo.value is error


def test_ensure_deployable_target_ready_skips_transport_api_error(
    integration_conftest,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/model.gguf",
        engine_name="llamacpp",
        quantization="q4_k",
    )
    error = integration_conftest.APIError("connection reset", status_code=None)

    def reject(*_args: object, **_kwargs: object) -> object:
        raise error

    with pytest.raises(pytest.skip.Exception, match="connection reset"):
        integration_conftest._ensure_deployable_target_ready(
            object(), reject, target
        )


def test_deployable_model_probe_reuses_exact_active_target(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/model.gguf",
        engine_name="llamacpp",
    )
    monkeypatch.setattr(
        integration_conftest,
        "_preferred_active_model_deployment",
        lambda *_args, **_kwargs: {
            "repo_model_id": target.repo_id,
            "engine_name": target.engine_name,
        },
    )

    integration_conftest.deployable_model_prerequisite.__wrapped__(
        object(),
        lambda *_args: pytest.fail("an active exact target must skip the probe deploy"),
        target,
    )


@pytest.mark.parametrize(
    ("active_repo", "active_engine"),
    [
        ("org/model.gguf", "mlx"),
        ("someone-else/other", "llamacpp"),
    ],
)
def test_deployable_model_probe_does_not_reuse_mismatched_target(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
    active_repo: str,
    active_engine: str,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/model.gguf",
        engine_name="llamacpp",
    )
    monkeypatch.setattr(
        integration_conftest,
        "_preferred_active_model_deployment",
        lambda *_args, **_kwargs: {
            "repo_model_id": active_repo,
            "engine_name": active_engine,
        },
    )
    monkeypatch.setattr(
        integration_conftest,
        "_ensure_deployable_target_ready",
        lambda *_args, **_kwargs: SimpleNamespace(id="model-1", m_files=[]),
    )
    deploy_calls: list[dict[str, object]] = []
    client = SimpleNamespace(
        models=SimpleNamespace(
            get_model_configs=lambda _model_id: [
                SimpleNamespace(id="cfg-1", default=True)
            ]
        ),
        serving=SimpleNamespace(
            deploy_model=lambda **kwargs: deploy_calls.append(kwargs) or "dep-1",
            wait_for_deployment=lambda *_args, **_kwargs: SimpleNamespace(
                status="DEPLOYED",
                instances=[SimpleNamespace(status="DEPLOYED")],
            ),
            stop_deployment=lambda **_kwargs: None,
        ),
    )

    integration_conftest.deployable_model_prerequisite.__wrapped__(
        client,
        lambda *_args, **_kwargs: pytest.fail("patched readiness should be used"),
        target,
    )

    # A mismatched active deployment must trigger a fresh deploy (not reuse). The
    # re-deploy no longer forces engine_name -- the platform auto-selects it from
    # the model format; the target's engine remains the match key above. (ENG-9872)
    assert deploy_calls and "engine_name" not in deploy_calls[0]


def test_context_target_carries_shared_quantization(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/model.gguf",
        engine_name="llamacpp",
        quantization="q4_k",
        required=True,
    )
    monkeypatch.setattr(integration_conftest, "_CONTEXT_TEST_LLM_REPO_OVERRIDE", "")
    monkeypatch.setattr(integration_conftest, "_CONTEXT_TEST_LLM_ENGINE_OVERRIDE", "")
    monkeypatch.setattr(
        integration_conftest._model_targets,
        "select_inference_target",
        lambda _snapshot: target,
    )

    assert integration_conftest._context_llm_target(None) == target


def test_explicit_context_override_is_required(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        integration_conftest,
        "_CONTEXT_TEST_LLM_REPO_OVERRIDE",
        "org/context.gguf",
    )
    monkeypatch.setattr(
        integration_conftest,
        "_CONTEXT_TEST_LLM_ENGINE_OVERRIDE",
        "llamacpp",
    )

    assert integration_conftest._context_llm_target(None).required is True


def test_context_prerequisite_prepares_and_deploys_exact_target_file(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/context.gguf",
        engine_name="llamacpp",
        quantization="q4_k",
    )
    model_file_id = uuid4()
    readiness_calls: list[tuple[str, str]] = []
    deploy_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        integration_conftest,
        "_context_llm_target",
        lambda _snapshot: target,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_preferred_active_model_deployment",
        lambda *_args, **_kwargs: None,
    )

    def ensure_repo_ready(
        _client: object, repo_id: str, *, quantization: str
    ) -> object:
        readiness_calls.append((repo_id, quantization))
        return SimpleNamespace(
            id="model-1",
            m_files=[
                SimpleNamespace(
                    id=model_file_id,
                    name="context-Q4_K_M.gguf",
                    storage_location="oci://context-Q4_K_M.gguf",
                    is_downloading=False,
                    dl_requested_at=None,
                )
            ],
        )

    client = SimpleNamespace(
        models=SimpleNamespace(
            get_model_configs=lambda _model_id: [
                SimpleNamespace(id="cfg-1", default=True)
            ]
        ),
        serving=SimpleNamespace(
            deploy_model=lambda **kwargs: deploy_calls.append(kwargs) or "dep-1",
            wait_for_deployment=lambda *_args, **_kwargs: SimpleNamespace(
                id="dep-1",
                status="DEPLOYED",
                instances=[SimpleNamespace(status="DEPLOYED")],
            ),
            stop_deployment=lambda **_kwargs: None,
        ),
    )

    prerequisite = integration_conftest.context_llm_prerequisite.__wrapped__(
        client,
        ensure_repo_ready,
        None,
    )
    assert next(prerequisite) == "dep-1"
    with pytest.raises(StopIteration):
        next(prerequisite)

    assert readiness_calls == [(target.repo_id, target.quantization)]
    assert deploy_calls[0]["engine_name"] == target.engine_name
    assert deploy_calls[0]["m_file_id"] == str(model_file_id)


def test_required_context_does_not_reuse_mismatched_active_quantization(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/context.gguf",
        engine_name="llamacpp",
        quantization="q8_0",
        required=True,
    )
    q8_file_id = uuid4()
    readiness_calls: list[tuple[str, str]] = []
    deploy_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        integration_conftest,
        "_context_llm_target",
        lambda _snapshot: target,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_preferred_active_model_deployment",
        lambda *_args, **_kwargs: {
            "deployment_id": "dep-q4",
            "repo_model_id": target.repo_id,
            "engine_name": target.engine_name,
            "m_file_id": "q4-file-id",
        },
    )

    def ensure_repo_ready(
        _client: object, repo_id: str, *, quantization: str
    ) -> object:
        readiness_calls.append((repo_id, quantization))
        return SimpleNamespace(
            id="model-1",
            m_files=[
                SimpleNamespace(
                    id=q8_file_id,
                    name="context-Q8_0.gguf",
                    storage_location="oci://context-Q8_0.gguf",
                    is_downloading=False,
                    dl_requested_at=None,
                )
            ],
        )

    client = SimpleNamespace(
        models=SimpleNamespace(
            get_model_configs=lambda _model_id: [
                SimpleNamespace(id="cfg-1", default=True)
            ]
        ),
        serving=SimpleNamespace(
            deploy_model=lambda **kwargs: deploy_calls.append(kwargs) or "dep-q8",
            wait_for_deployment=lambda *_args, **_kwargs: SimpleNamespace(
                id="dep-q8",
                status="DEPLOYED",
                instances=[SimpleNamespace(status="DEPLOYED")],
            ),
            stop_deployment=lambda **_kwargs: None,
        ),
    )

    prerequisite = integration_conftest.context_llm_prerequisite.__wrapped__(
        client,
        ensure_repo_ready,
        None,
    )
    assert next(prerequisite) == "dep-q8"
    with pytest.raises(StopIteration):
        next(prerequisite)

    assert readiness_calls == [(target.repo_id, target.quantization)]
    assert deploy_calls[0]["m_file_id"] == str(q8_file_id)


def test_context_prerequisite_does_not_mask_programming_errors(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = integration_conftest._model_targets.GGUF_LLM_TARGET
    monkeypatch.setattr(
        integration_conftest,
        "_context_llm_target",
        lambda _snapshot: target,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_preferred_active_model_deployment",
        lambda *_args, **_kwargs: None,
    )

    def programming_error(*_args: object, **_kwargs: object) -> object:
        raise TypeError("fixture wiring defect")

    prerequisite = integration_conftest.context_llm_prerequisite.__wrapped__(
        object(),
        programming_error,
        None,
    )
    try:
        next(prerequisite)
    except pytest.skip.Exception as skipped:
        pytest.fail(f"a programming error must not be masked as a skip: {skipped}")
    except TypeError as raised:
        assert str(raised) == "fixture wiring defect"
    else:
        pytest.fail("expected the programming error to propagate")


@pytest.mark.parametrize(
    ("status_code", "should_skip"),
    [(400, False), (500, True), (None, True)],
)
def test_context_prerequisite_preserves_api_error_taxonomy(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int | None,
    should_skip: bool,
) -> None:
    target = integration_conftest._model_targets.GGUF_LLM_TARGET
    error = integration_conftest.APIError(
        "context readiness failed", status_code=status_code
    )
    monkeypatch.setattr(
        integration_conftest,
        "_context_llm_target",
        lambda _snapshot: target,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_preferred_active_model_deployment",
        lambda *_args, **_kwargs: None,
    )

    def fail_readiness(*_args: object, **_kwargs: object) -> object:
        raise error

    prerequisite = integration_conftest.context_llm_prerequisite.__wrapped__(
        object(), fail_readiness, None
    )
    if should_skip:
        with pytest.raises(pytest.skip.Exception, match="context readiness failed"):
            next(prerequisite)
        return

    try:
        next(prerequisite)
    except pytest.skip.Exception as skipped:
        pytest.fail(f"a 4xx must not be masked as a skip: {skipped}")
    except integration_conftest.APIError as raised:
        assert raised is error
    else:
        pytest.fail("expected the 4xx to propagate")


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("required context deployment timed out"),
        pytest.param(
            APIError("required context server failure", status_code=500),
            id="server-error",
        ),
    ],
)
def test_required_context_prerequisite_reraises_capability_failure(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/required-context.gguf",
        engine_name="llamacpp",
        required=True,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_context_llm_target",
        lambda _snapshot: target,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_preferred_active_model_deployment",
        lambda *_args, **_kwargs: None,
    )

    def fail_readiness(*_args: object, **_kwargs: object) -> object:
        raise error

    prerequisite = integration_conftest.context_llm_prerequisite.__wrapped__(
        object(),
        fail_readiness,
        None,
    )

    with pytest.raises(type(error)) as excinfo:
        next(prerequisite)

    assert excinfo.value is error


def test_deployable_probe_skips_transport_api_error(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = integration_conftest._model_targets.GGUF_LLM_TARGET
    error = integration_conftest.APIError("connection reset", status_code=None)
    monkeypatch.setattr(
        integration_conftest,
        "_preferred_active_model_deployment",
        lambda *_args, **_kwargs: None,
    )

    def fail_readiness(*_args: object, **_kwargs: object) -> object:
        raise error

    monkeypatch.setattr(
        integration_conftest,
        "_ensure_deployable_target_ready",
        fail_readiness,
    )
    client = SimpleNamespace(
        serving=SimpleNamespace(stop_deployment=lambda **_kwargs: None)
    )

    with pytest.raises(pytest.skip.Exception, match="APIError transport"):
        integration_conftest.deployable_model_prerequisite.__wrapped__(
            client, object(), target
        )


def test_required_deployable_probe_reraises_readiness_timeout(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/required-model.gguf",
        engine_name="llamacpp",
        required=True,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_preferred_active_model_deployment",
        lambda *_args, **_kwargs: None,
    )

    def fail_readiness(*_args: object, **_kwargs: object) -> object:
        raise TimeoutError("required model download timed out")

    client = SimpleNamespace(
        serving=SimpleNamespace(stop_deployment=lambda **_kwargs: None)
    )

    with pytest.raises(TimeoutError, match="required model download timed out"):
        integration_conftest.deployable_model_prerequisite.__wrapped__(
            client,
            fail_readiness,
            target,
        )


def test_required_deployable_target_fails_without_model_config(
    integration_conftest,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/required-model.gguf",
        engine_name="llamacpp",
        required=True,
    )
    client = SimpleNamespace(
        models=SimpleNamespace(get_model_configs=lambda _model_id: [])
    )

    with pytest.raises(pytest.fail.Exception, match="No model configs available"):
        integration_conftest._default_model_config(client, "model-1", target)


def test_required_deployable_target_fails_when_deploy_returns_no_id(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/required-model.gguf",
        engine_name="llamacpp",
        required=True,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_ensure_deployable_target_ready",
        lambda *_args, **_kwargs: SimpleNamespace(id="model-1", m_files=[]),
    )
    client = SimpleNamespace(
        models=SimpleNamespace(
            get_model_configs=lambda _model_id: [
                SimpleNamespace(id="cfg-1", default=True)
            ]
        ),
        serving=SimpleNamespace(deploy_model=lambda **_kwargs: None),
    )

    with pytest.raises(pytest.fail.Exception, match="returned no id"):
        integration_conftest._start_deployable_target_probe(
            client,
            object(),
            target,
        )


def test_required_deployable_probe_fails_when_deployment_is_not_ready(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = integration_conftest._model_targets.InferenceTarget(
        repo_id="org/required-model.gguf",
        engine_name="llamacpp",
        required=True,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_matching_active_deployable_target",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        integration_conftest,
        "_start_deployable_target_probe",
        lambda *_args, **_kwargs: "dep-1",
    )
    client = SimpleNamespace(
        serving=SimpleNamespace(
            wait_for_deployment=lambda *_args, **_kwargs: SimpleNamespace(
                status="FAILED",
                instances=[SimpleNamespace(status="FAILED")],
            ),
            stop_deployment=lambda **_kwargs: None,
        )
    )

    with pytest.raises(pytest.fail.Exception, match="did not become ready"):
        integration_conftest.deployable_model_prerequisite.__wrapped__(
            client,
            object(),
            target,
        )


def test_target_model_file_id_pins_ready_gguf_quantization(
    integration_conftest,
) -> None:
    selected_id = uuid4()
    model = SimpleNamespace(
        m_files=[
            SimpleNamespace(
                id=uuid4(),
                name="model-Q6_K.gguf",
                storage_location="oci://model-Q6_K.gguf",
                is_downloading=False,
                dl_requested_at=None,
            ),
            SimpleNamespace(
                id=selected_id,
                name="model-Q4_K_M.gguf",
                storage_location="oci://model-Q4_K_M.gguf",
                is_downloading=False,
                dl_requested_at=None,
            ),
        ]
    )

    assert integration_conftest._target_model_file_id(model, "q4_k") == str(
        selected_id
    )


def test_target_model_file_id_ignores_unready_files(integration_conftest) -> None:
    ready_id = uuid4()
    model = SimpleNamespace(
        m_files=[
            SimpleNamespace(
                id=uuid4(),
                name="a-Q4_K_M.gguf",
                storage_location=None,
                is_downloading=True,
                dl_requested_at=None,
            ),
            SimpleNamespace(
                id=ready_id,
                name="z-Q4_K_M.gguf",
                storage_location="oci://z-Q4_K_M.gguf",
                is_downloading=False,
                dl_requested_at=None,
            ),
        ]
    )

    assert integration_conftest._target_model_file_id(model, "q4_k") == str(
        ready_id
    )


def test_target_model_file_id_is_deterministic_across_input_order(
    integration_conftest,
) -> None:
    first_id = uuid4()
    files = [
        SimpleNamespace(
            id=first_id,
            name="a-Q4_K_M.gguf",
            storage_location="oci://a-Q4_K_M.gguf",
            is_downloading=False,
            dl_requested_at=None,
        ),
        SimpleNamespace(
            id=uuid4(),
            name="z-Q4_K_M.gguf",
            storage_location="oci://z-Q4_K_M.gguf",
            is_downloading=False,
            dl_requested_at=None,
        ),
    ]

    assert integration_conftest._target_model_file_id(
        SimpleNamespace(m_files=files), "q4_k"
    ) == str(first_id)
    assert integration_conftest._target_model_file_id(
        SimpleNamespace(m_files=list(reversed(files))), "q4_k"
    ) == str(first_id)


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
