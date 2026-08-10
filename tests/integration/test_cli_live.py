from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest
from model_targets import InferenceTarget

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

_SECRET_CLI_OPTIONS = frozenset(
    {"--access-token", "--api-key", "--password", "--token"}
)
_CREDENTIAL_BEARING_COMMANDS = frozenset({"login", "pat"})
_SUPPRESSED_OUTPUT = "<suppressed for credential-bearing command>"
_OUTPUT_LIMIT = 8_000


def _redact_cli_args(args: list[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    for arg in args:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        option = arg.partition("=")[0]
        if option in _SECRET_CLI_OPTIONS:
            redacted.append(option if "=" not in arg else f"{option}=***")
            hide_next = "=" not in arg
            continue
        redacted.append(arg)
    return redacted


def _captured_output(value: str) -> str:
    output = value.strip()
    if not output:
        return "<empty>"
    return output[-_OUTPUT_LIMIT:]


def _failure_output(cmd: list[str], value: str) -> str:
    if not _CREDENTIAL_BEARING_COMMANDS.isdisjoint(cmd):
        return _SUPPRESSED_OUTPUT
    return _captured_output(value)


def _cli_failure_message(
    cmd: list[str], result: subprocess.CompletedProcess[str]
) -> str:
    command = shlex.join(_redact_cli_args(cmd))
    return (
        f"CLI command failed with exit code {result.returncode}: {command}\n"
        f"stdout:\n{_failure_output(cmd, result.stdout)}\n"
        f"stderr:\n{_failure_output(cmd, result.stderr)}"
    )


def run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "kamiwaza_sdk.cli", *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(_cli_failure_message(cmd, result))
    return result


def _cli_login_and_create_pat(
    base_args: list[str],
    env: dict[str, str],
    live_username: str,
    live_password: str,
    token_path: Path,
    *,
    pat_prefix: str,
) -> str:
    """Login + create a cached PAT via the CLI; return the PAT token.

    Asserts the session token and PAT cache are persisted along the way, so this
    doubles as the shared CLI-auth coverage for both the auth-only and the
    deploy tests below.
    """
    run_cli(
        [*base_args, "login", "--username", live_username, "--password", live_password],
        env,
    )
    assert token_path.exists()
    session_token = json.loads(token_path.read_text())
    assert "access_token" in session_token

    pat_name = f"{pat_prefix}-{int(time.time())}"
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
    return pat_token


def test_cli_login_and_pat_flow(
    live_server_available: str,
    live_username: str,
    live_password: str,
    tmp_path: Path,
) -> None:
    """CLI login + PAT creation/caching (no model deployment required)."""
    token_path = tmp_path / "token.json"
    base_args = ["--base-url", live_server_available, "--token-path", str(token_path)]

    env = os.environ.copy()
    env.setdefault("PYTHONWARNINGS", "ignore")

    # _cli_login_and_create_pat asserts the session token, PAT, and cache match.
    _cli_login_and_create_pat(
        base_args, env, live_username, live_password, token_path, pat_prefix="cli-m1"
    )


@pytest.mark.requires_deployable_model
def test_cli_serve_deploy(
    live_server_available: str,
    live_username: str,
    live_password: str,
    client_factory,
    ensure_deployable_model_ready,
    deployable_model_target: InferenceTarget,
    target_model_file_id,
    tmp_path: Path,
) -> None:
    """CLI ``serve deploy`` round-trip.

    Requires a host that can actually deploy the test model; gated by
    ``requires_deployable_model`` so it skips (rather than fails) on hosts
    without compatible inference capacity for the platform-selected target.
    """
    token_path = tmp_path / "token.json"
    base_args = ["--base-url", live_server_available, "--token-path", str(token_path)]

    env = os.environ.copy()
    env.setdefault("PYTHONWARNINGS", "ignore")

    pat_token = _cli_login_and_create_pat(
        base_args,
        env,
        live_username,
        live_password,
        token_path,
        pat_prefix="cli-deploy",
    )
    pat_client = client_factory(base_url=live_server_available, api_key=pat_token)
    model = ensure_deployable_model_ready(pat_client)
    model_file_id = target_model_file_id(model, deployable_model_target.quantization)

    serve_result = run_cli(
        [
            *base_args,
            "serve",
            "deploy",
            "--repo-id",
            deployable_model_target.repo_id,
            "--engine-name",
            deployable_model_target.engine_name,
            *(["--file-id", model_file_id] if model_file_id else []),
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
