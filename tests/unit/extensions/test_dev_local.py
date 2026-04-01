"""Tests for DevLocalRunner."""

import json
from unittest.mock import patch, MagicMock

import pytest

from kamiwaza_extensions.connections import ConnectionInfo
from kamiwaza_extensions.dev_local import DevLocalRunner, build_env_overlay, detect_compose_command


@pytest.mark.unit
class TestExtensionDetection:
    def test_finds_extension_at_root(self, tmp_path, monkeypatch):
        (tmp_path / "kamiwaza.json").write_text(json.dumps({"name": "my-app"}))
        monkeypatch.chdir(tmp_path)
        runner = DevLocalRunner(config_dir=tmp_path / ".kamiwaza")
        ext_dir = runner._find_extension()
        assert ext_dir == tmp_path

    def test_finds_extension_one_level_deep(self, tmp_path, monkeypatch):
        sub = tmp_path / "my-app"
        sub.mkdir()
        (sub / "kamiwaza.json").write_text(json.dumps({"name": "my-app"}))
        monkeypatch.chdir(tmp_path)
        runner = DevLocalRunner(config_dir=tmp_path / ".kamiwaza")
        ext_dir = runner._find_extension()
        assert ext_dir == sub

    def test_errors_when_no_extension_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = DevLocalRunner(config_dir=tmp_path / ".kamiwaza")
        with pytest.raises(FileNotFoundError, match="No kamiwaza.json"):
            runner._find_extension()

    def test_errors_when_multiple_extensions(self, tmp_path, monkeypatch):
        for name in ("app-a", "app-b"):
            d = tmp_path / name
            d.mkdir()
            (d / "kamiwaza.json").write_text(json.dumps({"name": name}))
        monkeypatch.chdir(tmp_path)
        runner = DevLocalRunner(config_dir=tmp_path / ".kamiwaza")
        with pytest.raises(FileNotFoundError, match="Multiple"):
            runner._find_extension()


@pytest.mark.unit
class TestEnvOverlay:
    def test_builds_correct_overlay(self):
        conn = ConnectionInfo(name="test", url="https://example.com/api", active=True, created_at=0.0)
        overlay = build_env_overlay(conn, "my-app")
        assert overlay["KAMIWAZA_API_URL"] == "https://example.com/api"
        assert overlay["KAMIWAZA_PUBLIC_API_URL"] == "https://example.com"
        assert overlay["KAMIWAZA_USE_AUTH"] == "false"
        assert overlay["KAMIWAZA_APP_NAME"] == "my-app"

    def test_overlay_without_api_in_url(self):
        conn = ConnectionInfo(name="test", url="https://example.com", active=True, created_at=0.0)
        overlay = build_env_overlay(conn, "my-app")
        assert overlay["KAMIWAZA_PUBLIC_API_URL"] == "https://example.com"


@pytest.mark.unit
class TestComposeDetection:
    def test_detects_compose_v2(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = detect_compose_command()
            assert result == ["docker", "compose"]

    def test_falls_back_to_v1(self):
        def side_effect(cmd, **kwargs):
            if cmd == ["docker", "compose", "version"]:
                raise FileNotFoundError()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=side_effect):
            result = detect_compose_command()
            assert result == ["docker-compose"]

    def test_errors_when_no_compose(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with pytest.raises(FileNotFoundError, match="Docker Compose not found"):
                detect_compose_command()


@pytest.mark.unit
class TestComposeFileDetection:
    def test_finds_docker_compose_yml(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        runner = DevLocalRunner(config_dir=tmp_path / ".kamiwaza")
        result = runner._find_compose_file(tmp_path)
        assert result.name == "docker-compose.yml"

    def test_finds_compose_yaml(self, tmp_path):
        (tmp_path / "compose.yaml").write_text("version: '3'")
        runner = DevLocalRunner(config_dir=tmp_path / ".kamiwaza")
        result = runner._find_compose_file(tmp_path)
        assert result.name == "compose.yaml"

    def test_errors_when_no_compose_file(self, tmp_path):
        runner = DevLocalRunner(config_dir=tmp_path / ".kamiwaza")
        with pytest.raises(FileNotFoundError, match="No compose file"):
            runner._find_compose_file(tmp_path)
