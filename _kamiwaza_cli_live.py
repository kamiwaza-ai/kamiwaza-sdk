from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

_JWT_PATTERN = re.compile(
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
)
_CLEANUP_ERROR_MAX_LENGTH = 500


@dataclass(frozen=True)
class CliAuthConfig:
    """Inputs shared by the CLI login and PAT creation commands."""

    base_args: list[str]
    env: Mapping[str, str] = field(repr=False)
    username: str
    password: str = field(repr=False)
    token_path: Path


class _DeploymentTarget(Protocol):
    repo_id: str
    engine_name: str


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


def _redact_cleanup_error(exc: Exception) -> str:
    """Redact and bound cleanup errors before they reach pytest output."""

    redacted = _redact_cli_stderr(str(exc), [])
    if len(redacted) <= _CLEANUP_ERROR_MAX_LENGTH:
        return redacted
    return f"{redacted[:_CLEANUP_ERROR_MAX_LENGTH]}... <truncated>"


def _cleanup_failure(context: str, exc: Exception) -> str:
    """Describe a cleanup failure without exposing its response payload."""

    return f"{context}: {type(exc).__name__}: {_redact_cleanup_error(exc)}"


def _raise_cleanup_failures(cleanup_failures: list[str]) -> None:
    """Fail after all cleanup operations have had a chance to run."""

    if cleanup_failures:
        raise AssertionError("CLI live cleanup failed: " + "; ".join(cleanup_failures))


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


def _serve_deploy_args(
    base_args: list[str],
    target: _DeploymentTarget,
    model_file_id: str | None,
) -> list[str]:
    """Build an asynchronous CLI model deployment command."""

    args = [
        *base_args,
        "serve",
        "deploy",
        "--repo-id",
        target.repo_id,
        "--engine-name",
        target.engine_name,
    ]
    if model_file_id:
        args.extend(["--file-id", model_file_id])
    return args


def _cleanup_cli_resources(
    cleanup_client: Any,
    deployment_id: str | None,
    pat_token: str,
) -> None:
    """Stop only this test's reported deployment and revoke its PAT."""

    cleanup_failures: list[str] = []
    if deployment_id:
        try:
            cleanup_client.serving.stop_deployment(
                deployment_id=deployment_id,
                force=True,
            )
        except Exception as exc:
            cleanup_failures.append(_cleanup_failure("deployment cleanup", exc))
    try:
        cleanup_client.auth.revoke_pat(pat_jti(pat_token))
    except Exception as exc:
        cleanup_failures.append(_cleanup_failure("PAT cleanup", exc))
    _raise_cleanup_failures(cleanup_failures)


def _token_digest(token: str) -> str:
    """Return a non-sensitive digest for PAT cache verification."""

    return sha256(token.encode()).hexdigest()


def assert_cli_pat_cache_matches(token_path: Path, pat_token: str) -> None:
    """Verify CLI PAT caching without exposing the credential value."""

    assert pat_token, "CLI pat create did not return a token"
    cached = json.loads(token_path.read_text())
    if "access_token" not in cached:
        raise AssertionError("CLI PAT cache does not contain an access token")
    assert _token_digest(cached["access_token"]) == _token_digest(pat_token)


def cli_login_and_create_pat(
    config: CliAuthConfig,
    *,
    pat_prefix: str,
    scope: str = "openid",
    runner: Callable[
        [list[str], Mapping[str, str]], subprocess.CompletedProcess[str]
    ] = run_cli,
) -> str:
    """Login, create a PAT with the requested scope, and return its token."""

    runner(
        [
            *config.base_args,
            "login",
            "--username",
            config.username,
            "--password",
            config.password,
        ],
        config.env,
    )
    assert config.token_path.exists()
    session_token = json.loads(config.token_path.read_text())
    assert "access_token" in session_token

    pat_name = f"{pat_prefix}-{int(time.time())}"
    result = runner(
        [
            *config.base_args,
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
        config.env,
    )
    return result.stdout.strip()


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
