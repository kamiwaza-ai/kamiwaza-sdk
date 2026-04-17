"""Unit tests for kz-ext convert command."""

from unittest.mock import patch

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
    def test_happy_path_calls_agent(self, mock_agent, tmp_path):
        from kamiwaza_extensions.commands.convert import run_convert
        from kamiwaza_extensions.convert_agent import ConversionPlan

        app_dir = self._setup_app(tmp_path)
        mock_agent.return_value = ConversionPlan(summary="Done")

        run_convert(path=str(app_dir), dry_run=False)

        mock_agent.assert_called_once()

    @patch("kamiwaza_extensions.convert_agent.run_agent")
    def test_dry_run_does_not_create_kamiwaza_json(self, mock_agent, tmp_path):
        from kamiwaza_extensions.commands.convert import run_convert
        from kamiwaza_extensions.convert_agent import ConversionPlan

        app_dir = self._setup_app(tmp_path)
        mock_agent.return_value = ConversionPlan(summary="Preview")

        run_convert(path=str(app_dir), dry_run=True)

        assert not (app_dir / "kamiwaza.json").exists()

    @patch("kamiwaza_extensions.convert_agent.run_agent")
    def test_failed_conversion_exits_nonzero(self, mock_agent, tmp_path):
        import typer
        from kamiwaza_extensions.commands.convert import run_convert
        from kamiwaza_extensions.convert_agent import ConversionPlan

        app_dir = self._setup_app(tmp_path)
        mock_agent.return_value = ConversionPlan(
            success=False,
            summary="Failed",
            errors=["No docker-compose file found after conversion."],
        )

        with pytest.raises(typer.Exit):
            run_convert(path=str(app_dir), dry_run=False)

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

    def test_no_llm_fallback_still_creates_manifest(self, monkeypatch, tmp_path):
        from kamiwaza_extensions.commands.convert import run_convert

        (tmp_path / "index.html").write_text("<html><body>Hello</body></html>")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        run_convert(path=str(tmp_path), dry_run=False)

        assert (tmp_path / "kamiwaza.json").exists()
        assert (tmp_path / "CONVERT_NOTES.md").exists()
