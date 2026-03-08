from __future__ import annotations

import base64
import json
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

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import APIError, AuthenticationError
from kamiwaza_sdk.schemas.auth import PATCreate
from kamiwaza_sdk.token_store import StoredToken, TokenStore

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
_LIVE_PASSWORD_CACHE: dict[tuple[str, str, str, str], tuple[str, str | None]] = {}
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
EMBEDDING_TEST_MODEL_REPO = os.environ.get(
    "KAMIWAZA_TEST_EMBEDDING_MODEL_REPO",
    "sentence-transformers/all-MiniLM-L6-v2",
)
EMBEDDING_TEST_MODEL_HUB = os.environ.get("KAMIWAZA_TEST_EMBEDDING_MODEL_HUB", "hf")
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


def _active_embedding_deployment(client: KamiwazaClient) -> dict[str, str] | None:
    try:
        deployments = client.serving.list_active_deployments()
    except APIError:
        return None

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

        if _classify_platform_model_type(purpose, model_name) != "embedding":
            continue

        return {
            "deployment_id": str(deployment.id),
            "model_id": str(deployment.m_id),
            "model_name": model_name,
            "repo_model_id": repo_model_id,
        }

    return None


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
    client.authenticator = UserPasswordAuthenticator(
        username,
        password,
        client._auth_service,
        token_store=_NoCacheTokenStore(),
    )
    try:
        client.auth.get_current_user()
        return True, ""
    except (AuthenticationError, APIError) as exc:
        return False, str(exc)


def _resolve_live_password_once(
    *,
    live_server_available: str,
    live_api_key: str,
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
        live_api_key.strip(),
        live_username.strip(),
        configured_password.strip(),
    )
    cached = _LIVE_PASSWORD_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if live_api_key.strip():
        result = ("", None)
        _LIVE_PASSWORD_CACHE[cache_key] = result
        return result

    username = live_username.strip()
    configured_password = configured_password.strip()
    if not username:
        result = ("", None)
        _LIVE_PASSWORD_CACHE[cache_key] = result
        return result

    errors: list[str] = []

    # The kube-backed password is the authoritative dev credential. If kz-login
    # is available, do not also churn through a stale local default password.
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
    """Ensure a running Kamiwaza server is reachable before running live tests."""

    health_url = f"{live_base_url}/ping"
    os.environ.setdefault("KAMIWAZA_VERIFY_SSL", "false")
    if not _verify_ssl_enabled():
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        response = requests.get(health_url, timeout=5, verify=_verify_ssl_enabled())
    except requests.RequestException as exc:  # pragma: no cover - network guard
        pytest.skip(f"Kamiwaza server unavailable at {live_base_url}: {exc}")
    if response.status_code >= 500:
        pytest.skip(
            f"Kamiwaza server unhealthy at {health_url}: {response.status_code}"
        )
    if response.status_code >= 400 and response.status_code not in (401, 403):
        pytest.skip(
            f"Kamiwaza server at {health_url} returned unexpected status {response.status_code}; "
            "check base URL or ping route configuration."
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


@pytest.fixture(autouse=True)
def _require_embedding_model_for_marked_tests(request: pytest.FixtureRequest) -> None:
    if "requires_embedding_model" in request.keywords:
        request.getfixturevalue("embedding_model_prerequisite")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run embedding-dependent live tests as a contiguous block near the front."""
    embedding_items = [
        item for item in items if "requires_embedding_model" in item.keywords
    ]
    if not embedding_items:
        return

    other_items = [
        item for item in items if "requires_embedding_model" not in item.keywords
    ]
    items[:] = embedding_items + other_items


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
    """

    password, error = _resolve_live_password_once(
        live_server_available=live_server_available,
        live_api_key=live_api_key,
        live_username=live_username,
        configured_password=str(pytestconfig.getoption("live_password")),
    )
    if password or live_api_key.strip():
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
    Prefer a session PAT for general integration traffic.

    Auth-specific tests still use live_username/live_password directly, but the
    shared client fixture should not re-run password grants across the whole suite.
    """

    api_key = live_api_key.strip()
    if api_key:
        yield api_key
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
            name=f"sdk-integration-{uuid.uuid4().hex[:10]}",
            ttl_seconds=4 * 60 * 60,
            scope="openid",
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
