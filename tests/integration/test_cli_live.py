from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


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
