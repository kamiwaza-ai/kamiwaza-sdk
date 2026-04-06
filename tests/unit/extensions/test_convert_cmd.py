"""Unit tests for kz-ext convert command."""

import json
from unittest.mock import MagicMock, patch

import pytest
import yaml

pytestmark = pytest.mark.unit


class TestRunConvert:
    """Test the convert command orchestration."""

    def _setup_app(self, tmp_path):
        """Create a minimal app for conversion."""
        compose = {
            "services": {
                "backend": {
                    "build": {"context": "./backend", "dockerfile": "Dockerfile"},
                    "ports": ["8000:8000"],
                }
            }
        }
        (tmp_path / "docker-compose.yml").write_text(yaml.dump(compose))
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "Dockerfile").write_text("FROM python:3.11\nCOPY . .\n")
        (tmp_path / "backend" / "requirements.txt").write_text("fastapi\n")
        return tmp_path

    @patch("kamiwaza_extensions.convert_agent.run_agent")
    def test_happy_path_creates_kamiwaza_json(self, mock_agent, tmp_path):
        from kamiwaza_extensions.commands.convert import run_convert
        from kamiwaza_extensions.convert_agent import ConversionPlan

        app_dir = self._setup_app(tmp_path)
        mock_agent.return_value = ConversionPlan(summary="Done")

        run_convert(path=str(app_dir), dry_run=False)

        assert (app_dir / "kamiwaza.json").exists()
        data = json.loads((app_dir / "kamiwaza.json").read_text())
        assert data["version"] == "0.1.0"
        assert data["source_type"] == "user_repo"

    @patch("kamiwaza_extensions.convert_agent.run_agent")
    def test_dry_run_does_not_create_kamiwaza_json(self, mock_agent, tmp_path):
        from kamiwaza_extensions.commands.convert import run_convert
        from kamiwaza_extensions.convert_agent import ConversionPlan

        app_dir = self._setup_app(tmp_path)
        mock_agent.return_value = ConversionPlan(summary="Preview")

        run_convert(path=str(app_dir), dry_run=True)

        assert not (app_dir / "kamiwaza.json").exists()

    @patch("kamiwaza_extensions.convert_agent.run_agent")
    def test_existing_kamiwaza_json_not_overwritten(self, mock_agent, tmp_path):
        from kamiwaza_extensions.commands.convert import run_convert
        from kamiwaza_extensions.convert_agent import ConversionPlan

        app_dir = self._setup_app(tmp_path)
        existing = {"name": "original", "version": "2.0.0"}
        (app_dir / "kamiwaza.json").write_text(json.dumps(existing))
        mock_agent.return_value = ConversionPlan(summary="Done")

        run_convert(path=str(app_dir), dry_run=False)

        data = json.loads((app_dir / "kamiwaza.json").read_text())
        assert data["version"] == "2.0.0"  # Not overwritten

    def test_nonexistent_path_exits(self):
        import typer
        from kamiwaza_extensions.commands.convert import run_convert

        with pytest.raises(typer.Exit):
            run_convert(path="/nonexistent/path", dry_run=False)

    @patch("kamiwaza_extensions.convert_agent.run_agent")
    def test_agent_modifications_reported(self, mock_agent, tmp_path, capsys):
        from kamiwaza_extensions.commands.convert import run_convert
        from kamiwaza_extensions.convert_agent import ConversionPlan, FileModification

        app_dir = self._setup_app(tmp_path)
        mock_agent.return_value = ConversionPlan(
            modifications=[
                FileModification(
                    path="backend/requirements.txt",
                    action="modify",
                    content="fastapi\nkamiwaza-extensions-lib\n",
                    description="added runtime lib",
                )
            ],
            manual_items=["Check auth middleware placement"],
            summary="Added SDK",
        )

        run_convert(path=str(app_dir), dry_run=False)
        # Verify agent was called
        mock_agent.assert_called_once()
