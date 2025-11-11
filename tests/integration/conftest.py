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


def _run_compose(*args: str) -> None:
    cmd = ["docker", "compose", "-f", str(DOCKER_COMPOSE_FILE), *args]
    subprocess.run(cmd, check=True)


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
