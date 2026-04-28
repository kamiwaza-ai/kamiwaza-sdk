"""Tests for Scaffolder."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kamiwaza_extensions.scaffolder import Scaffolder


@pytest.mark.unit
class TestScaffolder:
    @pytest.fixture
    def scaffolder(self):
        return Scaffolder()

    def _empty_dir(self, tmp_path, name="ext"):
        """Create and return an empty subdirectory to scaffold into."""
        d = tmp_path / name
        d.mkdir()
        return d

    def test_create_app(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            result = scaffolder.create(type_="app", name="my-app")

        assert result == d
        assert (d / "kamiwaza.json").exists()
        assert (d / "docker-compose.yml").exists()
        assert (d / "frontend" / "Dockerfile").exists()
        assert (d / "backend" / "Dockerfile").exists()
        assert (d / "backend" / "requirements.txt").exists()
        assert (d / ".gitignore").exists()
        assert (d / "AGENTS.md").exists()
        assert (d / "CLAUDE.md").exists()
        assert (d / "frontend" / "public" / "kmza-icon.png").exists()

        meta = json.loads((d / "kamiwaza.json").read_text())
        assert meta["name"] == "my-app"
        assert meta["type"] == "app"
        assert meta["version"] == "0.1.0"

    def test_binary_template_assets_are_copied_without_rendering(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            scaffolder.create(type_="app", name="logo-app")

        source_logo = (
            Path(__file__).resolve().parents[3]
            / "kamiwaza_extensions"
            / "templates"
            / "app"
            / "frontend"
            / "public"
            / "kmza-icon.png"
        )
        scaffolded_logo = d / "frontend" / "public" / "kmza-icon.png"

        assert scaffolded_logo.read_bytes() == source_logo.read_bytes()

    def test_create_tool_auto_prefix(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            scaffolder.create(type_="tool", name="my-tool")

        meta = json.loads((d / "kamiwaza.json").read_text())
        assert meta["name"] == "tool-my-tool"
        assert (d / "src" / "server.py").exists()

    def test_create_tool_with_prefix_no_double(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            scaffolder.create(type_="tool", name="tool-existing")

        meta = json.loads((d / "kamiwaza.json").read_text())
        assert meta["name"] == "tool-existing"

    def test_create_service(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            scaffolder.create(type_="service", name="my-svc")

        meta = json.loads((d / "kamiwaza.json").read_text())
        assert meta["name"] == "service-my-svc"
        assert (d / "kamiwaza.json").exists()
        assert (d / "Dockerfile").exists()

    def test_non_empty_directory_error(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        (d / "existing-file.txt").write_text("hello")
        monkeypatch.chdir(d)
        with pytest.raises(FileExistsError, match="not empty"):
            scaffolder.create(type_="app", name="my-app")

    def test_hidden_files_ignored(self, tmp_path, monkeypatch, scaffolder):
        """Hidden files like .git should not trigger non-empty check."""
        d = self._empty_dir(tmp_path)
        (d / ".git").mkdir()
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            scaffolder.create(type_="service", name="my-svc")
        assert (d / "kamiwaza.json").exists()

    def test_invalid_name(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with pytest.raises(ValueError, match="Invalid name"):
            scaffolder.create(type_="app", name="Bad Name!")

    def test_invalid_type(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with pytest.raises(ValueError, match="Invalid type"):
            scaffolder.create(type_="invalid", name="test")

    def test_template_substitution_in_files(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            scaffolder.create(type_="app", name="test-app")

        main_py = (d / "backend" / "app" / "main.py").read_text()
        assert "test-app" in main_py
        assert "{{name}}" not in main_py

        page_tsx = (d / "frontend" / "src" / "app" / "page.tsx").read_text()
        assert "Select a Kamiwaza model" in page_tsx
        assert "This starter already includes" in page_tsx

        readme = (d / "README.md").read_text()
        assert "AGENTS.md" in readme
        assert "CLAUDE.md" in readme

    def test_app_template_uses_standalone_frontend_runtime(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            scaffolder.create(type_="app", name="test-app")

        start_mjs = (d / "frontend" / "start.mjs").read_text()
        assert "const STANDALONE_SERVER = path.join(STANDALONE_DIR, \"server.js\");" in start_mjs
        assert "await prepareStandaloneRuntime();" in start_mjs
        assert "startExitCode = await runNodeArgs(" in start_mjs
        assert 'HOSTNAME: "0.0.0.0"' in start_mjs

    def test_git_init_called(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run") as mock_run:
            scaffolder.create(type_="service", name="svc")
        mock_run.assert_called_once()
        assert mock_run.call_args.args[0] == ["git", "init"]

    def test_git_init_failure_is_non_fatal(self, tmp_path, monkeypatch, scaffolder):
        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = scaffolder.create(type_="app", name="no-git")
        assert result.exists()


@pytest.mark.unit
class TestScaffoldThenValidate:
    """Integration-style: scaffold each type, then validate the output."""

    def _empty_dir(self, tmp_path, name="ext"):
        d = tmp_path / name
        d.mkdir()
        return d

    def test_app_passes_validation(self, tmp_path, monkeypatch):
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            Scaffolder().create(type_="app", name="valid-app")

        vr = MetadataValidator().validate(d / "kamiwaza.json")
        assert vr.passed, f"Validation failed: {vr.errors}"

    def test_tool_passes_validation(self, tmp_path, monkeypatch):
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            Scaffolder().create(type_="tool", name="my-tool")

        vr = MetadataValidator().validate(d / "kamiwaza.json")
        assert vr.passed, f"Validation failed: {vr.errors}"

    def test_service_passes_validation(self, tmp_path, monkeypatch):
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        d = self._empty_dir(tmp_path)
        monkeypatch.chdir(d)
        with patch("subprocess.run"):
            Scaffolder().create(type_="service", name="my-svc")

        vr = MetadataValidator().validate(d / "kamiwaza.json")
        assert vr.passed, f"Validation failed: {vr.errors}"
