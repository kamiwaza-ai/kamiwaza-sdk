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
    def test_invalid_level_exits(self, mock_detector_cls, tmp_path):
        _write_kamiwaza_json(tmp_path)
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path)

        import typer
        from kamiwaza_extensions.commands.bump import run_bump

        with pytest.raises(typer.Exit):
            run_bump(level="mega")
