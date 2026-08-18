from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from _kamiwaza_cli_live import (
    CliAuthConfig,
    _cleanup_cli_resources,
    _serve_deploy_args,
    cli_login_and_create_pat,
    pat_jti,
    run_cli,
)

_PAT_TOKEN = (
    "eyJhbGciOiJub25lIn0."
    "eyJqdGkiOiJiZTA5MGI3Zi1mNjM2LTRlNTctODNkMS04YzYwODcyYTlhNTAifQ."
    "signature"
)


def test_run_cli_reports_sanitized_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "do-not-log-this-password"
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature"

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout=token,
            stderr=(
                "API denied at api.example.com from kamiwaza_sdk.cli.main "
                f"version 1.1.0 password={password} bearer={token}"
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AssertionError) as exc_info:
        run_cli(["login", "--password", password], {})

    message = str(exc_info.value)
    assert "API denied" in message
    assert "api.example.com" in message
    assert "kamiwaza_sdk.cli.main" in message
    assert "1.1.0" in message
    assert password not in message
    assert token not in message
    assert "[REDACTED_PASSWORD]" in message
    assert "[REDACTED_TOKEN]" in message


@pytest.mark.parametrize("scope", ["openid", "admin"])
def test_cli_login_and_create_pat_uses_requested_scope(
    tmp_path: Path,
    scope: str,
) -> None:
    token_path = tmp_path / "token.json"
    calls: list[list[str]] = []

    def fake_runner(
        args: list[str], env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if "login" in args:
            token_path.write_text(json.dumps({"access_token": "session-token"}))
            stdout = "Login succeeded"
        else:
            token_path.write_text(json.dumps({"access_token": "pat-token"}))
            stdout = "pat-token"
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=stdout,
            stderr="",
        )

    config = CliAuthConfig(
        ["--base-url", "https://example.test/api", "--token-path", str(token_path)],
        {},
        "admin",
        "password",
        token_path,
    )
    token = cli_login_and_create_pat(
        config,
        pat_prefix="cli-test",
        scope=scope,
        runner=fake_runner,
    )

    assert token == "pat-token"
    pat_args = calls[1]
    assert pat_args[pat_args.index("--scope") + 1] == scope


@pytest.mark.parametrize(
    ("model_file_id", "expected_file_args"),
    [(None, []), ("file-123", ["--file-id", "file-123"])],
)
def test_serve_deploy_args_include_model_file_when_provided(
    model_file_id: str | None,
    expected_file_args: list[str],
) -> None:
    target = SimpleNamespace(repo_id="org/model", engine_name="llamacpp")

    args = _serve_deploy_args(
        ["--base-url", "https://example.test/api"], target, model_file_id
    )

    assert args == [
        "--base-url",
        "https://example.test/api",
        "serve",
        "deploy",
        "--repo-id",
        "org/model",
        "--engine-name",
        "llamacpp",
        *expected_file_args,
        "--wait",
        "--poll-interval",
        "5",
        "--timeout",
        "600",
    ]


def test_cleanup_stops_only_reported_deployment_and_revokes_exact_pat() -> None:
    client = SimpleNamespace(
        serving=SimpleNamespace(stop_deployment=Mock()),
        auth=SimpleNamespace(revoke_pat=Mock()),
    )

    _cleanup_cli_resources(client, "cli-deployment", _PAT_TOKEN)

    client.serving.stop_deployment.assert_called_once_with(
        deployment_id="cli-deployment",
        force=True,
    )
    client.auth.revoke_pat.assert_called_once_with(
        "be090b7f-f636-4e57-83d1-8c60872a9a50"
    )


def test_cleanup_revokes_pat_when_no_deployment_was_reported() -> None:
    client = SimpleNamespace(
        serving=SimpleNamespace(stop_deployment=Mock()),
        auth=SimpleNamespace(revoke_pat=Mock()),
    )

    _cleanup_cli_resources(client, None, _PAT_TOKEN)

    client.serving.stop_deployment.assert_not_called()
    client.auth.revoke_pat.assert_called_once_with(
        "be090b7f-f636-4e57-83d1-8c60872a9a50"
    )


def test_cleanup_redacts_and_truncates_failure_details() -> None:
    long_error = f"backend token={_PAT_TOKEN} {'x' * 600}"
    client = SimpleNamespace(
        serving=SimpleNamespace(
            stop_deployment=Mock(side_effect=RuntimeError(long_error))
        ),
        auth=SimpleNamespace(revoke_pat=Mock()),
    )

    with pytest.raises(AssertionError) as exc_info:
        _cleanup_cli_resources(client, "cli-deployment", _PAT_TOKEN)

    message = str(exc_info.value)
    assert _PAT_TOKEN not in message
    assert "[REDACTED_TOKEN]" in message
    assert "<truncated>" in message


def test_cli_pat_cache_mismatch_does_not_expose_tokens(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    cached_token = "SUPER_SECRET_CACHED_PAT"
    emitted_token = "SUPER_SECRET_STDOUT_PAT"

    def fake_runner(
        args: list[str], env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        if "login" in args:
            token_path.write_text(json.dumps({"access_token": "session-token"}))
            stdout = "Login succeeded"
        else:
            token_path.write_text(json.dumps({"access_token": cached_token}))
            stdout = emitted_token
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout=stdout, stderr=""
        )

    config = CliAuthConfig([], {}, "admin", "password", token_path)
    with pytest.raises(AssertionError) as exc_info:
        cli_login_and_create_pat(config, pat_prefix="cli-test", runner=fake_runner)

    message = str(exc_info.value)
    assert cached_token not in message
    assert emitted_token not in message


def test_pat_jti_extracts_exact_cleanup_identifier() -> None:
    assert pat_jti(_PAT_TOKEN) == "be090b7f-f636-4e57-83d1-8c60872a9a50"
