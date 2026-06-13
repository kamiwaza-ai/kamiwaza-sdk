from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

import pytest
import requests
import urllib3
from huggingface_hub import snapshot_download
from requests.adapters import HTTPAdapter

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import APIError, AuthenticationError, KamiwazaError
from kamiwaza_sdk.schemas.auth import PATCreate
from kamiwaza_sdk.token_store import StoredToken, TokenStore

# Co-located capability-marker helpers (M5). Add this directory to the path so
# the import resolves regardless of pytest's package-import mode (this conftest
# is loaded by name, not as part of the ``integration`` package).
sys.path.insert(0, str(Path(__file__).parent))
import capability_markers as _cap  # noqa: E402

DOCKER_COMPOSE_FILE = Path(__file__).parent / "docker" / "docker-compose.yml"
SEED_SCRIPT = Path(__file__).parent / "docker" / "seed_minio.py"

CATALOG_STACK_DIR = Path(__file__).parent / "catalog_stack"
CATALOG_STACK_COMPOSE = CATALOG_STACK_DIR / "docker-compose.yml"
CATALOG_STACK_SETUP = CATALOG_STACK_DIR / "setup-test-data.sh"
CATALOG_MINIO_ENDPOINT = os.environ.get(
    "CATALOG_STACK_MINIO_ENDPOINT", "http://localhost:19100"
)
CATALOG_MINIO_BUCKET = os.environ.get(
    "CATALOG_STACK_MINIO_BUCKET", "kamiwaza-test-bucket"
)
CATALOG_MINIO_PREFIX = os.environ.get("CATALOG_STACK_MINIO_PREFIX", "catalog-tests")
CATALOG_POSTGRES = {
    "host": os.environ.get("CATALOG_STACK_POSTGRES_HOST", "localhost"),
    "port": os.environ.get("CATALOG_STACK_POSTGRES_PORT", "15432"),
    "database": os.environ.get("CATALOG_STACK_POSTGRES_DB", "kamiwaza"),
    "user": os.environ.get("CATALOG_STACK_POSTGRES_USER", "kamiwaza"),
    "password": os.environ.get(
        "CATALOG_STACK_POSTGRES_PASSWORD", "kamiwazaGetY0urCape"
    ),
    "schema": os.environ.get("CATALOG_STACK_POSTGRES_SCHEMA", "public"),
}
CATALOG_KAFKA_BOOTSTRAP = os.environ.get(
    "CATALOG_STACK_KAFKA_BOOTSTRAP", "localhost:29092"
)
PODMAN_MACHINE_SOCKET = (
    Path.home()
    / ".local"
    / "share"
    / "containers"
    / "podman"
    / "machine"
    / "podman.sock"
)
RUNTIME_HOST_ALIAS = os.environ.get(
    "KAMIWAZA_DOCKER_HOST_ALIAS", "host.docker.internal"
)

_COMPOSE_ENV_CACHE: dict[str, str] | None = None
_COMPOSE_ENV_ERROR: str | None = None
_LIVE_PASSWORD_CACHE: dict[tuple[str, str, str], tuple[str, str | None]] = {}
# Memoize PAT probe (``GET /auth/users/me``) results for the session.
# ``_api_key_auth_works`` is called once in ``live_session_api_key`` and
# once in ``live_session_write_key`` (plus any future fixture that needs
# to validate a PAT). Without caching, the same PAT gets probed against
# Keycloak on every call, which is noise at best and a lockout vector at
# worst. Cache both success and failure so a bad PAT doesn't keep
# re-probing either.
_API_KEY_PROBE_CACHE: dict[tuple[str, str], tuple[bool, str]] = {}
_PROBE_TIMEOUT_SECONDS = 10.0
_PROBE_ERROR_TRUNCATE = 200
_logger = logging.getLogger(__name__)


class _TimeoutHTTPAdapter(HTTPAdapter):
    """HTTPAdapter that applies a default timeout to every request."""

    def __init__(
        self, *args: Any, timeout: float = _PROBE_TIMEOUT_SECONDS, **kwargs: Any
    ):
        self._timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", self._timeout)
        return super().send(request, **kwargs)


def _mount_probe_timeout(client: KamiwazaClient) -> None:
    adapter = _TimeoutHTTPAdapter(timeout=_PROBE_TIMEOUT_SECONDS)
    client.session.mount("http://", adapter)
    client.session.mount("https://", adapter)


_HTTP_TRACE_FLAG = "KAMIWAZA_HTTP_TRACE"
_HTTP_TRACE_FILE_ENV = "KAMIWAZA_HTTP_TRACE_FILE"
_TEXT_BODY_MARKERS = (
    "application/json",
    "application/problem+json",
    "application/x-www-form-urlencoded",
    "application/xml",
    "application/yaml",
    "application/javascript",
    "application/x-ndjson",
    "application/graphql",
    "text/",
)
CONTEXT_TEST_LLM_REPO = os.environ.get(
    "KAMIWAZA_CONTEXT_LLM_REPO",
    "mlx-community/Qwen3-4B-4bit",
)
CONTEXT_TEST_LLM_DEPLOY_TIMEOUT_SECONDS = float(
    os.environ.get("KAMIWAZA_CONTEXT_LLM_DEPLOY_TIMEOUT_SECONDS", "600")
)
# Must stay in sync with TEST_REPO_ID in test_serving_workflow.py and
# test_cli_live.py: the capability probe deploys the SAME model the gated tests
# deploy, so the gate's skip/run verdict matches what the tests will actually do.
# Intentionally not env-overridable — an override here that the test modules
# don't honor would let the probe pass while the tests deploy a different model
# and fail instead of skip.
DEPLOYABLE_TEST_MODEL_REPO = "mlx-community/Qwen3-4B-4bit"
DEPLOYABLE_TEST_DEPLOY_TIMEOUT_SECONDS = float(
    os.environ.get("KAMIWAZA_DEPLOYABLE_TEST_DEPLOY_TIMEOUT_SECONDS", "600")
)
EMBEDDING_TEST_MODEL_REPO = os.environ.get(
    "KAMIWAZA_TEST_EMBEDDING_MODEL_REPO",
    "sentence-transformers/all-MiniLM-L6-v2",
)
EMBEDDING_TEST_MODEL_HUB = os.environ.get("KAMIWAZA_TEST_EMBEDDING_MODEL_HUB", "hf")
EMBEDDING_TEST_PROVIDER = os.environ.get("KAMIWAZA_TEST_EMBEDDING_PROVIDER", "").strip()
EMBEDDING_TEST_DEPLOY_TIMEOUT_SECONDS = float(
    os.environ.get("KAMIWAZA_TEST_EMBEDDING_DEPLOY_TIMEOUT_SECONDS", "240")
)
EMBEDDING_TEST_DEPLOY_POLL_SECONDS = float(
    os.environ.get("KAMIWAZA_TEST_EMBEDDING_DEPLOY_POLL_SECONDS", "5")
)
EMBEDDING_TEST_DEPLOY_NUDGE_SECONDS = float(
    os.environ.get("KAMIWAZA_TEST_EMBEDDING_DEPLOY_NUDGE_SECONDS", "20")
)
EMBEDDING_TEST_DEPLOY_CONTEXT = int(
    os.environ.get("KAMIWAZA_TEST_EMBEDDING_DEPLOY_CONTEXT", "8192")
)
EMBEDDING_TEST_PROBE_TEXT = os.environ.get(
    "KAMIWAZA_TEST_EMBEDDING_PROBE_TEXT",
    "sdk embedding probe",
)
_EMBEDDING_NAME_PATTERNS = re.compile(
    r"(?i)(bge|e5[-_]|nomic[-_]?embed|gte[-_]|all[-_]minilm|"
    r"instructor|jina[-_]?embed|text[-_]?embedding|embed)"
)


def _http_trace_enabled() -> bool:
    flag = os.environ.get(_HTTP_TRACE_FLAG, "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    return bool(os.environ.get(_HTTP_TRACE_FILE_ENV, "").strip())


def _trace_body_payload(
    body: bytes | str | None,
    *,
    content_type: str = "",
    encoding: str | None = None,
) -> dict[str, Any]:
    if body is None:
        return {"encoding": "none", "size": 0, "body": None}

    if isinstance(body, str):
        raw = body.encode(encoding or "utf-8", errors="replace")
        return {
            "encoding": "utf-8",
            "size": len(raw),
            "body": body,
        }

    raw = bytes(body)
    lowered = content_type.lower()
    is_text = any(marker in lowered for marker in _TEXT_BODY_MARKERS)
    if not lowered:
        try:
            decoded = raw.decode(encoding or "utf-8")
        except UnicodeDecodeError:
            decoded = None
        else:
            return {
                "encoding": encoding or "utf-8",
                "size": len(raw),
                "body": decoded,
            }
    if is_text:
        return {
            "encoding": encoding or "utf-8",
            "size": len(raw),
            "body": raw.decode(encoding or "utf-8", errors="replace"),
        }
    return {
        "encoding": "base64",
        "size": len(raw),
        "body": base64.b64encode(raw).decode("ascii"),
    }


def _trace_request_body(prepared_request: requests.PreparedRequest) -> dict[str, Any]:
    body = prepared_request.body
    if body is None:
        return {"encoding": "none", "size": 0, "body": None}
    if isinstance(body, (bytes, str)):
        return _trace_body_payload(
            body,
            content_type=prepared_request.headers.get("Content-Type", ""),
        )
    return {
        "encoding": "unavailable",
        "size": None,
        "body": repr(body),
    }


class _HTTPTraceWriter:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._sequence = 0

    def write(self, event_type: str, **payload: Any) -> None:
        with self._lock:
            self._sequence += 1
            record = {
                "seq": self._sequence,
                "ts": time.time(),
                "event": event_type,
                **payload,
            }
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True))
                handle.write("\n")


def _wrap_streaming_response(
    response: requests.Response,
    writer: _HTTPTraceWriter,
    *,
    request_id: int,
    started_at: float,
) -> requests.Response:
    original_iter_content = response.iter_content
    original_close = response.close
    buffered = bytearray()
    finalized = False
    completed = False

    def finalize() -> None:
        nonlocal finalized
        if finalized:
            return
        finalized = True
        writer.write(
            "response-body",
            request_id=request_id,
            streamed=True,
            complete=completed,
            elapsed_ms=round((time.time() - started_at) * 1000, 3),
            body=_trace_body_payload(
                bytes(buffered),
                content_type=response.headers.get("Content-Type", ""),
                encoding=response.encoding,
            ),
        )

    def traced_iter_content(*args: Any, **kwargs: Any):
        nonlocal completed
        try:
            for chunk in original_iter_content(*args, **kwargs):
                if isinstance(chunk, str):
                    buffered.extend(
                        chunk.encode(response.encoding or "utf-8", errors="replace")
                    )
                elif chunk is not None:
                    buffered.extend(chunk)
                yield chunk
            completed = True
        finally:
            finalize()

    def traced_close() -> None:
        try:
            original_close()
        finally:
            finalize()

    response.iter_content = traced_iter_content  # type: ignore[assignment]
    response.close = traced_close  # type: ignore[assignment]
    return response


def _docker_info_ok(env: dict[str, str]) -> tuple[bool, str]:
    result = subprocess.run(
        ["docker", "info"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        return True, ""
    message = (result.stderr or result.stdout or "").strip()
    return False, message


def _resolve_compose_env() -> dict[str, str]:
    global _COMPOSE_ENV_CACHE, _COMPOSE_ENV_ERROR

    if _COMPOSE_ENV_CACHE is not None:
        return _COMPOSE_ENV_CACHE.copy()
    if _COMPOSE_ENV_ERROR is not None:
        raise RuntimeError(_COMPOSE_ENV_ERROR)

    if shutil.which("docker") is None:
        _COMPOSE_ENV_ERROR = "Docker CLI is required for integration tests"
        raise RuntimeError(_COMPOSE_ENV_ERROR)

    base_env = os.environ.copy()
    ok, message = _docker_info_ok(base_env)
    if ok:
        _COMPOSE_ENV_CACHE = base_env
        return _COMPOSE_ENV_CACHE.copy()

    candidate_hosts: list[str] = []
    for host in (
        os.environ.get("KAMIWAZA_DOCKER_HOST"),
        os.environ.get("DOCKER_HOST"),
        f"unix://{PODMAN_MACHINE_SOCKET}",
    ):
        if host and host not in candidate_hosts:
            candidate_hosts.append(host)

    for host in candidate_hosts:
        env = base_env.copy()
        env["DOCKER_HOST"] = host
        ok, _ = _docker_info_ok(env)
        if ok:
            _COMPOSE_ENV_CACHE = env
            return _COMPOSE_ENV_CACHE.copy()

    _COMPOSE_ENV_ERROR = (
        "Docker daemon is unavailable for integration fixtures. "
        f"Last error: {message or 'docker info failed'}. "
        "Set KAMIWAZA_DOCKER_HOST/DOCKER_HOST to a reachable Podman or Docker socket."
    )
    raise RuntimeError(_COMPOSE_ENV_ERROR)


def _compose_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = _resolve_compose_env()
    if extra_env:
        merged = env.copy()
        merged.update(extra_env)
        return merged
    return env


def _runtime_endpoint(endpoint: str) -> str:
    """Return an endpoint reachable from services running inside the K8s cluster."""

    parsed = urlparse(endpoint)
    host = parsed.hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return endpoint

    target_host = os.environ.get("KAMIWAZA_RUNTIME_HOST", RUNTIME_HOST_ALIAS).strip()
    if not target_host:
        return endpoint

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    replaced = parsed._replace(netloc=f"{target_host}:{port}")
    return replaced.geturl()


def _runtime_host(host: str) -> str:
    if host in {"localhost", "127.0.0.1", "::1"}:
        target = os.environ.get("KAMIWAZA_RUNTIME_HOST", RUNTIME_HOST_ALIAS).strip()
        if target:
            return target
    return host


def _runtime_bootstrap(bootstrap: str) -> str:
    rewritten: list[str] = []
    for endpoint in bootstrap.split(","):
        candidate = endpoint.strip()
        if not candidate:
            continue
        host, sep, port = candidate.partition(":")
        runtime_host = _runtime_host(host)
        rewritten.append(f"{runtime_host}{sep}{port}" if sep else runtime_host)
    return ",".join(rewritten)


def _run_compose(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = ["docker", "compose", "-f", str(DOCKER_COMPOSE_FILE), *args]
    subprocess.run(cmd, check=True, env=_compose_env(env))


def _run_catalog_compose(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = ["docker", "compose", "-f", str(CATALOG_STACK_COMPOSE), *args]
    subprocess.run(cmd, check=True, env=_compose_env(env))


def _catalog_stack_running() -> bool:
    cmd = ["docker", "compose", "-f", str(CATALOG_STACK_COMPOSE), "ps", "-q"]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False, env=_compose_env()
    )
    return bool(result.stdout.strip())


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _reserve_port(preferred: int) -> int:
    if not _port_open("localhost", preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


def _compose_port(
    service: str, container_port: int, env: dict[str, str] | None = None
) -> int | None:
    cmd = [
        "docker",
        "compose",
        "-f",
        str(CATALOG_STACK_COMPOSE),
        "port",
        service,
        str(container_port),
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False, env=_compose_env(env)
    )
    if result.returncode != 0 or not result.stdout:
        return None
    line = result.stdout.strip().splitlines()[0]
    if ":" not in line:
        return None
    try:
        return int(line.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _verify_ssl_enabled() -> bool:
    return os.environ.get("KAMIWAZA_VERIFY_SSL", "true").lower() != "false"


def _api_error_detail(exc: APIError) -> str:
    if isinstance(exc.response_data, dict):
        detail = exc.response_data.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    if exc.response_text and exc.response_text.strip():
        return exc.response_text.strip()
    return str(exc)


def _classify_platform_model_type(purpose: str | None, model_name: str) -> str:
    if purpose:
        lowered = purpose.lower()
        if "embed" in lowered:
            return "embedding"
        if lowered in {"vl", "vision"}:
            return "vl"
        return "llm"

    if _EMBEDDING_NAME_PATTERNS.search(model_name):
        return "embedding"
    return "llm"


def _platform_deployment_ready(deployment: object) -> bool:
    deployment_status = str(getattr(deployment, "status", "")).upper()
    if deployment_status != "DEPLOYED":
        return False
    instances = getattr(deployment, "instances", []) or []
    return any(
        str(getattr(instance, "status", "")).upper() == "DEPLOYED"
        for instance in instances
    )


def _stop_deployment_quietly(
    client: KamiwazaClient, deployment_id: str | None
) -> None:
    """Best-effort stop of a deployment (used for capability probes / cleanup)."""
    if not deployment_id:
        return
    try:
        client.serving.stop_deployment(deployment_id=deployment_id, force=True)
    except Exception:  # noqa: BLE001 — teardown is best-effort
        pass


def _active_model_deployments(
    client: KamiwazaClient, *, desired_type: str
) -> list[dict[str, str]]:
    try:
        deployments = client.serving.list_active_deployments()
    except APIError:
        return []

    active_deployments: list[dict[str, str]] = []
    for deployment in deployments:
        purpose: str | None = None
        model_name = str(getattr(deployment, "m_name", "") or "")
        repo_model_id = ""

        try:
            model = client.models.get_model(deployment.m_id)
        except Exception:
            model = None
        if model is not None:
            purpose = getattr(model, "purpose", None)
            model_name = str(getattr(model, "name", "") or model_name)
            repo_model_id = str(getattr(model, "repo_modelId", "") or "")

        if _classify_platform_model_type(purpose, model_name) != desired_type:
            continue

        active_deployments.append(
            {
                "deployment_id": str(deployment.id),
                "model_id": str(deployment.m_id),
                "model_name": model_name,
                "repo_model_id": repo_model_id,
            }
        )

    return active_deployments


def _preferred_active_model_deployment(
    client: KamiwazaClient,
    *,
    desired_type: str,
    preferred_repo_id: str = "",
) -> dict[str, str] | None:
    deployments = _active_model_deployments(client, desired_type=desired_type)
    preferred_repo_id = preferred_repo_id.strip()
    if preferred_repo_id:
        for deployment in deployments:
            if deployment["repo_model_id"] == preferred_repo_id:
                return deployment
    return deployments[0] if deployments else None


def _active_embedding_deployment(client: KamiwazaClient) -> dict[str, str] | None:
    return _preferred_active_model_deployment(
        client,
        desired_type="embedding",
        preferred_repo_id=EMBEDDING_TEST_MODEL_REPO,
    )


def _unique_nonempty_values(*values: str) -> list[str]:
    unique_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = str(value or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        unique_values.append(candidate)
    return unique_values


def _embedding_provider_candidates(client: KamiwazaClient) -> list[str]:
    if EMBEDDING_TEST_PROVIDER:
        return [EMBEDDING_TEST_PROVIDER]

    try:
        available = client.embedding.get_providers()
    except APIError:
        available = []

    ordered = [
        provider
        for provider in ("sentence_transformers", "huggingface_embedding")
        if provider in available
    ]
    ordered.extend(provider for provider in available if provider not in ordered)
    if ordered:
        return ordered
    return ["sentence_transformers", "huggingface_embedding"]


def _probe_embedding_target(
    client: KamiwazaClient, *, model: str, provider_type: str
) -> str | None:
    try:
        response = client.post(
            "/embedding/generate",
            json={
                "text": EMBEDDING_TEST_PROBE_TEXT,
                "model": model,
                "provider_type": provider_type,
            },
        )
    except APIError as exc:
        return _api_error_detail(exc)

    embedding = response.get("embedding") if isinstance(response, dict) else None
    if isinstance(embedding, list) and embedding:
        return None
    return "embedding endpoint returned no embedding data"


def _maybe_request_embedding_download_and_deploy(
    client: KamiwazaClient,
    *,
    repo_id: str,
    hub: str,
) -> str | None:
    try:
        response = client.post(
            "/models/download_and_deploy",
            json={
                "model": repo_id,
                "hub": hub,
                "deploy_after_download": True,
            },
        )
    except APIError as exc:
        return _api_error_detail(exc)

    if isinstance(response, dict):
        for key in ("message", "detail", "status"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _maybe_nudge_embedding_deploy(
    client: KamiwazaClient, *, repo_id: str
) -> str | None:
    try:
        response = client.post(
            f"/models/deploy_after_download/{repo_id}",
            json={"novice_selected_context": EMBEDDING_TEST_DEPLOY_CONTEXT},
        )
    except APIError as exc:
        return _api_error_detail(exc)

    if isinstance(response, dict):
        for key in ("message", "detail", "status"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _wait_for_active_embedding_deployment(
    client: KamiwazaClient,
    *,
    repo_id: str,
    timeout_seconds: float,
    poll_seconds: float,
    nudge_after_seconds: float,
) -> tuple[dict[str, str] | None, str | None]:
    deadline = time.monotonic() + timeout_seconds
    nudge_deadline = time.monotonic() + max(0.0, nudge_after_seconds)
    nudged = False
    last_message: str | None = None

    while time.monotonic() < deadline:
        deployment = _active_embedding_deployment(client)
        if deployment is not None:
            return deployment, last_message

        if not nudged and time.monotonic() >= nudge_deadline:
            last_message = _maybe_nudge_embedding_deploy(client, repo_id=repo_id)
            nudged = True

        time.sleep(poll_seconds)

    return None, last_message


class _NoCacheTokenStore(TokenStore):
    """Disable token persistence/loading during credential validation checks."""

    def load(self) -> StoredToken | None:
        return None

    def save(self, token: StoredToken) -> None:
        return None

    def clear(self) -> None:
        return None


def _resolve_kz_login_password() -> str | None:
    """Attempt to load the current local admin password from deploy helper script."""

    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        repo_root.parent / "deploy" / "scripts" / "kz-login",
    ]
    kamiwaza_root = os.environ.get("KAMIWAZA_ROOT")
    if kamiwaza_root:
        candidates.append(
            Path(kamiwaza_root).expanduser() / "deploy" / "scripts" / "kz-login"
        )

    for script in candidates:
        if not script.exists():
            continue
        try:
            result = subprocess.run(
                [str(script), "--show-password"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
            continue
        password = result.stdout.strip()
        if password:
            return password
    return None


@pytest.fixture(scope="session", autouse=True)
def http_trace_logging() -> Iterator[None]:
    """Optionally trace all HTTP traffic made through requests to a JSONL file."""

    if not _http_trace_enabled():
        yield
        return

    trace_path_value = os.environ.get(_HTTP_TRACE_FILE_ENV, "").strip()
    if trace_path_value:
        trace_path = Path(trace_path_value).expanduser()
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text("", encoding="utf-8")
    else:
        fd, temp_path = tempfile.mkstemp(prefix="kamiwaza-http-trace-", suffix=".jsonl")
        os.close(fd)
        trace_path = Path(temp_path)
        os.environ[_HTTP_TRACE_FILE_ENV] = str(trace_path)

    writer = _HTTPTraceWriter(trace_path)
    original_send = requests.sessions.Session.send
    next_request_id = 0

    print(f"HTTP trace enabled: {trace_path}")

    def traced_send(session, request, **kwargs):
        nonlocal next_request_id
        next_request_id += 1
        request_id = next_request_id
        started_at = time.time()
        writer.write(
            "request",
            request_id=request_id,
            method=request.method,
            url=request.url,
            headers=list(request.headers.items()),
            body=_trace_request_body(request),
            stream=bool(kwargs.get("stream")),
            verify=kwargs.get("verify"),
            allow_redirects=kwargs.get("allow_redirects"),
        )
        try:
            response = original_send(session, request, **kwargs)
        except requests.RequestException as exc:
            writer.write(
                "request-exception",
                request_id=request_id,
                elapsed_ms=round((time.time() - started_at) * 1000, 3),
                exception_type=type(exc).__name__,
                message=str(exc),
            )
            raise

        writer.write(
            "response-head",
            request_id=request_id,
            elapsed_ms=round((time.time() - started_at) * 1000, 3),
            status_code=response.status_code,
            reason=response.reason,
            url=response.url,
            headers=list(response.headers.items()),
            streamed=bool(kwargs.get("stream")),
        )

        if kwargs.get("stream"):
            return _wrap_streaming_response(
                response,
                writer,
                request_id=request_id,
                started_at=started_at,
            )

        writer.write(
            "response-body",
            request_id=request_id,
            streamed=False,
            complete=True,
            elapsed_ms=round((time.time() - started_at) * 1000, 3),
            body=_trace_body_payload(
                response.content,
                content_type=response.headers.get("Content-Type", ""),
                encoding=response.encoding,
            ),
        )
        return response

    requests.sessions.Session.send = traced_send
    try:
        yield
    finally:
        requests.sessions.Session.send = original_send


def _password_auth_works(
    base_url: str, username: str, password: str
) -> tuple[bool, str]:
    """Validate username/password by calling /auth/users/me."""

    client = KamiwazaClient(base_url)
    _mount_probe_timeout(client)
    client.authenticator = UserPasswordAuthenticator(
        username,
        password,
        client._auth_service,
        token_store=_NoCacheTokenStore(),
    )
    try:
        client.auth.get_current_user()
        return True, ""
    except (KamiwazaError, requests.RequestException) as exc:
        return False, str(exc)
    finally:
        client.session.close()


def _api_key_auth_works(base_url: str, api_key: str) -> tuple[bool, str]:
    """Validate a PAT/API key by calling /auth/users/me.

    Stale PATs left over from a prior platform install (e.g. ``.env.local`` from
    before Keycloak's signing keys were rotated during a reinstall) will parse
    as valid JWTs but fail verification with 401 ``Not authenticated`` because
    the signing ``kid`` is unknown to the new platform. Probing /auth/users/me
    lets the fixture chain detect that case and fall through to password-based
    PAT creation instead of handing every test a dead token.

    Results are memoized per ``(base_url, api_key)`` so the session-scoped
    ``live_session_api_key`` and ``live_session_write_key`` fixtures don't each
    issue their own probe round-trip.
    """

    cache_key = (base_url, api_key)
    cached = _API_KEY_PROBE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    client = KamiwazaClient(base_url, api_key=api_key)
    _mount_probe_timeout(client)
    try:
        client.auth.get_current_user()
        result = (True, "")
    except (KamiwazaError, requests.RequestException) as exc:
        result = (False, str(exc))
    finally:
        client.session.close()
    _API_KEY_PROBE_CACHE[cache_key] = result
    return result


def _resolve_live_password_once(
    *,
    live_server_available: str,
    live_username: str,
    configured_password: str,
) -> tuple[str, str | None]:
    """
    Resolve password auth at most once for a given session configuration.

    Pytest does not cache skipped fixture setup. Without this cache, a lockout or
    bad fallback password can trigger dozens of extra password grants as each test
    retries the same session-scoped fixture chain.
    """

    cache_key = (
        live_server_available,
        live_username.strip(),
        configured_password.strip(),
    )
    cached = _LIVE_PASSWORD_CACHE.get(cache_key)
    if cached is not None:
        return cached

    username = live_username.strip()
    configured_password = configured_password.strip()
    if not username:
        result = ("", None)
        _LIVE_PASSWORD_CACHE[cache_key] = result
        return result

    errors: list[str] = []

    # The kube-backed password is the authoritative dev credential. Try it first
    # when available, then fall through to the configured password if kz-login
    # returned a stale/invalid value (e.g. freshly-rotated admin password not
    # yet propagated to the cached fallback).
    fallback_password = _resolve_kz_login_password()
    if fallback_password:
        ok, error = _password_auth_works(
            live_server_available,
            username,
            fallback_password,
        )
        if ok:
            os.environ["KAMIWAZA_PASSWORD"] = fallback_password
            result = (fallback_password, None)
            _LIVE_PASSWORD_CACHE[cache_key] = result
            return result
        errors.append(f"kz-login password failed: {error}")
    else:
        errors.append("kz-login fallback unavailable")

    if configured_password:
        ok, error = _password_auth_works(
            live_server_available,
            username,
            configured_password,
        )
        if ok:
            result = (configured_password, None)
            _LIVE_PASSWORD_CACHE[cache_key] = result
            return result
        errors.append(f"configured password failed: {error}")
    else:
        errors.append("configured password is empty")

    result = ("", "; ".join(errors))
    _LIVE_PASSWORD_CACHE[cache_key] = result
    return result


@pytest.fixture(scope="session")
def live_server_available(live_base_url: str) -> str:
    """Ensure a running Kamiwaza server is reachable before running live tests.

    Retries a few times, then ``pytest.exit`` if the server is unreachable or
    unhealthy. Earlier versions raised ``pytest.skip`` here, which pytest caches
    on a session-scoped fixture and replays as a skip on every dependent test —
    producing a green report with 165+ silent skips when the platform was
    actually down (see ENG-6504). Exiting fails the run loudly with the actual
    cause; tests that intentionally don't need a live server should not depend
    on this fixture.

    Auth-related skips in sibling fixtures (``live_kamiwaza_client``,
    ``resolved_live_password``) stay as ``pytest.skip`` — missing credentials
    is a legitimate opt-out, distinct from "infrastructure is broken."
    """

    health_url = f"{live_base_url}/ping"
    os.environ.setdefault("KAMIWAZA_VERIFY_SSL", "false")
    if not _verify_ssl_enabled():
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    attempts = 3
    per_attempt_timeout = 10
    backoff_seconds = 2
    last_exc: Exception | None = None
    response: requests.Response | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(
                health_url,
                timeout=per_attempt_timeout,
                verify=_verify_ssl_enabled(),
            )
            break
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(backoff_seconds)
    # pytest's exit code 2 normally means EXIT_INTERRUPTED. We reuse it here
    # because "infrastructure unreachable" is operationally the same shape
    # from a pipeline perspective: the run did not complete normally, and any
    # caller that already treats non-zero pytest exits as failure (e.g. the
    # kajiya smoke runner) handles it without special-casing. The collision is
    # intentional and documented.
    _INFRA_UNAVAILABLE_RETURNCODE = 2
    if response is None:
        pytest.exit(
            f"Kamiwaza server unavailable at {live_base_url} after {attempts} attempts: {last_exc}",
            returncode=_INFRA_UNAVAILABLE_RETURNCODE,
        )
    if response.status_code >= 500:
        pytest.exit(
            f"Kamiwaza server unhealthy at {health_url}: {response.status_code}",
            returncode=_INFRA_UNAVAILABLE_RETURNCODE,
        )
    if response.status_code >= 400 and response.status_code not in (401, 403):
        pytest.exit(
            f"Kamiwaza server at {health_url} returned unexpected status {response.status_code}; "
            "check base URL or ping route configuration.",
            returncode=_INFRA_UNAVAILABLE_RETURNCODE,
        )
    return live_base_url


@pytest.fixture(scope="session")
def qwen_snapshot_dir(hf_cache_dir: Path) -> Path:
    """Ensure the canonical MLX model README is cached locally."""

    repo_id = "mlx-community/Qwen3-4B-4bit"
    snapshot_path = snapshot_download(
        repo_id,
        cache_dir=hf_cache_dir,
        allow_patterns=["README.md"],
        repo_type="model",
    )
    return Path(snapshot_path)


@pytest.fixture
def live_kamiwaza_client(
    live_server_available: str,
    live_session_api_key: str,
    resolved_live_password: str,
    live_username: str,
) -> KamiwazaClient:
    """Provide an authenticated client for integration tests."""
    os.environ.setdefault("KAMIWAZA_VERIFY_SSL", "false")

    api_key = live_session_api_key.strip()
    if api_key:
        return KamiwazaClient(live_server_available, api_key=api_key)

    username = live_username.strip()
    password = resolved_live_password.strip()
    if username and password:
        client = KamiwazaClient(live_server_available)
        client.authenticator = UserPasswordAuthenticator(
            username, password, client._auth_service, token_store=_NoCacheTokenStore()
        )
        return client

    pytest.skip(
        "Unable to build authenticated live client. "
        "Provide username/password (kz-login-backed) or KAMIWAZA_API_KEY."
    )


@pytest.fixture(scope="session")
def live_kamiwaza_session_client(
    live_server_available: str,
    live_session_api_key: str,
    resolved_live_password: str,
    live_username: str,
) -> KamiwazaClient:
    """Session-scoped authenticated client for shared live prerequisites."""
    os.environ.setdefault("KAMIWAZA_VERIFY_SSL", "false")

    api_key = live_session_api_key.strip()
    if api_key:
        return KamiwazaClient(live_server_available, api_key=api_key)

    username = live_username.strip()
    password = resolved_live_password.strip()
    if username and password:
        client = KamiwazaClient(live_server_available)
        client.authenticator = UserPasswordAuthenticator(
            username, password, client._auth_service, token_store=_NoCacheTokenStore()
        )
        return client

    pytest.skip(
        "Unable to build authenticated live client. "
        "Provide username/password (kz-login-backed) or KAMIWAZA_API_KEY."
    )


@pytest.fixture(scope="session")
def embedding_model_prerequisite(
    live_kamiwaza_session_client: KamiwazaClient,
) -> dict[str, str]:
    """Ensure the live platform has an active embedding deployment or skip once."""
    client = live_kamiwaza_session_client

    deployment = _active_embedding_deployment(client)
    if deployment is not None:
        return deployment

    request_message = _maybe_request_embedding_download_and_deploy(
        client,
        repo_id=EMBEDDING_TEST_MODEL_REPO,
        hub=EMBEDDING_TEST_MODEL_HUB,
    )

    deployment, wait_message = _wait_for_active_embedding_deployment(
        client,
        repo_id=EMBEDDING_TEST_MODEL_REPO,
        timeout_seconds=EMBEDDING_TEST_DEPLOY_TIMEOUT_SECONDS,
        poll_seconds=EMBEDDING_TEST_DEPLOY_POLL_SECONDS,
        nudge_after_seconds=EMBEDDING_TEST_DEPLOY_NUDGE_SECONDS,
    )
    if deployment is not None:
        return deployment

    details = [f"repo={EMBEDDING_TEST_MODEL_REPO}"]
    if request_message:
        details.append(f"download_and_deploy={request_message}")
    if wait_message and wait_message != request_message:
        details.append(f"deploy_after_download={wait_message}")
    pytest.skip(
        "No active platform embedding deployment was found and the test harness "
        f"could not confirm one after attempting deployment ({'; '.join(details)})."
    )


@pytest.fixture(scope="session")
def embedding_test_target(
    live_kamiwaza_session_client: KamiwazaClient,
    embedding_model_prerequisite: dict[str, str],
) -> dict[str, str]:
    """Pick an embedding endpoint target, preferring the active platform model."""
    client = live_kamiwaza_session_client
    provider_candidates = _embedding_provider_candidates(client)
    model_candidates = _unique_nonempty_values(
        embedding_model_prerequisite.get("repo_model_id", ""),
        embedding_model_prerequisite.get("model_name", ""),
        EMBEDDING_TEST_MODEL_REPO,
    )

    failures: list[str] = []
    for model in model_candidates:
        for provider_type in provider_candidates:
            probe_error = _probe_embedding_target(
                client,
                model=model,
                provider_type=provider_type,
            )
            if probe_error is None:
                return {
                    "model": model,
                    "provider_type": provider_type,
                }
            failures.append(f"{model}/{provider_type}: {probe_error}")

    pytest.skip(
        "Embedding deployment is present, but the embedding endpoint could not use "
        f"any preferred test target ({'; '.join(failures)})."
    )


@pytest.fixture(scope="session")
def context_llm_prerequisite(
    live_kamiwaza_session_client: KamiwazaClient,
    ensure_repo_ready,
) -> Iterator[str]:
    """Ensure a usable LLM deployment exists for context ontology operations, or skip once.

    Mirrors ``embedding_model_prerequisite``: if no LLM is already deployed the
    fixture attempts to provision one, but it **skips** (does not error) when the
    platform cannot bring one up — e.g. a CPU-only smoke host with no inference
    capacity, or an MLX-only test model on a non-Apple-Silicon runner. This keeps
    the context ontology/vectordb tests as conditional skips on incapable hosts
    instead of a cascade of fixture-setup ERRORs.
    """
    client = live_kamiwaza_session_client

    def _stop_provisioned(deployment_id: str | None) -> None:
        """Best-effort teardown of a deployment THIS fixture provisioned."""
        if not deployment_id:
            return
        try:
            client.serving.stop_deployment(deployment_id=deployment_id, force=True)
        except Exception:  # noqa: BLE001 — teardown is best-effort
            pass

    existing = _preferred_active_model_deployment(
        client,
        desired_type="llm",
        preferred_repo_id=CONTEXT_TEST_LLM_REPO,
    )
    if existing is not None:
        yield existing["deployment_id"]
        return

    # Track the id as soon as deploy_model returns so the skip/teardown paths can
    # stop it — otherwise a deploy that succeeds but never becomes ready (e.g. a
    # capacity-limited host) would be orphaned when we skip.
    provisioned_deployment_id: str | None = None
    try:
        model = ensure_repo_ready(client, CONTEXT_TEST_LLM_REPO)
        configs = client.models.get_model_configs(model.id)
        if not configs:
            pytest.skip(
                f"No model configs available for context LLM repo '{CONTEXT_TEST_LLM_REPO}'"
            )
        default_config = next(
            (config for config in configs if config.default), configs[0]
        )

        # deploy_model returns Union[UUID, bool] — False on failure (no raise).
        # Guard before str() so a refused deploy skips cleanly instead of turning
        # into the truthy id "False".
        raw_deployment_id = client.serving.deploy_model(
            model_id=str(model.id),
            m_config_id=default_config.id,
            lb_port=0,
            autoscaling=False,
            min_copies=1,
            starting_copies=1,
            # The fixture's wait_for_deployment below owns the timeout; the
            # SDK-internal wait would block up to its own 3600s default first.
            wait=False,
        )
        if not raw_deployment_id:
            pytest.skip(
                "deploy_model did not return a deployment id for context LLM repo "
                f"'{CONTEXT_TEST_LLM_REPO}' (deploy refused on this host)."
            )
        provisioned_deployment_id = str(raw_deployment_id)
        deployment = client.serving.wait_for_deployment(
            provisioned_deployment_id,
            poll_interval=5,
            timeout=CONTEXT_TEST_LLM_DEPLOY_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — any provisioning failure → skip, not error
        _stop_provisioned(provisioned_deployment_id)
        pytest.skip(
            "No active LLM deployment for context ontology tests and one could not "
            f"be provisioned (repo={CONTEXT_TEST_LLM_REPO}): "
            f"{type(exc).__name__}: {exc}"
        )

    if not _platform_deployment_ready(deployment):
        _stop_provisioned(provisioned_deployment_id)
        pytest.skip(
            "Context ontology prerequisite LLM deployment did not become ready: "
            f"deployment_id={deployment.id}, status={deployment.status}, "
            f"instance_statuses={[instance.status for instance in deployment.instances]}"
        )

    try:
        yield provisioned_deployment_id
    finally:
        _stop_provisioned(provisioned_deployment_id)


@pytest.fixture(scope="session")
def deployable_model_prerequisite(
    live_kamiwaza_session_client: KamiwazaClient,
    ensure_repo_ready,
) -> None:
    """Skip once if this host cannot deploy the integration test model.

    Serving/CLI tests that deploy ``DEPLOYABLE_TEST_MODEL_REPO`` (an MLX model)
    fail with a 5xx on hosts without compatible inference capacity — e.g. the
    x86 CPU Azure smoke. Probe deployability once per session and skip the
    marked tests instead of failing them. Mirrors ``embedding_model_prerequisite``
    / ``context_llm_prerequisite``; the probe tears down its own deployment so
    dependent tests perform their own deploys.
    """
    client = live_kamiwaza_session_client
    # Short-circuit ONLY when the exact test model is already deployed — a
    # different active LLM does not prove this host can deploy DEPLOYABLE_TEST_MODEL_REPO
    # (e.g. MLX on x86), so in that case we must still probe.
    existing = _preferred_active_model_deployment(
        client,
        desired_type="llm",
        preferred_repo_id=DEPLOYABLE_TEST_MODEL_REPO,
    )
    if existing is not None and existing.get("repo_model_id") == DEPLOYABLE_TEST_MODEL_REPO:
        return  # the test model itself is already deployed → host is capable

    probe_deployment_id: str | None = None
    try:
        model = ensure_repo_ready(client, DEPLOYABLE_TEST_MODEL_REPO)
        configs = client.models.get_model_configs(model.id)
        if not configs:
            pytest.skip(
                "No model configs available for deployable test model "
                f"'{DEPLOYABLE_TEST_MODEL_REPO}'"
            )
        default_config = next(
            (config for config in configs if config.default), configs[0]
        )
        raw_deployment_id = client.serving.deploy_model(
            model_id=str(model.id),
            m_config_id=default_config.id,
            lb_port=0,
            autoscaling=False,
            min_copies=1,
            starting_copies=1,
            # The probe's wait_for_deployment below owns the timeout
            # (DEPLOYABLE_TEST_DEPLOY_TIMEOUT_SECONDS), not the SDK default.
            wait=False,
        )
        if not raw_deployment_id:
            pytest.skip(
                f"deploy_model returned no id for '{DEPLOYABLE_TEST_MODEL_REPO}' "
                "(deploy refused on this host)."
            )
        probe_deployment_id = str(raw_deployment_id)
        deployment = client.serving.wait_for_deployment(
            probe_deployment_id,
            poll_interval=5,
            timeout=DEPLOYABLE_TEST_DEPLOY_TIMEOUT_SECONDS,
        )
    except (TimeoutError, RuntimeError) as exc:
        # Download/registration timeout, or the deployment entering FAILED/ERROR
        # status because the instance can't load the model on this host
        # (RuntimeError from wait_for_deployment, kamiwaza_sdk/services/serving.py)
        # — capability/infra failure → skip + tear down.
        _stop_deployment_quietly(client, probe_deployment_id)
        pytest.skip(
            "Host cannot provision integration test model (download/deploy) "
            f"'{DEPLOYABLE_TEST_MODEL_REPO}': {type(exc).__name__}: {exc}"
        )
    except APIError as exc:
        # Only a 5xx (server cannot bring the model up on this host) is a
        # capability failure → skip. A 4xx (auth / scope / validation /
        # request-shape) is a real regression and MUST fail, not be masked as a
        # skip, so it is re-raised.
        status_code = getattr(exc, "status_code", None)
        _stop_deployment_quietly(client, probe_deployment_id)
        if status_code is None or status_code < 500:
            raise
        pytest.skip(
            "Host cannot provision integration test model (download/deploy) "
            f"'{DEPLOYABLE_TEST_MODEL_REPO}': APIError {status_code}: {exc}"
        )

    ready = _platform_deployment_ready(deployment)
    _stop_deployment_quietly(client, probe_deployment_id)
    if not ready:
        pytest.skip(
            f"Integration test model '{DEPLOYABLE_TEST_MODEL_REPO}' did not become "
            "ready on this host."
        )


@pytest.fixture(autouse=True)
def _require_embedding_model_for_marked_tests(request: pytest.FixtureRequest) -> None:
    if "requires_embedding_model" in request.keywords:
        request.getfixturevalue("embedding_model_prerequisite")


@pytest.fixture(autouse=True)
def _require_deployable_model_for_marked_tests(request: pytest.FixtureRequest) -> None:
    if "requires_deployable_model" in request.keywords:
        request.getfixturevalue("deployable_model_prerequisite")


@pytest.fixture(autouse=True)
def _require_two_clusters_for_marked_tests(request: pytest.FixtureRequest) -> None:
    """ENG-5784 — surface a clear skip reason when peer creds are missing.

    Collection-side deselection in ``pytest_collection_modifyitems`` removes
    these tests entirely when neither --live-peer-base-url nor
    KAMIWAZA_PEER_BASE_URL is set, so this fixture only fires when peer
    creds were partially provided (e.g. base URL but no API key).
    """
    if "requires_two_clusters" not in request.keywords:
        return
    peer_url = str(request.config.getoption("live_peer_base_url")).strip()
    peer_key = str(request.config.getoption("live_peer_api_key")).strip()
    if not peer_url:
        pytest.skip(
            "requires_two_clusters: set --live-peer-base-url or "
            "KAMIWAZA_PEER_BASE_URL to run."
        )
    if not peer_key:
        pytest.skip(
            "requires_two_clusters: --live-peer-base-url is set but "
            "--live-peer-api-key / KAMIWAZA_PEER_API_KEY is missing."
        )


@pytest.fixture(scope="session")
def cluster_capability_snapshot(
    live_kamiwaza_session_client: KamiwazaClient,
) -> _cap.ClusterCapabilitySnapshot | None:
    """Session-cached GPU/node inventory backing the capability markers (M5).

    Built once from the live cluster via the SDK client. Returns ``None`` when
    inventory can't be fetched so capability-marked tests skip (not fail) with a
    clear reason rather than erroring.
    """
    client = live_kamiwaza_session_client
    try:
        hardware = client.cluster.list_hardware()
    except Exception:  # noqa: BLE001 - inventory unavailable => undeterminable
        hardware = None
    node_count: int | None = None
    try:
        nodes = client.cluster.get_running_nodes()
        node_count = sum(
            1 for node in nodes if getattr(node, "alive", True) is not False
        )
    except Exception:  # noqa: BLE001
        node_count = None
    if hardware is None and node_count is None:
        return None
    return _cap.build_capability_snapshot(hardware or [], node_count=node_count)


@pytest.fixture(autouse=True)
def _enforce_capability_markers(request: pytest.FixtureRequest) -> None:
    """Skip (never fail) capability-marked tests on under-provisioned hosts (M5).

    Honors ``@pytest.mark.min_gpu_count(N)`` / ``min_gpu_mem(GB)`` /
    ``gpu_vendor("nvidia"|"amd"|"none")`` / ``gpu_mig_support`` /
    ``min_node_count(N)``. Tests without a capability marker are unaffected
    (the snapshot fixture — and the live client it needs — are never touched).
    """
    marks = [
        mark
        for mark in request.node.iter_markers()
        if mark.name in _cap.CAPABILITY_MARKER_NAMES
    ]
    if not marks:
        return
    requirements = _cap.collect_capability_requirements(marks)
    snapshot = request.getfixturevalue("cluster_capability_snapshot")
    reason = _cap.evaluate_capability_requirements(snapshot, requirements)
    if reason:
        pytest.skip(reason)


@pytest.fixture(scope="session")
def live_kamiwaza_peer_client(
    live_peer_base_url: str,
    live_peer_api_key: str,
) -> KamiwazaClient:
    """ENG-5784 — KamiwazaClient bound to the federation peer cluster.

    Only resolves when both KAMIWAZA_PEER_BASE_URL + KAMIWAZA_PEER_API_KEY are
    configured. Two-cluster tests should depend on this fixture alongside
    the @pytest.mark.requires_two_clusters marker.

    SSL verification is opted out per-client (dev clusters typically run
    with self-signed certs) so the toggle is scoped to this client rather
    than mutating process-wide environment.
    """
    if not live_peer_base_url:
        pytest.skip("requires_two_clusters: KAMIWAZA_PEER_BASE_URL not set")
    if not live_peer_api_key:
        pytest.skip("requires_two_clusters: KAMIWAZA_PEER_API_KEY not set")
    return KamiwazaClient(
        live_peer_base_url,
        api_key=live_peer_api_key.strip(),
        verify=False,
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Order: smoke first, embedding-dependent next, others last.

    ENG-5784: also deselect @pytest.mark.requires_two_clusters when neither
    --live-peer-base-url nor KAMIWAZA_PEER_BASE_URL is set. Mirrors the
    requires_embedding_model deselection convention so contributor PRs
    without peer creds don't show false reds.
    """
    peer_url = str(config.getoption("live_peer_base_url")).strip()
    if not peer_url:
        kept: list[pytest.Item] = []
        deselected: list[pytest.Item] = []
        for item in items:
            if "requires_two_clusters" in item.keywords:
                deselected.append(item)
            else:
                kept.append(item)
        if deselected:
            config.hook.pytest_deselected(items=deselected)
            items[:] = kept

    smoke_items = [
        item for item in items if Path(str(item.fspath)).name.startswith("test_00_")
    ]
    remaining_items = [
        item for item in items if not Path(str(item.fspath)).name.startswith("test_00_")
    ]
    embedding_items = [
        item for item in remaining_items if "requires_embedding_model" in item.keywords
    ]
    if not smoke_items and not embedding_items:
        return

    other_items = [
        item
        for item in remaining_items
        if "requires_embedding_model" not in item.keywords
    ]
    items[:] = smoke_items + embedding_items + other_items


@pytest.fixture(scope="session")
def resolved_live_password(
    live_server_available: str,
    live_api_key: str,
    live_username: str,
    pytestconfig: pytest.Config,
) -> str:
    """
    Resolve live password with kube-derived credentials first (kz-login),
    then explicit configured password as fallback.

    Session-scoped and backed by ``_LIVE_PASSWORD_CACHE`` inside
    ``_resolve_live_password_once`` so kz-login / password grants run at most
    once per session. Password-authentication tests
    (``test_password_authentication_allows_whoami``, PAT-lifecycle, CLI login)
    consume this fixture directly, so it must always resolve a real password
    when one is available — returning an empty short-circuit string here
    regresses those tests.
    """

    env_api_key = live_api_key.strip()
    password, error = _resolve_live_password_once(
        live_server_available=live_server_available,
        live_username=live_username,
        configured_password=str(pytestconfig.getoption("live_password")),
    )
    if password or env_api_key:
        return password

    username = live_username.strip()
    pytest.skip(
        "Unable to authenticate live integration client via username/password "
        f"(user='{username}', details: {error})"
    )


@pytest.fixture(scope="session")
def live_session_api_key(
    live_server_available: str,
    live_api_key: str,
    resolved_live_password: str,
    live_username: str,
) -> Iterator[str]:
    """
    Session PAT with **admin** scope for integration tests.

    Uses scope="admin" so the PAT carries the admin role required by cluster,
    model-config, retrieval, and serving endpoints.  Without admin scope the
    server's scope mapping (scope_mapping.py) resolves to "write" which only
    includes ["user", "editor", "viewer"] — causing ~22 403 failures.

    See also ``live_session_write_key`` for a write-scoped PAT used by tests
    that should *not* require admin privileges (authorization-regression guard).

    Auth-specific tests still use live_username/live_password directly, but the
    shared client fixture should not re-run password grants across the whole suite.

    Env-supplied ``KAMIWAZA_API_KEY`` values are validated against
    ``/auth/users/me`` before use. Stale PATs left behind by a prior platform
    install (same ``.env.local``, new Keycloak signing keys after an
    ``install-dev.sh`` reinstall) would otherwise poison every test with the
    generic ``AuthenticationError: Authentication failed after token refresh``
    signature. If the env PAT fails probe, fall through to password-based PAT
    creation instead.
    """

    api_key = live_api_key.strip()
    if api_key:
        ok, error = _api_key_auth_works(live_server_available, api_key)
        if ok:
            yield api_key
            return
        # Stale PAT (e.g. signed by a pre-reinstall Keycloak key). Fall through
        # to password-based PAT creation so the rest of the suite can run.
        # Cap the error text so a gateway 502/503 HTML body (or any other
        # verbose non-401 failure surfaced via ``APIError.__str__``) doesn't
        # flood CI logs / pytest output.
        truncated = error[:_PROBE_ERROR_TRUNCATE]
        if len(error) > _PROBE_ERROR_TRUNCATE:
            truncated += "..."
        _logger.warning(
            "env KAMIWAZA_API_KEY rejected by platform (%s); "
            "falling back to password-based PAT creation.",
            truncated,
        )

    username = live_username.strip()
    password = resolved_live_password.strip()
    if not username or not password:
        yield ""
        return

    bootstrap_client = KamiwazaClient(live_server_available)
    bootstrap_client.authenticator = UserPasswordAuthenticator(
        username,
        password,
        bootstrap_client._auth_service,
        token_store=_NoCacheTokenStore(),
    )

    pat_response = bootstrap_client.auth.create_pat(
        PATCreate(
            name=f"sdk-integration-{uuid.uuid4().hex[:10]}",
            ttl_seconds=4 * 60 * 60,
            scope="admin",
            aud="kamiwaza-platform",
        )
    )
    try:
        yield pat_response.token
    finally:
        try:
            bootstrap_client.auth.revoke_pat(pat_response.pat.jti)
        except (AuthenticationError, APIError):
            pass


@pytest.fixture(scope="session")
def live_session_write_key(
    live_server_available: str,
    live_api_key: str,
    resolved_live_password: str,
    live_username: str,
) -> Iterator[str]:
    """
    Session PAT with **write** scope for authorization-regression tests.

    Tests using ``live_write_client`` run at write scope (roles: user, editor,
    viewer — no admin).  If an endpoint that should work for regular users
    starts requiring admin, these tests will surface the regression as a 403.
    """

    # Only skip when the env PAT is usable — otherwise a stale env PAT would
    # also prevent the write-scope regression guard from running.
    api_key = live_api_key.strip()
    if api_key and _api_key_auth_works(live_server_available, api_key)[0]:
        yield ""
        return

    username = live_username.strip()
    password = resolved_live_password.strip()
    if not username or not password:
        yield ""
        return

    bootstrap_client = KamiwazaClient(live_server_available)
    bootstrap_client.authenticator = UserPasswordAuthenticator(
        username,
        password,
        bootstrap_client._auth_service,
        token_store=_NoCacheTokenStore(),
    )

    pat_response = bootstrap_client.auth.create_pat(
        PATCreate(
            name=f"sdk-integration-write-{uuid.uuid4().hex[:10]}",
            ttl_seconds=4 * 60 * 60,
            scope="write",
            aud="kamiwaza-platform",
        )
    )
    try:
        yield pat_response.token
    finally:
        try:
            bootstrap_client.auth.revoke_pat(pat_response.pat.jti)
        except (AuthenticationError, APIError):
            pass


@pytest.fixture
def live_write_client(
    live_server_available: str,
    live_session_write_key: str,
) -> KamiwazaClient:
    """Client authenticated at write scope (no admin role).

    Use this fixture for tests that verify non-admin endpoints work without
    elevated privileges.
    """
    os.environ.setdefault("KAMIWAZA_VERIFY_SSL", "false")

    api_key = live_session_write_key.strip()
    if not api_key:
        pytest.skip("Write-scoped session PAT unavailable")

    return KamiwazaClient(live_server_available, api_key=api_key)


@pytest.fixture(scope="session")
def live_password(resolved_live_password: str) -> str:
    """Override base fixture so integration tests get resolved/fallback password."""
    return resolved_live_password


@pytest.fixture(scope="session")
def ensure_repo_ready() -> Callable[[KamiwazaClient, str], object]:
    """Ensure a Hugging Face repo is present in the live catalog (downloading if needed)."""

    def _ensure(
        client: KamiwazaClient,
        repo_id: str,
        *,
        quantization: str = "q6_k",
        wait_timeout: int = 900,
        poll_interval: int = 5,
    ):
        model = client.models.get_model_by_repo_id(repo_id)
        if model:
            return model

        client.models.initiate_model_download(repo_id, quantization=quantization)

        try:
            client.models.wait_for_download(
                repo_id, timeout=wait_timeout, show_progress=False
            )
        except TimeoutError as exc:  # pragma: no cover - slow live path
            raise TimeoutError(f"Timed out downloading {repo_id}: {exc}") from exc

        deadline = time.time() + wait_timeout if wait_timeout else None
        while True:
            model = client.models.get_model_by_repo_id(repo_id)
            if model:
                return model
            if deadline and time.time() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for {repo_id} to register after download"
                )
            time.sleep(poll_interval)

    return _ensure


@pytest.fixture(scope="session")
def ingestion_environment() -> Iterator[dict[str, str]]:
    """Spin up fixture services used by ingestion/retrieval integration tests."""

    try:
        compose_env = _compose_env()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    # If something is already listening on the MinIO port (e.g., catalog stack), reuse it.
    host = "localhost"
    port = 19100
    started_compose = False
    if not _port_open(host, port):
        _run_compose("up", "-d", env=compose_env)
        started_compose = True

    try:
        result = subprocess.run(
            [sys.executable, str(SEED_SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            error_msg = f"MinIO seed script failed (exit {result.returncode})\n"
            error_msg += f"STDOUT: {result.stdout}\n"
            error_msg += f"STDERR: {result.stderr}"
            pytest.skip(error_msg)

        yield {
            "bucket": "kamiwaza-sdk-tests",
            "prefix": "sdk-integration",
            "endpoint": _runtime_endpoint("http://localhost:19100"),
        }
    finally:
        if started_compose and os.environ.get("KEEP_INGESTION_FIXTURES") != "1":
            _run_compose("down", "-v", env=compose_env)


@pytest.fixture(scope="session")
def catalog_stack_environment() -> Iterator[dict[str, object]]:
    """Provision the multi-source ingestion stack used by catalog tests."""

    try:
        compose_env = _compose_env()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    if not CATALOG_STACK_COMPOSE.exists() or not CATALOG_STACK_SETUP.exists():
        pytest.skip("Catalog stack assets are unavailable")
    stack_running = _catalog_stack_running()
    parsed_minio = urlparse(CATALOG_MINIO_ENDPOINT)
    preferred_minio_port = int(
        os.environ.get("CATALOG_STACK_MINIO_PORT") or (parsed_minio.port or 19100)
    )
    preferred_console_port = int(
        os.environ.get("CATALOG_STACK_MINIO_CONSOLE_PORT", "19101")
    )

    minio_port = preferred_minio_port
    minio_console_port = preferred_console_port

    if stack_running:
        minio_port = _compose_port("minio", 9000, env=compose_env) or minio_port
        minio_console_port = (
            _compose_port("minio", 9001, env=compose_env) or minio_console_port
        )
    else:
        if _port_open(parsed_minio.hostname or "localhost", minio_port):
            minio_port = _reserve_port(minio_port)
        if minio_console_port == minio_port or _port_open(
            "localhost", minio_console_port
        ):
            minio_console_port = _reserve_port(minio_console_port)

    compose_env["CATALOG_STACK_MINIO_PORT"] = str(minio_port)
    compose_env["CATALOG_STACK_MINIO_CONSOLE_PORT"] = str(minio_console_port)
    compose_env["CATALOG_STACK_KAFKA_ADVERTISED_HOST"] = _runtime_host("localhost")
    # Always issue an up to ensure all services (e.g., minio) are running; harmless if already up.
    _run_catalog_compose("up", "-d", env=compose_env)

    minio_endpoint_local = f"{parsed_minio.scheme or 'http'}://{parsed_minio.hostname or 'localhost'}:{minio_port}"
    minio_endpoint_runtime = _runtime_endpoint(minio_endpoint_local)

    env = compose_env.copy()
    env["INGESTION_STACK_COMPOSE"] = str(CATALOG_STACK_COMPOSE)
    env["STATE_DIR"] = str((CATALOG_STACK_DIR / "state").resolve())
    env["DATA_DIR"] = str((CATALOG_STACK_DIR / "data").resolve())
    env["MINIO_ENDPOINT"] = minio_endpoint_local
    env["MINIO_BUCKET"] = CATALOG_MINIO_BUCKET
    env["MINIO_PREFIX"] = CATALOG_MINIO_PREFIX
    env["POSTGRES_HOST"] = CATALOG_POSTGRES["host"]
    env["POSTGRES_PORT"] = str(CATALOG_POSTGRES["port"])
    env["POSTGRES_DB"] = CATALOG_POSTGRES["database"]
    env["POSTGRES_USER"] = CATALOG_POSTGRES["user"]
    env["POSTGRES_PASSWORD"] = CATALOG_POSTGRES["password"]
    env["KAFKA_EXTERNAL_BOOTSTRAP"] = CATALOG_KAFKA_BOOTSTRAP
    env["FORCE_SEED"] = "1"

    result = subprocess.run(
        ["bash", str(CATALOG_STACK_SETUP)],
        check=False,
        cwd=str(CATALOG_STACK_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error_msg = f"Catalog stack setup failed (exit {result.returncode})\n"
        error_msg += f"STDOUT: {result.stdout}\n"
        error_msg += f"STDERR: {result.stderr}"
        pytest.skip(error_msg)

    config = {
        "object": {
            "bucket": CATALOG_MINIO_BUCKET,
            "prefix": CATALOG_MINIO_PREFIX,
            "endpoint": minio_endpoint_runtime,
            "region": "us-east-1",
            "small_key": f"{CATALOG_MINIO_PREFIX}/inline-small.parquet",
            "large_key": f"{CATALOG_MINIO_PREFIX}/inline-large.parquet",
        },
        "file_root": str((CATALOG_STACK_DIR / "state" / "test-data").resolve()),
        "postgres": {
            "host": _runtime_host(CATALOG_POSTGRES["host"]),
            "port": int(CATALOG_POSTGRES["port"]),
            "database": CATALOG_POSTGRES["database"],
            "user": CATALOG_POSTGRES["user"],
            "password": CATALOG_POSTGRES["password"],
            "schema": CATALOG_POSTGRES["schema"],
        },
        "kafka": {
            "bootstrap": _runtime_bootstrap(CATALOG_KAFKA_BOOTSTRAP),
            "topic": "catalog-test-events",
        },
    }

    try:
        yield config
    finally:
        if not stack_running and os.environ.get("KEEP_CATALOG_STACK") != "1":
            _run_catalog_compose("down", "-v")
