"""End-to-end flow tests: create → validate via CLI."""

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from kamiwaza_extensions.cli import app

runner = CliRunner()


def _empty_dir(tmp_path, name="ext"):
    d = tmp_path / name
    d.mkdir()
    return d


@pytest.mark.unit
class TestCreateThenValidateFlow:
    """Test the full create → validate flow through the CLI."""

    def test_create_app_then_validate(self, tmp_path, monkeypatch):
        d = _empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            result = runner.invoke(app, ["create", "--type", "app", "--name", "test-app"])
        assert result.exit_code == 0
        assert "Created" in result.output

        result = runner.invoke(app, ["validate", str(d)])
        assert result.exit_code == 0
        assert "passed" in result.output.lower()

    def test_create_tool_then_validate(self, tmp_path, monkeypatch):
        d = _empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            result = runner.invoke(app, ["create", "--type", "tool", "--name", "my-tool"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["validate", str(d)])
        assert result.exit_code == 0

    def test_create_service_then_validate(self, tmp_path, monkeypatch):
        d = _empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            result = runner.invoke(app, ["create", "--type", "service", "--name", "my-svc"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["validate", str(d)])
        assert result.exit_code == 0

    def test_create_validates_json_output(self, tmp_path, monkeypatch):
        d = _empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            runner.invoke(app, ["create", "--type", "app", "--name", "json-test"])

        result = runner.invoke(app, ["validate", str(d), "--json"])
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
        d = _empty_dir(tmp_path)
        monkeypatch.chdir(d)
        result = runner.invoke(app, ["create", "--type", "bad", "--name", "test"])
        assert result.exit_code == 1
        assert "Invalid type" in result.output

    def test_create_non_empty_directory_scaffolds_into_subdir(self, tmp_path, monkeypatch):
        # Updated 2026-04-29 (ENG-3898 P1 §4.8): non-empty cwd no longer
        # errors; the scaffolder routes into ./<name>/.
        (tmp_path / "existing.txt").write_text("hello")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["create", "--type", "app", "--name", "my-app"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "my-app" / "kamiwaza.json").exists()

    def test_create_errors_when_target_subdir_has_content(self, tmp_path, monkeypatch):
        (tmp_path / "existing.txt").write_text("hello")
        target = tmp_path / "my-app"
        target.mkdir()
        (target / "leftover.py").write_text("# ...")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["create", "--type", "app", "--name", "my-app"])
        assert result.exit_code != 0
        assert "already exists" in result.output or "not empty" in result.output
