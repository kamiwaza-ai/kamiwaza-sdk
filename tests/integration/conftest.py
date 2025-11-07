from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import pytest

DOCKER_COMPOSE_FILE = Path(__file__).parent / "docker" / "docker-compose.yml"
SEED_SCRIPT = Path(__file__).parent / "docker" / "seed_minio.py"


def _run_compose(*args: str) -> None:
    cmd = ["docker", "compose", "-f", str(DOCKER_COMPOSE_FILE), *args]
    subprocess.run(cmd, check=True)


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
