"""Concrete Kamiwaza SDK and Kubernetes implementation of inference runtime ops."""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import UUID

from kamiwaza_sdk.utils.model_file_readiness import model_file_download_satisfied
from kamiwaza_sdk.utils.quant_manager import QuantizationManager
from kamiwaza_sdk.validation.inference_runtime import (
    CatalogConfig,
    CatalogFile,
    CatalogModel,
    DeploymentRequest,
    ReadyDeployment,
    RuntimeObservation,
)
from kamiwaza_sdk.validation.models import RuntimeCluster

_DOWNLOAD_TIMEOUT_SECONDS = 900
_DEPLOY_TIMEOUT_SECONDS = 900
_POLL_INTERVAL_SECONDS = 5
_IMAGE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_TERMINAL_INACTIVE_STATUSES = frozenset({"STOPPED"})
_SUPPORTED_ENGINES = frozenset({"llamacpp", "vllm"})
_KAMIWAZA_NAMESPACE = "kamiwaza"
_SECRET_ARG_KEYS = frozenset(
    {
        "access-token",
        "access_token",
        "api-key",
        "api_key",
        "hf-token",
        "hf_token",
        "password",
        "secret",
        "token",
    }
)
_REDACTED = "<redacted>"

CommandRunner = Callable[[tuple[str, ...]], str]
ClientBuilder = Callable[[str, str], Any]


class KubectlRuntimeObserver:
    """Read exact image/args from pods labeled by platform deployment ID."""

    def __init__(
        self, kubeconfig_path: Path, runner: CommandRunner | None = None
    ) -> None:
        self._kubeconfig_path = kubeconfig_path
        self._runner = runner or _run_command

    def observe(self, deployment_id: str, engine: str) -> RuntimeObservation:
        _validate_observation_identity(deployment_id, engine)
        payload = _json_object(self._runner(self._command(deployment_id)))
        items = payload.get("items")
        if not isinstance(items, list):
            raise RuntimeError("kubectl returned an invalid pod collection")
        observations = tuple(
            observation
            for pod in items
            if (observation := _pod_observation(pod, engine)) is not None
        )
        if not observations:
            raise RuntimeError("no running inference pod observation is available")
        if len(set(observations)) != 1:
            raise RuntimeError("inconsistent runtime observations across replicas")
        return observations[0]

    def _command(self, deployment_id: str) -> tuple[str, ...]:
        return (
            "kubectl",
            "--kubeconfig",
            str(self._kubeconfig_path),
            "get",
            "pods",
            "--namespace",
            _KAMIWAZA_NAMESPACE,
            "--selector",
            f"kamiwaza.io/deployment-id={deployment_id}",
            "--output",
            "json",
        )


class SdkInferenceCluster:
    """Product API operations plus read-only runtime observation."""

    def __init__(
        self,
        client: Any,
        kubeconfig_path: Path,
        observer: KubectlRuntimeObserver | None = None,
    ) -> None:
        self._client = client
        self.kubeconfig_path = kubeconfig_path
        self._observer = observer or KubectlRuntimeObserver(kubeconfig_path)

    def discover(self, repository: str) -> CatalogModel:
        model = self._local_model(repository)
        if model is None:
            matches = self._client.models.search_models(
                repository, exact=True, load_files=True
            )
            model = next(
                (
                    item
                    for item in matches
                    if getattr(item, "repo_modelId", None) == repository
                ),
                None,
            )
        if model is None:
            raise RuntimeError("exact model repository was not discovered")
        return _catalog_model(model, repository)

    def ensure_download(self, repository: str, quantization: str) -> CatalogModel:
        model = self._local_model(repository)
        if model is not None and _model_ready(model, quantization):
            return _catalog_model(model, repository)
        self._client.models.initiate_model_download(
            repository, quantization=quantization
        )
        self._client.models.wait_for_download(
            repository,
            timeout=_DOWNLOAD_TIMEOUT_SECONDS,
            show_progress=False,
        )
        return self._wait_for_ready_model(repository, quantization)

    def list_configs(self, model_id: str) -> tuple[CatalogConfig, ...]:
        configs = self._client.models.get_model_configs(model_id)
        return tuple(
            CatalogConfig(config_id=str(item.id), default=bool(item.default))
            for item in configs
        )

    def deploy(self, request: DeploymentRequest) -> str:
        if request.runtime_profile != "product-default":
            raise RuntimeError("unsupported semantic runtime profile")
        deployment_id = self._client.serving.deploy_model(
            model_id=request.model_id,
            m_config_id=request.config_id,
            m_file_id=request.model_file_id,
            engine_name=request.engine,
            lb_port=0,
            autoscaling=False,
            min_copies=1,
            starting_copies=1,
            wait=False,
        )
        if not deployment_id:
            raise RuntimeError("platform refused the deployment")
        return str(deployment_id)

    def wait_ready(self, deployment_id: str) -> ReadyDeployment:
        deployment = self._client.serving.wait_for_deployment(
            deployment_id,
            poll_interval=_POLL_INTERVAL_SECONDS,
            timeout=_DEPLOY_TIMEOUT_SECONDS,
        )
        instances = [
            item
            for item in deployment.instances
            if str(getattr(item, "status", "")).upper() == "DEPLOYED"
        ]
        return ReadyDeployment(
            engine=str(getattr(deployment, "engine_name", "") or ""),
            instance_count=len(instances),
        )

    def observe_runtime(self, deployment_id: str, engine: str) -> RuntimeObservation:
        return self._observer.observe(deployment_id, engine)

    def chat(self, deployment_id: str, messages: tuple[dict[str, str], ...]) -> str:
        openai_client = self._client.openai.get_client(deployment_id=deployment_id)
        try:
            served = openai_client.models.list()
            model_name = _served_model_name(served)
            response = openai_client.chat.completions.create(
                model=model_name,
                messages=list(messages),
                temperature=0.0,
            )
            return _response_content(response)
        finally:
            openai_client.close()

    def stop(self, deployment_id: str) -> bool:
        return bool(
            self._client.serving.stop_deployment(
                deployment_id=deployment_id,
                force=True,
            )
        )

    def is_active(self, deployment_id: str) -> bool:
        for deployment in self._client.serving.list_deployments():
            if str(deployment.id) != deployment_id:
                continue
            return str(deployment.status).upper() not in _TERMINAL_INACTIVE_STATUSES
        return False

    def close(self) -> None:
        self._client.close()

    def _local_model(self, repository: str) -> Any | None:
        models = self._client.models.list_models(load_files=True)
        return next(
            (
                item
                for item in models
                if getattr(item, "repo_modelId", None) == repository
            ),
            None,
        )

    def _wait_for_ready_model(self, repository: str, quantization: str) -> CatalogModel:
        deadline = time.monotonic() + _DOWNLOAD_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            model = self._local_model(repository)
            if model is not None and _model_ready(model, quantization):
                return _catalog_model(model, repository)
            time.sleep(_POLL_INTERVAL_SECONDS)
        raise TimeoutError("model target files did not become ready")


class SdkInferenceClusterFactory:
    """Materialize opaque runtime file references into an SDK cluster client."""

    def __init__(self, client_builder: ClientBuilder | None = None) -> None:
        self._client_builder = client_builder or _build_client

    def __call__(self, runtime_cluster: RuntimeCluster) -> SdkInferenceCluster:
        api_key = _read_api_key(runtime_cluster.api_key_ref)
        kubeconfig = _file_reference(runtime_cluster.kubeconfig_ref)
        if not kubeconfig.is_file():
            raise RuntimeError("runtime kubeconfig is not materialized")
        client = self._client_builder(runtime_cluster.base_url, api_key)
        return SdkInferenceCluster(client, kubeconfig)


def _build_client(base_url: str, api_key: str) -> Any:
    from kamiwaza_sdk import KamiwazaClient

    return KamiwazaClient(base_url=base_url, api_key=api_key)


def _read_api_key(reference: str) -> str:
    if not reference.startswith("file://"):
        raise RuntimeError("runtime API key is not materialized as a file")
    path = _file_reference(reference)
    try:
        api_key = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise RuntimeError("runtime API key file is unavailable") from None
    if not api_key:
        raise RuntimeError("runtime API key file is empty")
    return api_key


def _file_reference(reference: str) -> Path:
    parsed = urlsplit(reference)
    _validate_file_location(parsed.scheme, parsed.netloc, parsed.path)
    return _absolute_file_path(parsed.path)


def _absolute_file_path(encoded_path: str) -> Path:
    path = Path(unquote(encoded_path))
    if not path.is_absolute():
        raise RuntimeError("runtime file reference must be absolute")
    return path


def _validate_file_location(scheme: str, authority: str, path: str) -> None:
    if scheme != "file":
        raise RuntimeError("runtime file reference is invalid")
    if authority:
        raise RuntimeError("runtime file reference is invalid")
    if not path:
        raise RuntimeError("runtime file reference is invalid")


def _catalog_model(model: Any, repository: str) -> CatalogModel:
    model_id = str(getattr(model, "id", "") or "") or None
    files = tuple(
        CatalogFile(
            file_id=str(getattr(item, "id", "") or ""),
            name=str(getattr(item, "name", "") or ""),
            ready=model_file_download_satisfied(item),
        )
        for item in (getattr(model, "m_files", None) or [])
    )
    return CatalogModel(model_id=model_id, repository=repository, files=files)


def _model_ready(model: Any, quantization: str) -> bool:
    files = list(getattr(model, "m_files", None) or [])
    target_files = _source_target_files(files, quantization)
    return bool(target_files) and all(
        model_file_download_satisfied(item) for item in target_files
    )


def _source_target_files(files: Sequence[Any], quantization: str) -> list[Any]:
    gguf = [
        item
        for item in files
        if str(getattr(item, "name", "") or "").lower().endswith(".gguf")
    ]
    if not gguf:
        return list(files)
    return list(
        QuantizationManager().filter_files_by_quantization(
            gguf, quantization, apply_fallback=False
        )
    )


def _served_model_name(response: Any) -> str:
    for item in getattr(response, "data", None) or []:
        model_name = str(getattr(item, "id", "") or "").strip()
        if model_name:
            return model_name
    raise RuntimeError("deployment did not publish a served model id")


def _response_content(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise RuntimeError("deployment returned no chat choices")
    content = getattr(getattr(choices[0], "message", None), "content", None)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("deployment returned empty assistant content")
    return content


def _validate_observation_identity(deployment_id: str, engine: str) -> None:
    try:
        UUID(deployment_id)
    except ValueError:
        raise RuntimeError("deployment id is not a UUID") from None
    if engine not in _SUPPORTED_ENGINES:
        raise RuntimeError("runtime observation engine is unsupported")


def _pod_observation(pod: Any, engine: str) -> RuntimeObservation | None:
    if not isinstance(pod, Mapping):
        raise RuntimeError("kubectl returned an invalid pod")
    status = _object_field(pod, "status")
    if status.get("phase") != "Running":
        return None
    spec = _object_field(pod, "spec")
    container = _named_item(spec.get("containers"), engine, "pod container")
    container_status = _named_item(
        status.get("containerStatuses"), engine, "pod container status"
    )
    args = container.get("args")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise RuntimeError("inference pod has invalid effective arguments")
    digest = _digest(container_status.get("imageID"))
    return RuntimeObservation(
        image_digest=digest,
        effective_args=_redact_args(args),
    )


def _redact_args(args: Sequence[str]) -> tuple[str, ...]:
    redacted: list[str] = []
    redact_next = False
    for arg in args:
        if redact_next:
            redacted.append(_REDACTED)
            redact_next = False
            continue
        key, separator, _value = arg.lstrip("-").partition("=")
        if key.lower() not in _SECRET_ARG_KEYS:
            redacted.append(arg)
            continue
        redacted.append(f"{arg.split('=', 1)[0]}={_REDACTED}" if separator else arg)
        redact_next = not separator
    return tuple(redacted)


def _object_field(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    return _as_object(value.get(key), f"pod {key}")


def _named_item(value: Any, name: str, label: str) -> Mapping[str, Any]:
    if not isinstance(value, list):
        raise RuntimeError(f"{label} collection is invalid")
    matches = [
        item for item in value if isinstance(item, Mapping) and item.get("name") == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"{label} is unavailable")
    return matches[0]


def _digest(value: Any) -> str:
    if not isinstance(value, str):
        raise RuntimeError("inference pod image digest is unavailable")
    match = _IMAGE_DIGEST_RE.search(value)
    if match is None:
        raise RuntimeError("inference pod image digest is unavailable")
    return match.group(0)


def _json_object(payload: str) -> Mapping[str, Any]:
    try:
        value = json.loads(payload)
    except (TypeError, ValueError):
        raise RuntimeError("kubectl returned invalid JSON") from None
    return _as_object(value, "kubectl response")


def _as_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} is invalid")
    return value


def _run_command(command: tuple[str, ...]) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("kubectl runtime observation failed") from None
    return result.stdout
