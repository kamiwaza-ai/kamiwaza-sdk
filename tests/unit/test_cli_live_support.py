from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from _kamiwaza_cli_live import cli_login_and_create_pat, pat_jti, run_cli


def test_run_cli_reports_sanitized_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "do-not-log-this-password"
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature"

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout=token,
            stderr=f"API denied password={password} bearer={token}",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AssertionError) as exc_info:
        run_cli(["login", "--password", password], {})

    message = str(exc_info.value)
    assert "API denied" in message
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

    token = cli_login_and_create_pat(
        ["--base-url", "https://example.test/api", "--token-path", str(token_path)],
        {},
        "admin",
        "password",
        token_path,
        pat_prefix="cli-test",
        scope=scope,
        runner=fake_runner,
    )

    assert token == "pat-token"
    pat_args = calls[1]
    assert pat_args[pat_args.index("--scope") + 1] == scope


def test_pat_jti_extracts_exact_cleanup_identifier() -> None:
    token = (
        "eyJhbGciOiJub25lIn0."
        "eyJqdGkiOiJiZTA5MGI3Zi1mNjM2LTRlNTctODNkMS04YzYwODcyYTlhNTAifQ."
        "signature"
    )

    assert pat_jti(token) == "be090b7f-f636-4e57-83d1-8c60872a9a50"
