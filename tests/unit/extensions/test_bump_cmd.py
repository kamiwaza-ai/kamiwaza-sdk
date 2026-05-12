"""Tests for kz-ext bump command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _write_kamiwaza_json(path: Path, version: str = "1.2.3") -> Path:
    """Create a minimal kamiwaza.json and return its path."""
    kj = path / "kamiwaza.json"
    kj.write_text(json.dumps({"name": "test-app", "version": version}, indent=4) + "\n")
    return kj


def _make_extension_info(tmp_path: Path, version: str = "1.2.3"):
    from kamiwaza_extensions.extension_detector import ExtensionInfo

    return ExtensionInfo(
        path=tmp_path,
        name="test-app",
        version=version,
        metadata={"name": "test-app", "version": version},
    )


class TestBump:
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_patch_bump(self, mock_detector_cls, tmp_path):
        kj = _write_kamiwaza_json(tmp_path, "1.2.3")
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "1.2.3")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="patch")

        data = json.loads(kj.read_text())
        assert data["version"] == "1.2.4"

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_minor_bump(self, mock_detector_cls, tmp_path):
        kj = _write_kamiwaza_json(tmp_path, "1.2.3")
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "1.2.3")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="minor")

        data = json.loads(kj.read_text())
        assert data["version"] == "1.3.0"

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_major_bump(self, mock_detector_cls, tmp_path):
        kj = _write_kamiwaza_json(tmp_path, "1.2.3")
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "1.2.3")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="major")

        data = json.loads(kj.read_text())
        assert data["version"] == "2.0.0"

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_default_is_patch(self, mock_detector_cls, tmp_path):
        kj = _write_kamiwaza_json(tmp_path, "0.1.0")
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "0.1.0")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump()

        data = json.loads(kj.read_text())
        assert data["version"] == "0.1.1"

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_preserves_other_fields(self, mock_detector_cls, tmp_path):
        kj = tmp_path / "kamiwaza.json"
        kj.write_text(json.dumps({
            "name": "test-app",
            "version": "1.0.0",
            "description": "keep me",
            "tags": ["ai"],
        }, indent=4) + "\n")
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "1.0.0")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="patch")

        data = json.loads(kj.read_text())
        assert data["version"] == "1.0.1"
        assert data["description"] == "keep me"
        assert data["tags"] == ["ai"]

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_propagates_image_tag(self, mock_detector_cls, tmp_path):
        kj = tmp_path / "kamiwaza.json"
        kj.write_text(json.dumps({
            "name": "test-app",
            "version": "2.0.14",
            "image": "ghcr.io/kamiwaza/omniparse:2.0.14",
        }, indent=4) + "\n")
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="minor")

        data = json.loads(kj.read_text())
        assert data["version"] == "2.1.0"
        assert data["image"] == "ghcr.io/kamiwaza/omniparse:2.1.0"

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_leaves_latest_tag_alone(self, mock_detector_cls, tmp_path):
        kj = tmp_path / "kamiwaza.json"
        kj.write_text(json.dumps({
            "name": "test-app",
            "version": "2.0.14",
            "image": "ghcr.io/kamiwaza/omniparse:latest",
        }, indent=4) + "\n")
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="patch")

        data = json.loads(kj.read_text())
        assert data["image"] == "ghcr.io/kamiwaza/omniparse:latest"

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_propagates_compose_files(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "2.0.14")
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            "services:\n"
            "  app:\n"
            "    image: ghcr.io/kamiwaza/omniparse:2.0.14\n"
            "  db:\n"
            "    image: postgres:16\n"
        )
        appgarden = tmp_path / "docker-compose.appgarden.yml"
        appgarden.write_text(
            "services:\n"
            "  app:\n"
            "    image: ghcr.io/kamiwaza/omniparse:2.0.14\n"
        )
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="minor")

        assert "ghcr.io/kamiwaza/omniparse:2.1.0" in compose.read_text()
        assert "postgres:16" in compose.read_text()
        assert "ghcr.io/kamiwaza/omniparse:2.1.0" in appgarden.read_text()

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_propagates_dockerfile_arg(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "2.0.14")
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text(
            "FROM python:3.11\n"
            "ARG OMNIPARSE_VERSION=2.0.14\n"
            "ARG UNRELATED=something-else\n"
        )
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="minor")

        content = dockerfile.read_text()
        assert "ARG OMNIPARSE_VERSION=2.1.0" in content
        assert "ARG UNRELATED=something-else" in content

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_propagates_pyproject(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "2.0.14")
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "# top comment\n"
            "[project]\n"
            'name = "omniparse"\n'
            'version = "2.0.14"  # bumped by kz-ext\n'
            "\n"
            "[tool.ruff]\n"
            "line-length = 88\n"
        )
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="patch")

        content = pyproject.read_text()
        assert 'version = "2.0.15"' in content
        assert "# bumped by kz-ext" in content  # comment preserved
        assert "# top comment" in content

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_propagates_package_json(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "2.0.14")
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({"name": "x", "version": "2.0.14"}, indent=2) + "\n")
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="major")

        data = json.loads(pkg.read_text())
        assert data["version"] == "3.0.0"

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_only_kamiwaza_json_present(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "1.0.0")
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "1.0.0")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="patch")  # should not raise

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_dry_run_does_not_write(self, mock_detector_cls, tmp_path):
        kj = tmp_path / "kamiwaza.json"
        kj.write_text(json.dumps({
            "name": "test-app",
            "version": "2.0.14",
            "image": "ghcr.io/x/y:2.0.14",
        }, indent=4) + "\n")
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  app:\n    image: ghcr.io/x/y:2.0.14\n")
        before_kj = kj.read_text()
        before_compose = compose.read_text()
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="minor", dry_run=True)

        assert kj.read_text() == before_kj
        assert compose.read_text() == before_compose

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_compose_handles_registry_port(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "2.0.14")
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            "services:\n"
            "  app:\n"
            "    image: localhost:5000/omniparse:2.0.14\n"
        )
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="minor")

        assert "localhost:5000/omniparse:2.1.0" in compose.read_text()

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_compose_preserves_digest_suffix(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "2.0.14")
        compose = tmp_path / "docker-compose.yml"
        digest = "sha256:" + "a" * 64
        compose.write_text(
            f"services:\n  app:\n    image: ghcr.io/x/y:2.0.14@{digest}\n"
        )
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="patch")

        content = compose.read_text()
        assert f"ghcr.io/x/y:2.0.15@{digest}" in content

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_compose_handles_quoted_image(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "2.0.14")
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            'services:\n  app:\n    image: "ghcr.io/x/y:2.0.14"\n'
        )
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="minor")

        assert '"ghcr.io/x/y:2.1.0"' in compose.read_text()

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_pyproject_with_arrays_before_version(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "2.0.14")
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[project]\n"
            'name = "omniparse"\n'
            'classifiers = [\n'
            '    "Topic :: Software Development",\n'
            '    "License :: OSI Approved",\n'
            ']\n'
            'dependencies = ["requests", "typer"]\n'
            'version = "2.0.14"\n'
            "\n"
            "[tool.ruff]\n"
            "line-length = 88\n"
        )
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="minor")

        assert 'version = "2.1.0"' in pyproject.read_text()

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_propagates_nested_package_json(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "2.0.14")
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        pkg = frontend / "package.json"
        pkg.write_text(json.dumps({"name": "x", "version": "2.0.14"}, indent=2) + "\n")
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="minor")

        data = json.loads(pkg.read_text())
        assert data["version"] == "2.1.0"

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_package_json_preserves_indent(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "2.0.14")
        pkg = tmp_path / "package.json"
        # Use 4-space indent; expect it to survive.
        original = (
            '{\n'
            '    "name": "x",\n'
            '    "version": "2.0.14",\n'
            '    "scripts": {\n'
            '        "build": "next build"\n'
            '    }\n'
            '}\n'
        )
        pkg.write_text(original)
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="patch")

        content = pkg.read_text()
        assert '"version": "2.0.15"' in content
        assert '    "name": "x"' in content  # 4-space indent preserved
        assert '"scripts"' in content

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_dockerfile_arg_with_trailing_comment(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path, "2.0.14")
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text(
            "FROM python:3.11\n"
            "ARG OMNIPARSE_VERSION=2.0.14  # injected at build\n"
        )
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="minor")

        content = dockerfile.read_text()
        assert "ARG OMNIPARSE_VERSION=2.1.0" in content
        assert "# injected at build" in content

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_image_with_digest_in_kamiwaza_json(self, mock_detector_cls, tmp_path):
        kj = tmp_path / "kamiwaza.json"
        digest = "sha256:" + "b" * 64
        kj.write_text(json.dumps({
            "name": "test-app",
            "version": "2.0.14",
            "image": f"ghcr.io/x/y:2.0.14@{digest}",
        }, indent=4) + "\n")
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path, "2.0.14")

        from kamiwaza_extensions.commands.bump import run_bump
        run_bump(level="minor")

        data = json.loads(kj.read_text())
        assert data["image"] == f"ghcr.io/x/y:2.1.0@{digest}"

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_invalid_level_exits(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path)
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path)

        import typer
        from kamiwaza_extensions.commands.bump import run_bump

        with pytest.raises(typer.Exit):
            run_bump(level="mega")
