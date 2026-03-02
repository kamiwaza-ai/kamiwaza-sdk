from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlparse

import urllib3

import pytest
import requests
from huggingface_hub import snapshot_download

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import APIError, AuthenticationError
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
PODMAN_MACHINE_SOCKET = Path.home() / ".local" / "share" / "containers" / "podman" / "machine" / "podman.sock"
RUNTIME_HOST_ALIAS = os.environ.get("KAMIWAZA_DOCKER_HOST_ALIAS", "host.docker.internal")

_COMPOSE_ENV_CACHE: dict[str, str] | None = None
_COMPOSE_ENV_ERROR: str | None = None


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
        candidates.append(Path(kamiwaza_root).expanduser() / "deploy" / "scripts" / "kz-login")

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


def _password_auth_works(base_url: str, username: str, password: str) -> tuple[bool, str]:
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
    live_api_key: str,
    resolved_live_password: str,
    live_username: str,
) -> KamiwazaClient:
    """Provide an authenticated client for integration tests."""
    os.environ.setdefault("KAMIWAZA_VERIFY_SSL", "false")

    api_key = live_api_key.strip()
    if api_key:
        return KamiwazaClient(live_server_available, api_key=api_key)

    username = live_username.strip()
    password = resolved_live_password.strip()
    if not username or not password:
        pytest.skip(
            "Provide KAMIWAZA_API_KEY or username/password for live integration tests"
        )

    client = KamiwazaClient(live_server_available)
    client.authenticator = UserPasswordAuthenticator(
        username, password, client._auth_service
    )
    return client


@pytest.fixture(scope="session")
def resolved_live_password(
    live_server_available: str,
    live_api_key: str,
    live_username: str,
    pytestconfig: pytest.Config,
) -> str:
    """
    Resolve live password by trying configured credentials first, then falling
    back to deploy/scripts/kz-login when available.
    """

    if live_api_key.strip():
        return ""

    username = live_username.strip()
    configured_password = str(pytestconfig.getoption("live_password")).strip()
    if not username:
        return configured_password

    if configured_password:
        ok, error = _password_auth_works(
            live_server_available,
            username,
            configured_password,
        )
        if ok:
            return configured_password
    else:
        error = "password is empty"

    fallback_password = _resolve_kz_login_password()
    if fallback_password:
        ok, fallback_error = _password_auth_works(
            live_server_available,
            username,
            fallback_password,
        )
        if ok:
            os.environ["KAMIWAZA_PASSWORD"] = fallback_password
            return fallback_password
        error = f"{error}; kz-login fallback failed: {fallback_error}"
    else:
        error = f"{error}; kz-login fallback unavailable"

    pytest.skip(
        "Unable to authenticate live integration client with configured credentials "
        f"or kz-login fallback ({error})"
    )


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
