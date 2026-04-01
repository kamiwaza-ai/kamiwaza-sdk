"""Tests for the login command."""

import pytest
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

from kamiwaza_extensions.cli import app

runner = CliRunner()


@pytest.mark.unit
class TestLoginCommand:
    def test_login_list_empty(self, tmp_path):
        with patch("kamiwaza_extensions.connections.ConnectionManager") as MockMgr:
            mgr = MockMgr.return_value
            mgr.list_connections.return_value = []
            result = runner.invoke(app, ["login", "--list"])
            assert result.exit_code == 0
            assert "No stored connections" in result.output

    def test_login_list_with_connections(self, tmp_path):
        from kamiwaza_extensions.connections import ConnectionInfo

        with patch("kamiwaza_extensions.connections.ConnectionManager") as MockMgr:
            mgr = MockMgr.return_value
            mgr.list_connections.return_value = [
                ConnectionInfo(name="default", url="https://a.example/api", active=True, created_at=0.0),
                ConnectionInfo(name="staging", url="https://b.example/api", active=False, created_at=0.0),
            ]
            result = runner.invoke(app, ["login", "--list"])
            assert result.exit_code == 0
            assert "default" in result.output
            assert "staging" in result.output

    def test_login_use_switches_connection(self):
        with patch("kamiwaza_extensions.connections.ConnectionManager") as MockMgr:
            mgr = MockMgr.return_value
            result = runner.invoke(app, ["login", "--use", "staging"])
            assert result.exit_code == 0
            mgr.set_active.assert_called_once_with("staging")
            assert "Switched" in result.output

    def test_login_no_url_uses_default(self):
        with patch("kamiwaza_extensions.connections.ConnectionManager"):
            result = runner.invoke(app, ["login"], input="\n")  # abort at username prompt
            assert "kamiwaza.test" in result.output

    def test_login_with_api_key(self):
        with patch("kamiwaza_extensions.connections.ConnectionManager") as MockMgr:
            mgr = MockMgr.return_value
            with patch("kamiwaza_extensions.commands.login._validate_connection"):
                result = runner.invoke(app, ["login", "https://test.example/api", "--api-key", "my-key"])
                assert result.exit_code == 0
                mgr.add_connection.assert_called_once()
                call_kwargs = mgr.add_connection.call_args
                assert call_kwargs.kwargs["name"] == "default"
                assert call_kwargs.kwargs["url"] == "https://test.example/api"
