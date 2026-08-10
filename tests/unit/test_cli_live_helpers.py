import importlib
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

INTEGRATION_TESTS = str(Path(__file__).parents[1] / "integration")
if INTEGRATION_TESTS not in sys.path:
    sys.path.insert(0, INTEGRATION_TESTS)

cli_live = importlib.import_module("test_cli_live")

pytestmark = pytest.mark.unit


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
    assert "deployment was still waiting" in message
    assert "--password '***'" in message
    assert secret not in message


def test_jwt_and_token_fields_are_scrubbed_without_blanket_suppression() -> None:
    jwt = "PAT-eyJabcdefgh.abcdefghijk.abcdefghijk"
    output = cli_live._captured_output(
        f"request failed token='opaque-secret' response={jwt}"
    )
    assert "request failed" in output
    assert "opaque-secret" not in output
    assert jwt not in output
