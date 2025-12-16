from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TEST_REPO_ID = "mlx-community/Qwen3-4B-4bit"

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "kamiwaza_sdk.cli", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def test_cli_login_and_pat_flow(
    live_server_available: str,
    live_username: str,
    live_password: str,
    client_factory,
    ensure_repo_ready,
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "token.json"
    base_args = ["--base-url", live_server_available, "--token-path", str(token_path)]

    env = os.environ.copy()
    env.setdefault("PYTHONWARNINGS", "ignore")

    # Login and persist session token
    run_cli([*base_args, "login", "--username", live_username, "--password", live_password], env)
    assert token_path.exists()
    session_token = json.loads(token_path.read_text())
    assert "access_token" in session_token

    # Create PAT via CLI and cache it
    pat_name = f"cli-m1-{int(time.time())}"
    result = run_cli(
        [
            *base_args,
            "pat",
            "create",
            "--name",
            pat_name,
            "--ttl",
            "900",
            "--scope",
            "openid",
            "--aud",
            "kamiwaza-platform",
            "--cache-token",
        ],
        env,
    )
    pat_token = result.stdout.strip()
    assert pat_token

    cached = json.loads(token_path.read_text())
    assert cached["access_token"] == pat_token

    pat_client = client_factory(base_url=live_server_available, api_key=pat_token)
    ensure_repo_ready(pat_client, TEST_REPO_ID)

    serve_result = run_cli(
        [
            *base_args,
            "serve",
            "deploy",
            "--repo-id",
            TEST_REPO_ID,
            "--wait",
            "--poll-interval",
            "5",
            "--timeout",
            "600",
        ],
        env,
    )

    summary = json.loads(serve_result.stdout.strip())
    deployment_id = summary.get("deployment_id")
    assert deployment_id, "CLI serve deploy did not return a deployment_id"
    assert summary.get("status") == "DEPLOYED"

    try:
        pat_client.serving.stop_deployment(deployment_id=deployment_id, force=True)
    except Exception:
        pass
