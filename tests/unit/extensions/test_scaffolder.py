"""Tests for Scaffolder."""

import json
from unittest.mock import patch

import pytest

from kamiwaza_extensions.scaffolder import Scaffolder


@pytest.mark.unit
class TestScaffolder:
    @pytest.fixture
    def scaffolder(self):
        return Scaffolder()

    def test_create_app(self, tmp_path, monkeypatch, scaffolder):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):  # mock git init
            result = scaffolder.create(type_="app", name="my-app")

        assert result.exists()
        assert (result / "kamiwaza.json").exists()
        assert (result / "docker-compose.yml").exists()
        assert (result / "frontend" / "Dockerfile").exists()
        assert (result / "backend" / "Dockerfile").exists()
        assert (result / "backend" / "requirements.txt").exists()
        assert (result / ".gitignore").exists()

        # Check template substitution
        meta = json.loads((result / "kamiwaza.json").read_text())
        assert meta["name"] == "my-app"
        assert meta["type"] == "app"
        assert meta["version"] == "0.1.0"

    def test_create_tool_auto_prefix(self, tmp_path, monkeypatch, scaffolder):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):
            result = scaffolder.create(type_="tool", name="my-tool")

        # Should auto-prefix
        assert result.name == "tool-my-tool"
        meta = json.loads((result / "kamiwaza.json").read_text())
        assert meta["name"] == "tool-my-tool"
        assert (result / "src" / "server.py").exists()

    def test_create_tool_with_prefix_no_double(self, tmp_path, monkeypatch, scaffolder):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):
            result = scaffolder.create(type_="tool", name="tool-existing")
        assert result.name == "tool-existing"

    def test_create_service(self, tmp_path, monkeypatch, scaffolder):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):
            result = scaffolder.create(type_="service", name="my-svc")

        assert result.name == "service-my-svc"
        assert (result / "kamiwaza.json").exists()
        assert (result / "Dockerfile").exists()

    def test_directory_conflict_error(self, tmp_path, monkeypatch, scaffolder):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "my-app").mkdir()
        with pytest.raises(FileExistsError, match="already exists"):
            scaffolder.create(type_="app", name="my-app")

    def test_invalid_name(self, scaffolder):
        with pytest.raises(ValueError, match="Invalid name"):
            scaffolder.create(type_="app", name="Bad Name!")

    def test_invalid_type(self, scaffolder):
        with pytest.raises(ValueError, match="Invalid type"):
            scaffolder.create(type_="invalid", name="test")

    def test_template_substitution_in_files(self, tmp_path, monkeypatch, scaffolder):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):
            result = scaffolder.create(type_="app", name="test-app")

        # Check substitution in backend main.py
        main_py = (result / "backend" / "app" / "main.py").read_text()
        assert "test-app" in main_py
        assert "{{name}}" not in main_py

    def test_git_init_called(self, tmp_path, monkeypatch, scaffolder):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run") as mock_run:
            scaffolder.create(type_="service", name="svc")
        mock_run.assert_called_once()
        assert mock_run.call_args.args[0] == ["git", "init"]

    def test_git_init_failure_is_non_fatal(self, tmp_path, monkeypatch, scaffolder):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = scaffolder.create(type_="app", name="no-git")
        assert result.exists()  # Should still succeed


@pytest.mark.unit
class TestScaffoldThenValidate:
    """Integration-style: scaffold each type, then validate the output."""

    def test_app_passes_validation(self, tmp_path, monkeypatch):
        from kamiwaza_extensions.scaffolder import Scaffolder
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):
            result = Scaffolder().create(type_="app", name="valid-app")

        validator = MetadataValidator()
        vr = validator.validate(result / "kamiwaza.json")
        assert vr.passed, f"Validation failed: {vr.errors}"

    def test_tool_passes_validation(self, tmp_path, monkeypatch):
        from kamiwaza_extensions.scaffolder import Scaffolder
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):
            result = Scaffolder().create(type_="tool", name="my-tool")

        validator = MetadataValidator()
        vr = validator.validate(result / "kamiwaza.json")
        assert vr.passed, f"Validation failed: {vr.errors}"

    def test_service_passes_validation(self, tmp_path, monkeypatch):
        from kamiwaza_extensions.scaffolder import Scaffolder
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        monkeypatch.chdir(tmp_path)
        with patch("subprocess.run"):
            result = Scaffolder().create(type_="service", name="my-svc")

        validator = MetadataValidator()
        vr = validator.validate(result / "kamiwaza.json")
        assert vr.passed, f"Validation failed: {vr.errors}"
