from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path


_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
)


def _redact_cli_stderr(stderr: str, args: list[str]) -> str:
    """Return actionable CLI diagnostics without echoing credentials."""

    redacted = _JWT_PATTERN.sub("[REDACTED_TOKEN]", stderr)
    for index, arg in enumerate(args[:-1]):
        if arg != "--password":
            continue
        password = args[index + 1]
        if password:
            redacted = redacted.replace(password, "[REDACTED_PASSWORD]")
    return redacted.strip()


def run_cli(
    args: list[str], env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run the SDK CLI and retain sanitized stderr when it fails."""

    result = subprocess.run(
        [sys.executable, "-m", "kamiwaza_sdk.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode:
        stderr = _redact_cli_stderr(result.stderr, args) or "<empty stderr>"
        raise AssertionError(
            f"CLI subprocess exited with status {result.returncode}.\n"
            f"Sanitized stderr:\n{stderr}"
        )
    return result


def cli_login_and_create_pat(
    base_args: list[str],
    env: Mapping[str, str],
    username: str,
    password: str,
    token_path: Path,
    *,
    pat_prefix: str,
    scope: str = "openid",
    runner: Callable[
        [list[str], Mapping[str, str]], subprocess.CompletedProcess[str]
    ] = run_cli,
) -> str:
    """Login, cache a PAT with the requested scope, and return its token."""

    runner(
        [*base_args, "login", "--username", username, "--password", password],
        env,
    )
    assert token_path.exists()
    session_token = json.loads(token_path.read_text())
    assert "access_token" in session_token

    pat_name = f"{pat_prefix}-{int(time.time())}"
    result = runner(
        [
            *base_args,
            "pat",
            "create",
            "--name",
            pat_name,
            "--ttl",
            "900",
            "--scope",
            scope,
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
    return pat_token


def pat_jti(token: str) -> str:
    """Extract a PAT identifier for exact test cleanup without logging it."""

    parts = token.split(".")
    if len(parts) != 3:
        raise AssertionError("PAT is not a three-part JWT")
    payload = parts[1]
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    jti = json.loads(decoded).get("jti")
    if not isinstance(jti, str) or not jti:
        raise AssertionError("PAT JWT does not contain a jti claim")
    return jti
