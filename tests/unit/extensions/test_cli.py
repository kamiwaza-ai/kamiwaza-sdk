"""Tests for the kz-ext CLI skeleton."""

import pytest
from click.exceptions import Exit as ClickExit
from typer.testing import CliRunner

from kamiwaza_extensions.cli import app
from kamiwaza_extensions import __version__

runner = CliRunner()


@pytest.mark.unit
class TestCLISkeleton:
    def test_help_output(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Kamiwaza extension developer tools" in result.output

    def test_version_output(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_no_args_shows_help(self):
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert "Usage" in result.output or "Commands" in result.output

    def test_subcommands_listed(self):
        result = runner.invoke(app, ["--help"])
        assert "login" in result.output
        assert "validate" in result.output
        assert "doctor" in result.output
        assert "create" in result.output
        assert "dev" in result.output
        assert "status" in result.output
        assert "logs" in result.output
        assert "shell" in result.output

    def test_dev_local_subcommand_listed(self):
        result = runner.invoke(app, ["dev", "--help"])
        assert result.exit_code == 0
        assert "local" in result.output

    def test_dev_local_help_includes_auth_flag(self):
        # TS-14 — typer recognises --auth without parsing error
        result = runner.invoke(app, ["dev", "local", "--help"])
        assert result.exit_code == 0
        assert "--auth" in result.output

    def test_dev_local_auth_flag_invokes_runner_with_auth_true(self, monkeypatch):
        # TS-15 — `kz-ext dev local --auth` calls run_dev_local(auth=True)
        captured: dict = {}

        def fake_run(*, detach, sdk_repo=None, auth=False):
            captured["detach"] = detach
            captured["sdk_repo"] = sdk_repo
            captured["auth"] = auth

        monkeypatch.setattr(
            "kamiwaza_extensions.commands.dev_local.run_dev_local", fake_run
        )
        result = runner.invoke(app, ["dev", "local", "--auth"])
        assert result.exit_code == 0
        assert captured == {"detach": False, "sdk_repo": None, "auth": True}

    def test_dev_local_without_auth_flag_defaults_to_false(self, monkeypatch):
        captured: dict = {}

        def fake_run(*, detach, sdk_repo=None, auth=False):
            captured["auth"] = auth

        monkeypatch.setattr(
            "kamiwaza_extensions.commands.dev_local.run_dev_local", fake_run
        )
        result = runner.invoke(app, ["dev", "local"])
        assert result.exit_code == 0
        assert captured["auth"] is False

    def test_dev_local_auth_localdevautherror_exits_code_2(self, monkeypatch):
        """Round-2 review High #11 — LocalDevAuthError raised from the runner
        must surface as a clean stderr message + exit code 2 (not a stack
        trace, not exit code 1)."""
        from kamiwaza_extensions_lib.local_dev import LocalDevAuthError

        from kamiwaza_extensions import dev_local as dev_local_mod

        # Stub the runner to raise the bridge error
        class StubRunner:
            def run(self, *, detach, sdk_repo=None, auth=False):
                raise LocalDevAuthError(
                    "no active Kamiwaza connection — run `kz-ext login` first"
                )

        monkeypatch.setattr(
            dev_local_mod, "DevLocalRunner", lambda *a, **kw: StubRunner()
        )

        result = runner.invoke(app, ["dev", "local", "--auth"])
        assert result.exit_code == 2
        # Developer-facing hint surfaces (not "Traceback:" or stack)
        assert "Traceback" not in result.output

    def test_unknown_command(self):
        result = runner.invoke(app, ["nonexistent"])
        assert result.exit_code != 0


@pytest.mark.unit
class TestErrorHandling:
    def test_error_handler_formats_file_not_found(self):
        from kamiwaza_extensions.cli import run_with_error_handling

        @run_with_error_handling
        def failing_func():
            raise FileNotFoundError("missing.txt")

        with pytest.raises(ClickExit):
            failing_func()

    def test_error_handler_formats_generic_exception(self):
        from kamiwaza_extensions.cli import run_with_error_handling

        @run_with_error_handling
        def failing_func():
            raise RuntimeError("something broke")

        with pytest.raises(ClickExit):
            failing_func()

    # TS-8: RuntimeLibException → exit code matches exit_code_for(class_name)
    @pytest.mark.parametrize(
        "error_cls, expected_code",
        [
            ("MisboundAuthError", 10),
            ("UnexpectedContextError", 11),
            ("OutOfEnvelopeAccessError", 12),
            ("PlatformOutageError", 13),
        ],
    )
    def test_runtime_lib_exception_maps_to_exit_code(self, error_cls, expected_code):
        from kamiwaza_extensions import cli
        import kamiwaza_extensions_lib.errors as errors_module

        cls = getattr(errors_module, error_cls)

        @cli.run_with_error_handling
        def failing_func():
            raise cls("boom")

        with pytest.raises(ClickExit) as exc_info:
            failing_func()
        assert exc_info.value.exit_code == expected_code

    def test_unknown_runtime_lib_subclass_falls_back_to_failure(self):
        """A custom KamiwazaRuntimeError subclass with an unmapped class_name
        should exit with the generic FAILURE code (1), not silently crash."""
        from kamiwaza_extensions import cli
        from kamiwaza_extensions_lib.errors import KamiwazaRuntimeError

        class CustomRuntimeError(KamiwazaRuntimeError):
            class_name = "some_novel_class_name_not_in_catalog"

        @cli.run_with_error_handling
        def failing_func():
            raise CustomRuntimeError("surprise")

        with pytest.raises(ClickExit) as exc_info:
            failing_func()
        assert exc_info.value.exit_code == 1  # ExitCode.FAILURE
