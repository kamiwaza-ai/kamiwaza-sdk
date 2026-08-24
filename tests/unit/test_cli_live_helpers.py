import ast
import importlib
import inspect
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from kamiwaza_sdk.exceptions import APIError, AuthenticationError

INTEGRATION_TESTS = str(Path(__file__).parents[1] / "integration")
if INTEGRATION_TESTS not in sys.path:
    sys.path.insert(0, INTEGRATION_TESTS)

cli_live = importlib.import_module("test_cli_live")

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("pat_scope", "pat_ttl_seconds"),
    [("openid", 900), ("admin", 3600)],
)
def test_cli_login_and_create_pat_forwards_requested_scope_and_ttl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pat_scope: str,
    pat_ttl_seconds: int,
) -> None:
    token_path = tmp_path / "token.json"
    calls: list[list[str]] = []

    def fake_run_cli(
        args: list[str],
        _env: dict[str, str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if len(calls) == 1:
            token_path.write_text('{"access_token": "session-token"}')
            return subprocess.CompletedProcess(args, 0, "", "")

        token_path.write_text('{"access_token": "admin-pat"}')
        return subprocess.CompletedProcess(args, 0, "admin-pat\n", "")

    monkeypatch.setattr(cli_live, "run_cli", fake_run_cli)

    pat_token = cli_live._cli_login_and_create_pat(
        ["--base-url", "https://localhost/api", "--token-path", str(token_path)],
        {"PATH": "/bin"},
        "admin",
        "password",
        token_path,
        pat_prefix="cli-deploy",
        pat_scope=pat_scope,
        pat_ttl_seconds=pat_ttl_seconds,
    )

    assert pat_token == "admin-pat"
    pat_args = calls[1]
    scope_index = pat_args.index("--scope")
    assert pat_args[scope_index + 1] == pat_scope
    ttl_index = pat_args.index("--ttl")
    assert pat_args[ttl_index + 1] == str(pat_ttl_seconds)


def test_cli_serve_deploy_requests_full_budget_admin_pat() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(cli_live.test_cli_serve_deploy)))
    pat_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_cli_login_and_create_pat"
    ]

    assert len(pat_calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in pat_calls[0].keywords}
    assert ast.literal_eval(keywords["pat_scope"]) == "admin"
    ttl_argument = keywords["pat_ttl_seconds"]
    assert isinstance(ttl_argument, ast.Name)
    assert ttl_argument.id == "_DEPLOYMENT_PAT_TTL_SECONDS"
    assert cli_live._DEPLOYMENT_PAT_TTL_SECONDS == 60 * 60


@pytest.mark.parametrize(
    ("revoke_error", "deploy_status", "inference_error"),
    [
        (None, "DEPLOYED", None),
        (APIError("gateway unavailable", status_code=503), "DEPLOYED", None),
        (AuthenticationError("PAT expired", status_code=401), "DEPLOYED", None),
        (APIError("gateway unavailable", status_code=503), "FAILED", None),
        (None, "DEPLOYED", RuntimeError("inference failed")),
    ],
    ids=[
        "success",
        "api-error",
        "authentication-error",
        "deploy-and-revoke-fail",
        "inference-fail",
    ],
)
def test_cli_serve_deploy_attempts_revocation_and_clears_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    revoke_error: Exception | None,
    deploy_status: str,
    inference_error: Exception | None,
) -> None:
    token_path = tmp_path / "token.json"
    pat_token = cli_live.jwt.encode(
        {"jti": "pat-jti"}, "test-key-with-at-least-32-bytes!!", algorithm="HS256"
    )
    revoked: list[str] = []
    events: list[str] = []

    def fake_login_and_create_pat(*_args: object, **_kwargs: object) -> str:
        token_path.write_text('{"access_token": "admin-pat"}')
        return pat_token

    def fake_revoke(jti: str) -> None:
        events.append("revoke")
        revoked.append(jti)
        if revoke_error:
            raise revoke_error

    def fake_stop_deployment(**_kwargs: object) -> None:
        events.append("stop")

    def fake_chat_completion(**_kwargs: object) -> object:
        events.append("infer")
        if inference_error:
            raise inference_error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ready"))]
        )

    def fake_run_cli(*_args: object, **_kwargs: object):
        events.append("deploy")
        return subprocess.CompletedProcess(
            [],
            0,
            f'{{"deployment_id": "dep-1", "status": "{deploy_status}"}}',
            "",
        )

    pat_client = SimpleNamespace(
        auth=SimpleNamespace(revoke_pat=fake_revoke),
        serving=SimpleNamespace(stop_deployment=fake_stop_deployment),
        openai=SimpleNamespace(
            get_client=lambda **_kwargs: SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=fake_chat_completion)
                )
            )
        ),
    )
    monkeypatch.setattr(
        cli_live, "_cli_login_and_create_pat", fake_login_and_create_pat
    )
    monkeypatch.setattr(cli_live, "run_cli", fake_run_cli)

    def invoke_deploy_test() -> None:
        cli_live.test_cli_serve_deploy(
            "https://localhost/api",
            "admin",
            "password",
            lambda **_kwargs: pat_client,
            lambda _client: object(),
            SimpleNamespace(
                repo_id="repo/model", engine_name="llamacpp", quantization="q6_k"
            ),
            lambda _model, _quantization: None,
            tmp_path,
        )

    if inference_error is not None:
        with pytest.raises(RuntimeError, match="inference failed"):
            invoke_deploy_test()
        assert events == ["deploy", "infer", "stop", "revoke"]
    elif deploy_status == "DEPLOYED":
        invoke_deploy_test()
        assert events == ["deploy", "infer", "stop", "revoke"]
    else:
        with pytest.raises(AssertionError, match="FAILED"):
            invoke_deploy_test()
        assert events == ["deploy", "stop", "revoke"]

    assert revoked == ["pat-jti"]
    assert not token_path.exists()


def test_pat_jti_reads_server_issued_cleanup_identifier() -> None:
    token = cli_live.jwt.encode(
        {"jti": "pat-jti"}, "test-key-with-at-least-32-bytes!!", algorithm="HS256"
    )

    assert cli_live._pat_jti(token) == "pat-jti"


def test_pat_jti_rejects_token_without_cleanup_identifier() -> None:
    token = cli_live.jwt.encode(
        {"sub": "user-id"}, "test-key-with-at-least-32-bytes!!", algorithm="HS256"
    )

    with pytest.raises(AssertionError, match="did not contain a JTI"):
        cli_live._pat_jti(token)


def test_run_cli_reports_output_and_redacts_secret_options() -> None:
    result = subprocess.CompletedProcess(
        args=[],
        returncode=2,
        stdout="deployment rejected\n",
        stderr="model file was not found; password=super-secret\n",
    )
    with pytest.raises(AssertionError) as exc_info:
        cli_live.run_cli(
            ["serve", "deploy", "--password", "super-secret"],
            {"PATH": "/bin"},
            runner=lambda *_args, **_kwargs: result,
        )

    message = str(exc_info.value)
    assert "exit code 2" in message
    assert "deployment rejected" in message
    assert "model file was not found" in message
    assert "--password '***'" in message
    assert "super-secret" not in message


@pytest.mark.parametrize(
    "command", [["login", "--password", "known-secret"], ["pat", "create"]]
)
def test_run_cli_scrubs_output_for_credential_bearing_commands(
    command: list[str],
) -> None:
    result = subprocess.CompletedProcess(
        args=[],
        returncode=2,
        stdout='{"access_token": "server-issued-secret"}\n',
        stderr="validation input token='server-issued-secret'\n",
    )
    with pytest.raises(AssertionError) as exc_info:
        cli_live.run_cli(
            command,
            {"PATH": "/bin"},
            secret_values=("server-issued-secret",),
            runner=lambda *_args, **_kwargs: result,
        )

    message = str(exc_info.value)
    assert "validation input" in message
    assert "***" in message
    assert "server-issued-secret" not in message


def test_run_cli_returns_successful_result() -> None:
    result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='{"status": "DEPLOYED"}\n',
        stderr="",
    )
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((_args, kwargs))
        return result

    assert (
        cli_live.run_cli(["serve", "deploy"], {"PATH": "/bin"}, runner=fake_run)
        is result
    )
    args, kwargs = calls[0]
    assert args[0] == [
        sys.executable,
        "-m",
        "kamiwaza_sdk.cli",
        "serve",
        "deploy",
    ]
    assert kwargs == {
        "capture_output": True,
        "text": True,
        "check": False,
        "env": {"PATH": "/bin"},
        "timeout": cli_live._CLI_TIMEOUT_SECONDS,
    }


def test_captured_output_keeps_head_tail_and_marks_truncation() -> None:
    value = "HEADMARK" + ("x" * cli_live._OUTPUT_LIMIT) + "TAILMARK"

    output = cli_live._captured_output(value)

    assert "HEADMARK" in output
    assert "TAILMARK" in output
    assert "chars omitted" in output


def test_captured_output_empty_sentinel_and_stream_labels() -> None:
    result = subprocess.CompletedProcess(
        args=[], returncode=-9, stdout="  \n", stderr="TAILMARK"
    )

    message = cli_live._cli_failure_message(["serve", "deploy"], result)

    assert "exit code -9" in message
    assert "stdout:\n<empty>" in message
    assert "stderr:\nTAILMARK" in message


@pytest.mark.parametrize(
    ("args", "secret"),
    [
        (["login", "--password=secret"], "secret"),
        (["login", "--passw", "secret"], "secret"),
        (["login", "--api-key", "secret"], "secret"),
    ],
)
def test_redaction_covers_equals_and_abbreviated_secret_options(
    args: list[str], secret: str
) -> None:
    rendered = shlex.join(cli_live._redact_cli_args(args))
    assert secret not in rendered


def test_token_path_is_not_redacted() -> None:
    args = ["--token-path", "/tmp/token.json"]
    assert cli_live._redact_cli_args(args) == args


def test_redaction_does_not_treat_empty_or_separator_args_as_secret_options() -> None:
    assert all(not cli_live._secret_option(value) for value in ("", "-", "--"))

    args = [
        "serve",
        "deploy",
        "--engine-name",
        "",
        "--repo-id",
        "meta/llama-3",
    ]
    assert cli_live._redact_cli_args(args) == args
    assert cli_live._redact_cli_args(["serve", "deploy", "--", "meta/llama-3"]) == [
        "serve",
        "deploy",
        "--",
        "meta/llama-3",
    ]


def test_run_cli_timeout_redacts_command_and_reports_partial_output() -> None:
    secret = "REALPASSWORD-CANARY-777"

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            cmd=["ignored"],
            timeout=cli_live._CLI_TIMEOUT_SECONDS,
            output=b"deployment was still waiting\n",
            stderr=f"password={secret}\n".encode(),
        )

    with pytest.raises(AssertionError) as exc_info:
        cli_live.run_cli(
            ["login", "--username", "admin", "--password", secret],
            {"PATH": "/bin"},
            runner=timeout,
        )

    message = str(exc_info.value)
    assert "timed out" in message
    assert str(cli_live._CLI_TIMEOUT_SECONDS) in message
    assert "deployment was still waiting" in message
    assert "password=***" in message
    assert "--password '***'" in message
    assert secret not in message
    assert exc_info.value.__context__ is None


def test_run_cli_timeout_fails_closed_if_diagnostic_rendering_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "REALPASSWORD-CANARY-777"

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            cmd=["login", "--password", secret],
            timeout=cli_live._CLI_TIMEOUT_SECONDS,
        )

    def broken_capture(*_args: object, **_kwargs: object) -> str:
        raise UnicodeError("diagnostic renderer failed")

    monkeypatch.setattr(cli_live, "_captured_output", broken_capture)
    with pytest.raises(AssertionError) as exc_info:
        cli_live.run_cli(
            ["login", "--password", secret],
            {"PATH": "/bin"},
            runner=timeout,
        )

    assert "diagnostic rendering failed safely" in str(exc_info.value)
    assert secret not in str(exc_info.value)
    assert exc_info.value.__context__ is None


def test_run_cli_timeout_scrubs_caller_secrets_from_both_streams() -> None:
    secret = "opaque-session-secret"

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            cmd=["pat", "create"],
            timeout=cli_live._CLI_TIMEOUT_SECONDS,
            output=f"stdout retained marker {secret}".encode(),
            stderr=f"stderr retained marker {secret}".encode(),
        )

    with pytest.raises(AssertionError) as exc_info:
        cli_live.run_cli(
            ["pat", "create", "--name", "nightly"],
            {"PATH": "/bin"},
            secret_values=(secret,),
            runner=timeout,
        )

    message = str(exc_info.value)
    assert "stdout retained marker ***" in message
    assert "stderr retained marker ***" in message
    assert secret not in message


def test_run_cli_timeout_handles_empty_and_invalid_byte_streams() -> None:
    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            cmd=["serve", "deploy"],
            timeout=cli_live._CLI_TIMEOUT_SECONDS,
            output=None,
            stderr=b"invalid byte: \xff",
        )

    with pytest.raises(AssertionError) as exc_info:
        cli_live.run_cli(["serve", "deploy"], {"PATH": "/bin"}, runner=timeout)

    message = str(exc_info.value)
    assert "stdout:\n<empty>" in message
    assert "stderr:\ninvalid byte:" in message
    assert exc_info.value.__context__ is None


def test_jwt_and_token_fields_are_scrubbed_without_blanket_suppression() -> None:
    jwt = "PAT-eyJabcdefgh.abcdefghijk.abcdefghijk"
    output = cli_live._captured_output(
        f"request failed token='opaque-secret' response={jwt}"
    )
    assert "request failed" in output
    assert "opaque-secret" not in output
    assert jwt not in output


@pytest.mark.parametrize(
    ("value", "secret"),
    [
        ("access_token: abc123XYZ", "abc123XYZ"),
        ("access_token=abc123XYZ", "abc123XYZ"),
        ("Authorization: Bearer opaque-abc123XYZ", "opaque-abc123XYZ"),
        ("X-API-Key: abc123XYZ", "abc123XYZ"),
        ("grant_type=password&username=u&password=hunter2", "hunter2"),
    ],
)
def test_unquoted_token_fields_and_auth_headers_are_scrubbed(
    value: str, secret: str
) -> None:
    output = cli_live._captured_output(value)
    assert secret not in output
    assert "***" in output
