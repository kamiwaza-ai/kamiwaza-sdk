from __future__ import annotations

import json
import os
import re
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
_OUTPUT_LIMIT = 32_000
_CLI_TIMEOUT_SECONDS = 4 * 60 * 60
# Covers two 15-minute model-readiness phases plus the 10-minute deploy wait,
# with enough margin for API calls and polling between phases.
_DEPLOYMENT_PAT_TTL_SECONDS = 60 * 60
_JWT_PATTERN = re.compile(
    r"\b(?:PAT-)?eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_TOKEN_FIELD_PATTERN = re.compile(
    r"(?i)((?:access_token|refresh_token|id_token|token|pat|password|passwd"
    r"|secret|api[-_]?key|client_secret)['\"]?\s*[:=]\s*['\"]?)([^'\"\s,}&]+)"
)
_AUTH_HEADER_PATTERN = re.compile(
    r"(?i)\b(authorization\s*:\s*(?:bearer|basic)\s+)(\S+)"
)


def _secret_option(option: str) -> bool:
    """Match exact secret flags and argparse's accepted abbreviations."""
    if len(option) <= 2 or not option.startswith("--"):
        return False
    return any(secret.startswith(option) for secret in _SECRET_CLI_OPTIONS)


def _redact_cli_args(args: list[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    for arg in args:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        option = arg.partition("=")[0]
        if _secret_option(option):
            redacted.append(option if "=" not in arg else f"{option}=***")
            hide_next = "=" not in arg
            continue
        redacted.append(arg)
    return redacted


def _secret_cli_value_at(args: list[str], index: int) -> str | None:
    option, separator, value = args[index].partition("=")
    if not _secret_option(option):
        return None
    if separator:
        return value
    return args[index + 1] if index + 1 < len(args) else None


def _secret_cli_values(args: list[str]) -> list[str]:
    return [
        value
        for index in range(len(args))
        if (value := _secret_cli_value_at(args, index)) is not None
    ]


def _scrub_output(value: str, secret_values: tuple[str, ...]) -> str:
    scrubbed = value
    for secret in secret_values:
        if secret:
            scrubbed = scrubbed.replace(secret, "***")
    scrubbed = _JWT_PATTERN.sub("***", scrubbed)
    scrubbed = _TOKEN_FIELD_PATTERN.sub(r"\1***", scrubbed)
    return _AUTH_HEADER_PATTERN.sub(r"\1***", scrubbed)


def _captured_output(value: str, secret_values: tuple[str, ...] = ()) -> str:
    output = _scrub_output(value, secret_values).strip()
    if not output:
        return "<empty>"
    if len(output) <= _OUTPUT_LIMIT:
        return output
    half = _OUTPUT_LIMIT // 2
    omitted = len(output) - (half * 2)
    return (
        f"{output[:half]}\n" f"... <{omitted} chars omitted> ...\n" f"{output[-half:]}"
    )


def _timeout_output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _cli_failure_message(
    cmd: list[str],
    result: subprocess.CompletedProcess[str],
    secret_values: tuple[str, ...] = (),
) -> str:
    command = shlex.join(_redact_cli_args(cmd))
    return (
        f"CLI command failed with exit code {result.returncode}: {command}\n"
        f"stdout:\n{_captured_output(result.stdout, secret_values)}\n"
        f"stderr:\n{_captured_output(result.stderr, secret_values)}"
    )


def _cli_timeout_message(
    cmd: list[str],
    exc: subprocess.TimeoutExpired,
    secret_values: tuple[str, ...],
) -> str:
    command = shlex.join(_redact_cli_args(cmd))
    return (
        f"CLI command timed out after {_CLI_TIMEOUT_SECONDS}s: {command}\n"
        f"stdout:\n{_captured_output(_timeout_output(exc.stdout), secret_values)}\n"
        f"stderr:\n{_captured_output(_timeout_output(exc.stderr), secret_values)}"
    )


def _safe_cli_timeout_message(
    cmd: list[str],
    exc: subprocess.TimeoutExpired,
    secret_values: tuple[str, ...],
) -> str:
    """Build a timeout diagnostic without ever re-exposing the original error."""
    try:
        return _cli_timeout_message(cmd, exc, secret_values)
    except Exception as diagnostic_error:
        return (
            f"CLI command timed out after {_CLI_TIMEOUT_SECONDS}s; "
            "diagnostic rendering failed safely "
            f"({type(diagnostic_error).__name__})"
        )


def run_cli(
    args: list[str],
    env: dict[str, str],
    *,
    secret_values: tuple[str, ...] = (),
    runner=subprocess.run,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "kamiwaza_sdk.cli", *args]
    timeout_message: str | None = None
    try:
        result = runner(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=_CLI_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        secrets = (*_secret_cli_values(args), *secret_values)
        timeout_message = _safe_cli_timeout_message(cmd, exc, secrets)
    if timeout_message is not None:
        raise AssertionError(timeout_message) from None
    if result.returncode != 0:
        secrets = (*_secret_cli_values(args), *secret_values)
        raise AssertionError(_cli_failure_message(cmd, result, secrets))
    return result


def _cli_login_and_create_pat(
    base_args: list[str],
    env: dict[str, str],
    live_username: str,
    live_password: str,
    token_path: Path,
    *,
    pat_prefix: str,
    pat_scope: str,
    pat_ttl_seconds: int,
) -> str:
    """Login + create a cached PAT via the CLI; return the PAT token.

    Asserts the session token and PAT cache are persisted along the way, so this
    doubles as the shared CLI-auth coverage for both the auth-only and the
    deploy tests below. Callers must choose the least-privileged scope and
    shortest lifetime that support the operation under test.
    """
    run_cli(
        [*base_args, "login", "--username", live_username, "--password", live_password],
        env,
    )
    assert token_path.exists()
    session_token = json.loads(token_path.read_text())
    if not isinstance(session_token, dict) or not session_token.get("access_token"):
        raise AssertionError("CLI login cache did not contain an access token")

    pat_name = f"{pat_prefix}-{int(time.time())}"
    result = run_cli(
        [
            *base_args,
            "pat",
            "create",
            "--name",
            pat_name,
            "--ttl",
            str(pat_ttl_seconds),
            "--scope",
            pat_scope,
            "--aud",
            "kamiwaza-platform",
            "--cache-token",
        ],
        env,
        secret_values=(live_password, str(session_token["access_token"])),
    )
    pat_token = result.stdout.strip()
    assert pat_token

    cached = json.loads(token_path.read_text())
    if cached.get("access_token") != pat_token:
        raise AssertionError("Cached PAT did not match the CLI-created PAT")
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
        base_args,
        env,
        live_username,
        live_password,
        token_path,
        pat_prefix="cli-m1",
        pat_scope="openid",
        pat_ttl_seconds=900,
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
        pat_scope="admin",
        pat_ttl_seconds=_DEPLOYMENT_PAT_TTL_SECONDS,
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
