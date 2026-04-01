"""End-to-end flow tests: create → validate via CLI."""

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from kamiwaza_extensions.cli import app

runner = CliRunner()


@pytest.mark.unit
class TestCreateThenValidateFlow:
    """Test the full create → validate flow through the CLI."""

    def test_create_app_then_validate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):  # mock git init
            result = runner.invoke(app, ["create", "--type", "app", "--name", "test-app"])
        assert result.exit_code == 0
        assert "Created" in result.output

        # Now validate the created extension
        result = runner.invoke(app, ["validate", str(tmp_path / "test-app")])
        assert result.exit_code == 0
        assert "passed" in result.output.lower()

    def test_create_tool_then_validate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):
            result = runner.invoke(app, ["create", "--type", "tool", "--name", "my-tool"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["validate", str(tmp_path / "tool-my-tool")])
        assert result.exit_code == 0

    def test_create_service_then_validate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):
            result = runner.invoke(app, ["create", "--type", "service", "--name", "my-svc"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["validate", str(tmp_path / "service-my-svc")])
        assert result.exit_code == 0

    def test_create_validates_json_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):
            runner.invoke(app, ["create", "--type", "app", "--name", "json-test"])

        result = runner.invoke(app, ["validate", str(tmp_path / "json-test"), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["passed"] is True
        assert data["errors"] == []


@pytest.mark.unit
class TestCLIErrorFlow:
    """Test error flows through the CLI."""

    def test_validate_nonexistent_dir(self, tmp_path):
        result = runner.invoke(app, ["validate", str(tmp_path / "nonexistent")])
        assert result.exit_code == 1

    def test_create_invalid_type(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["create", "--type", "bad", "--name", "test"])
        assert result.exit_code == 1
        assert "Invalid type" in result.output

    def test_create_directory_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "my-app").mkdir()
        result = runner.invoke(app, ["create", "--type", "app", "--name", "my-app"])
        assert result.exit_code == 1
        assert "already exists" in result.output
