"""Offline acceptance probe: real CLI + live-test orchestration, fake services."""

import contextlib
import functools
import importlib
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kamiwaza_sdk import cli
from kamiwaza_sdk.token_store import FileTokenStore, StoredToken

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
live = importlib.import_module("test_cli_live")
pytestmark = pytest.mark.unit

DEPLOYMENT_ID = "dep-created-before-wait-failure"


def make_client(mode, events):
    def create(**kwargs):
        assert kwargs["wait"] is False
        events.append(["create", DEPLOYMENT_ID])
        return DEPLOYMENT_ID

    def wait(deployment_id, **kwargs):
        assert deployment_id == DEPLOYMENT_ID
        events.append(["wait", deployment_id])
        if mode != "success":
            error = TimeoutError if mode == "timeout" else RuntimeError
            raise error("synthetic wait failure")
        return SimpleNamespace(status="DEPLOYED")

    client = Mock()
    client.serving.deploy_model.side_effect = create
    client.serving.wait_for_deployment.side_effect = wait
    client.serving.wait_deployment_ready.side_effect = wait
    client.serving.stop_deployment.side_effect = lambda **kw: events.append(
        ["stop", kw["deployment_id"]]
    )
    client.auth.revoke_pat.side_effect = lambda jti: events.append(["revoke", jti])
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ready"))]
    )
    client.openai.get_client.return_value.chat.completions.create.return_value = (
        response
    )
    return client


def make_runner(events):
    def runner(cmd, **kwargs):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = cli.main(cmd[3:])
            except (TimeoutError, RuntimeError) as exc:
                code = 1
                print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        events.append(["cli_result", code, stdout.getvalue()])
        return subprocess.CompletedProcess(
            cmd, code, stdout.getvalue(), stderr.getvalue()
        )

    return runner


def prepare(monkeypatch, tmp_path, mode):
    events = []
    client = make_client(mode, events)
    token_path = tmp_path / "token.json"
    pat = live.jwt.encode({"jti": "synthetic-pat-jti"}, "x" * 32, algorithm="HS256")

    def login(*args, **kwargs):
        assert kwargs["pat_scope"] == "admin"
        FileTokenStore(token_path).save(
            StoredToken(access_token=pat, refresh_token=None, expires_at=4102444800)
        )
        return pat

    monkeypatch.setattr(live, "_cli_login_and_create_pat", login)
    monkeypatch.setattr(
        cli,
        "serve_deploy_command",
        functools.partial(
            cli.serve_deploy_command, client_factory=lambda *a, **k: client
        ),
    )
    monkeypatch.setattr(
        live, "run_cli", functools.partial(live.run_cli, runner=make_runner(events))
    )
    return client, events, token_path


@pytest.mark.parametrize("mode", ["success", "timeout", "status-error"])
def test_created_deployment_is_stopped_after_wait(monkeypatch, tmp_path, mode):
    client, events, token_path = prepare(monkeypatch, tmp_path, mode)
    cleanup_client = Mock()
    target = SimpleNamespace(
        repo_id="synthetic/model", engine_name="llamacpp", quantization="q6_k"
    )
    args = (
        "https://disposable.invalid/api",
        "synthetic-user",
        "synthetic-password",
        lambda **kw: client,
        lambda c: object(),
        target,
        lambda m, q: None,
        tmp_path,
        cleanup_client,
    )
    if mode == "success":
        live.test_cli_serve_deploy(*args)
    else:
        with pytest.raises(
            (TimeoutError, RuntimeError), match="synthetic wait failure"
        ):
            live.test_cli_serve_deploy(*args)
    cleanup_client.auth.revoke_pat.assert_called_once_with("synthetic-pat-jti")
    assert not token_path.exists()
    cleanup_client.serving.stop_deployment.assert_called_once_with(
        deployment_id=DEPLOYMENT_ID, force=True
    )
    client.serving.stop_deployment.assert_not_called()
    client.auth.revoke_pat.assert_not_called()


@pytest.mark.parametrize("stop_failure", ["exception", "false"])
def test_cleanup_reports_failures_but_attempts_all_steps(tmp_path, stop_failure):
    client = Mock()
    secret = "opaque-test-credential"
    token_path = tmp_path / "token.json"
    token_path.write_text(secret)
    resources = live._CliResources(client, token_path, (secret,), "pat-jti", "dep-1")
    if stop_failure == "exception":
        client.serving.stop_deployment.side_effect = RuntimeError("x" * 490 + secret)
    else:
        client.serving.stop_deployment.return_value = False
    client.auth.revoke_pat.side_effect = RuntimeError(f"revocation failed: {secret}")

    with pytest.raises(AssertionError, match="CLI cleanup failed") as caught:
        live._cleanup_cli_resources(resources)
    message = str(caught.value)
    assert "stop:" in message
    assert "revoke:" in message
    assert secret[:10] not in message
    assert len(message) < 1100
    assert caught.value.__context__ is None
    client.auth.revoke_pat.assert_called_once_with("pat-jti")
    assert not token_path.exists()


@pytest.mark.parametrize("cache_matches", [True, False])
def test_auth_only_pat_is_revoked_even_if_cache_check_fails(
    monkeypatch, tmp_path, cache_matches
):
    client = Mock()
    token = live.jwt.encode({"jti": "openid-jti"}, "x" * 32, algorithm="HS256")
    token_path = tmp_path / "token.json"

    def login(*args, **kwargs):
        assert kwargs["pat_scope"] == "openid"
        cached = token if cache_matches else "wrong-token"
        token_path.write_text(json.dumps({"access_token": cached}))
        return token

    monkeypatch.setattr(live, "_cli_login_and_create_pat", login)
    invoke = functools.partial(
        live.test_cli_login_and_pat_flow,
        "https://disposable.invalid/api",
        "user",
        "password",
        tmp_path,
        client,
    )
    if cache_matches:
        invoke()
    else:
        with pytest.raises(AssertionError, match="Cached PAT did not match"):
            invoke()
    client.auth.revoke_pat.assert_called_once_with("openid-jti")
    client.serving.stop_deployment.assert_not_called()
    assert not token_path.exists()


@pytest.mark.parametrize(
    "failure", [RuntimeError("not ready"), pytest.skip.Exception("not ready")]
)
def test_prerequisite_failure_revokes_pat_without_stopping_other_deployments(
    monkeypatch, tmp_path, failure
):
    client, events, token_path = prepare(monkeypatch, tmp_path, "success")
    ensure_ready = Mock(side_effect=failure)
    target = SimpleNamespace(
        repo_id="synthetic/model", engine_name="llamacpp", quantization="q6_k"
    )
    with pytest.raises(type(failure), match="not ready"):
        live.test_cli_serve_deploy(
            "https://disposable.invalid/api",
            "user",
            "password",
            lambda **kw: client,
            ensure_ready,
            target,
            lambda m, q: None,
            tmp_path,
            client,
        )
    client.serving.stop_deployment.assert_not_called()
    client.auth.revoke_pat.assert_called_once_with("synthetic-pat-jti")
    assert not token_path.exists()
