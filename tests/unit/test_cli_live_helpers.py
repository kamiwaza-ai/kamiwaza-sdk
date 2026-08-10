import importlib
import subprocess
import sys
from pathlib import Path

import pytest

INTEGRATION_TESTS = str(Path(__file__).parents[1] / "integration")
if INTEGRATION_TESTS not in sys.path:
    sys.path.insert(0, INTEGRATION_TESTS)

cli_live = importlib.import_module("tests.integration.test_cli_live")

pytestmark = pytest.mark.unit


def test_run_cli_reports_output_and_redacts_secret_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = subprocess.CompletedProcess(
        args=[],
        returncode=2,
        stdout="deployment rejected\n",
        stderr="model file was not found\n",
    )
    monkeypatch.setattr(cli_live.subprocess, "run", lambda *_args, **_kwargs: result)

    with pytest.raises(AssertionError) as exc_info:
        cli_live.run_cli(
            ["login", "--username", "admin", "--password", "super-secret"],
            {"PATH": "/bin"},
        )

    message = str(exc_info.value)
    assert "exit code 2" in message
    assert "deployment rejected" in message
    assert "model file was not found" in message
    assert "--password '***'" in message
    assert "super-secret" not in message


def test_run_cli_returns_successful_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='{"status": "DEPLOYED"}\n',
        stderr="",
    )
    calls: list[dict[str, object]] = []

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return result

    monkeypatch.setattr(cli_live.subprocess, "run", fake_run)

    assert cli_live.run_cli(["serve", "deploy"], {"PATH": "/bin"}) is result
    assert calls == [
        {
            "capture_output": True,
            "text": True,
            "check": False,
            "env": {"PATH": "/bin"},
        }
    ]
