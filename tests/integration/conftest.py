from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import pytest
import requests
from huggingface_hub import snapshot_download

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator

DOCKER_COMPOSE_FILE = Path(__file__).parent / "docker" / "docker-compose.yml"
SEED_SCRIPT = Path(__file__).parent / "docker" / "seed_minio.py"

CATALOG_STACK_DIR = Path(__file__).parent / "catalog_stack"
CATALOG_STACK_COMPOSE = CATALOG_STACK_DIR / "docker-compose.yml"
CATALOG_STACK_SETUP = CATALOG_STACK_DIR / "setup-test-data.sh"
CATALOG_MINIO_ENDPOINT = os.environ.get("CATALOG_STACK_MINIO_ENDPOINT", "http://localhost:19100")
CATALOG_MINIO_BUCKET = os.environ.get("CATALOG_STACK_MINIO_BUCKET", "kamiwaza-test-bucket")
CATALOG_MINIO_PREFIX = os.environ.get("CATALOG_STACK_MINIO_PREFIX", "catalog-tests")
CATALOG_POSTGRES = {
    "host": os.environ.get("CATALOG_STACK_POSTGRES_HOST", "localhost"),
    "port": os.environ.get("CATALOG_STACK_POSTGRES_PORT", "15432"),
    "database": os.environ.get("CATALOG_STACK_POSTGRES_DB", "kamiwaza"),
    "user": os.environ.get("CATALOG_STACK_POSTGRES_USER", "kamiwaza"),
    "password": os.environ.get("CATALOG_STACK_POSTGRES_PASSWORD", "kamiwazaGetY0urCape"),
    "schema": os.environ.get("CATALOG_STACK_POSTGRES_SCHEMA", "public"),
}
CATALOG_KAFKA_BOOTSTRAP = os.environ.get("CATALOG_STACK_KAFKA_BOOTSTRAP", "localhost:29092")


def _run_compose(*args: str) -> None:
    cmd = ["docker", "compose", "-f", str(DOCKER_COMPOSE_FILE), *args]
    subprocess.run(cmd, check=True)


def _run_catalog_compose(*args: str) -> None:
    cmd = ["docker", "compose", "-f", str(CATALOG_STACK_COMPOSE), *args]
    subprocess.run(cmd, check=True)


def _catalog_stack_running() -> bool:
    cmd = ["docker", "compose", "-f", str(CATALOG_STACK_COMPOSE), "ps", "-q"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return bool(result.stdout.strip())


def _verify_ssl_enabled() -> bool:
    return os.environ.get("KAMIWAZA_VERIFY_SSL", "true").lower() != "false"


@pytest.fixture(scope="session")
def live_server_available(live_base_url: str) -> str:
    """Ensure a running Kamiwaza server is reachable before running live tests."""

    health_url = f"{live_base_url}/ping"
    os.environ.setdefault("KAMIWAZA_VERIFY_SSL", "false")
    try:
        response = requests.get(health_url, timeout=5, verify=_verify_ssl_enabled())
    except requests.RequestException as exc:  # pragma: no cover - network guard
        pytest.skip(f"Kamiwaza server unavailable at {live_base_url}: {exc}")
    if response.status_code >= 500:
        pytest.skip(f"Kamiwaza server unhealthy at {health_url}: {response.status_code}")
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
    live_username: str,
    live_password: str,
) -> KamiwazaClient:
    """Provide an authenticated client for integration tests."""

    os.environ.setdefault("KAMIWAZA_VERIFY_SSL", "false")
    api_key = live_api_key.strip()
    if api_key:
        return KamiwazaClient(live_server_available, api_key=api_key)

    username = live_username.strip()
    password = live_password.strip()
    if not username or not password:
        pytest.skip("Provide KAMIWAZA_API_KEY or username/password for live integration tests")

    client = KamiwazaClient(live_server_available)
    client.authenticator = UserPasswordAuthenticator(username, password, client._auth_service)
    return client


@pytest.fixture(scope="session")
def ingestion_environment() -> Iterator[dict[str, str]]:
    """Spin up fixture services used by ingestion/retrieval integration tests."""

    if shutil.which("docker") is None:
        pytest.skip("Docker is required for integration tests")

    _run_compose("up", "-d")
    try:
        subprocess.run([sys.executable, str(SEED_SCRIPT)], check=True)
        yield {
            "bucket": "kamiwaza-sdk-tests",
            "prefix": "sdk-integration",
            "endpoint": "http://localhost:9100",
        }
    finally:
        _run_compose("down", "-v")


@pytest.fixture(scope="session")
def catalog_stack_environment() -> Iterator[dict[str, object]]:
    """Provision the multi-source ingestion stack used by catalog tests."""

    if shutil.which("docker") is None:
        pytest.skip("Docker is required for catalog ingestion tests")
    if not CATALOG_STACK_COMPOSE.exists() or not CATALOG_STACK_SETUP.exists():
        pytest.skip("Catalog stack assets are unavailable")

    stack_running = _catalog_stack_running()
    if not stack_running:
        _run_catalog_compose("up", "-d")

    env = os.environ.copy()
    env.setdefault("INGESTION_STACK_COMPOSE", str(CATALOG_STACK_COMPOSE))
    env.setdefault("STATE_DIR", str((CATALOG_STACK_DIR / "state").resolve()))
    env.setdefault("DATA_DIR", str((CATALOG_STACK_DIR / "data").resolve()))
    env.setdefault("MINIO_ENDPOINT", CATALOG_MINIO_ENDPOINT)
    env.setdefault("MINIO_BUCKET", CATALOG_MINIO_BUCKET)
    env.setdefault("MINIO_PREFIX", CATALOG_MINIO_PREFIX)
    env.setdefault("POSTGRES_HOST", CATALOG_POSTGRES["host"])
    env.setdefault("POSTGRES_PORT", str(CATALOG_POSTGRES["port"]))
    env.setdefault("POSTGRES_DB", CATALOG_POSTGRES["database"])
    env.setdefault("POSTGRES_USER", CATALOG_POSTGRES["user"])
    env.setdefault("POSTGRES_PASSWORD", CATALOG_POSTGRES["password"])
    env.setdefault("KAFKA_EXTERNAL_BOOTSTRAP", CATALOG_KAFKA_BOOTSTRAP)

    subprocess.run(
        ["bash", str(CATALOG_STACK_SETUP)],
        check=True,
        cwd=str(CATALOG_STACK_DIR),
        env=env,
    )

    config = {
        "object": {
            "bucket": CATALOG_MINIO_BUCKET,
            "prefix": CATALOG_MINIO_PREFIX,
            "endpoint": CATALOG_MINIO_ENDPOINT,
            "region": "us-east-1",
            "small_key": f"{CATALOG_MINIO_PREFIX}/inline-small.parquet",
            "large_key": f"{CATALOG_MINIO_PREFIX}/inline-large.parquet",
        },
        "file_root": str((CATALOG_STACK_DIR / "state" / "test-data").resolve()),
        "postgres": {
            "host": CATALOG_POSTGRES["host"],
            "port": int(CATALOG_POSTGRES["port"]),
            "database": CATALOG_POSTGRES["database"],
            "user": CATALOG_POSTGRES["user"],
            "password": CATALOG_POSTGRES["password"],
            "schema": CATALOG_POSTGRES["schema"],
        },
        "kafka": {
            "bootstrap": CATALOG_KAFKA_BOOTSTRAP,
            "topic": "catalog-test-events",
        },
    }

    try:
        yield config
    finally:
        if not stack_running:
            _run_catalog_compose("down", "-v")
