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
