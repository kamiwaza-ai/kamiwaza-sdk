"""Tests for the validate CLI command."""

import json

import pytest
from typer.testing import CliRunner

from kamiwaza_extensions.cli import app

runner = CliRunner()


def _valid_metadata():
    return {
        "name": "my-app",
        "version": "1.0.0",
        "source_type": "kamiwaza",
        "visibility": "public",
        "description": "A test extension",
        "risk_tier": 0,
        "verified": False,
    }


@pytest.mark.unit
class TestValidateCommand:
    def test_validate_passes_with_valid_metadata(self, tmp_path):
        (tmp_path / "kamiwaza.json").write_text(json.dumps(_valid_metadata()))
        result = runner.invoke(app, ["validate", str(tmp_path)])
        assert result.exit_code == 0
        assert "passed" in result.output.lower()

    def test_validate_fails_with_invalid_metadata(self, tmp_path):
        (tmp_path / "kamiwaza.json").write_text(json.dumps({"name": "x"}))
        result = runner.invoke(app, ["validate", str(tmp_path)])
        assert result.exit_code == 1

    def test_validate_no_kamiwaza_json(self, tmp_path):
        result = runner.invoke(app, ["validate", str(tmp_path)])
        assert result.exit_code == 1
        assert "No kamiwaza.json" in result.output

    def test_validate_json_output(self, tmp_path):
        (tmp_path / "kamiwaza.json").write_text(json.dumps(_valid_metadata()))
        result = runner.invoke(app, ["validate", str(tmp_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["passed"] is True
        assert data["errors"] == []

    def test_validate_exit_code_0_with_warnings(self, tmp_path):
        meta = _valid_metadata()
        meta["preview_image"] = "wrong/path.png"
        (tmp_path / "kamiwaza.json").write_text(json.dumps(meta))
        result = runner.invoke(app, ["validate", str(tmp_path)])
        assert result.exit_code == 0  # warnings don't fail
