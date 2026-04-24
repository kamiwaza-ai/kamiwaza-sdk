"""Tests for the login command."""

import base64
import json

import pytest
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

from kamiwaza_extensions.cli import app

runner = CliRunner()


def _encode_jwt(payload: dict) -> str:
    """Produce a JWT-shaped string with *payload* as the body (no signature)."""
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    body = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    )
    return f"{header}.{body}.signature-ignored"


@pytest.mark.unit
class TestPATRoleDecoding:
    """TS-32: decode PAT roles claim (no signature verification)."""

    def test_returns_roles_from_jwt_body(self):
        from kamiwaza_extensions.commands.login import _decode_pat_roles

        token = _encode_jwt({"sub": "u1", "roles": ["member", "editor"]})
        assert _decode_pat_roles(token) == {"member", "editor"}

    def test_returns_empty_when_no_roles_claim(self):
        from kamiwaza_extensions.commands.login import _decode_pat_roles

        token = _encode_jwt({"sub": "u1"})
        assert _decode_pat_roles(token) == set()

    def test_returns_empty_on_malformed_token(self):
        from kamiwaza_extensions.commands.login import _decode_pat_roles

        assert _decode_pat_roles("not-a-jwt") == set()


@pytest.mark.unit
class TestRoleSetWarning:
    """TS-32: warn when UI role-set is a strict superset of PAT role-set."""

    def test_warns_on_strict_superset(self, capsys):
        from kamiwaza_extensions.commands.login import _warn_if_roles_downgraded

        _warn_if_roles_downgraded(
            pat_roles={"member"},
            ui_roles={"member", "admin"},
        )
        out = capsys.readouterr().out
        assert "admin" in out
        assert "PAT" in out or "role" in out.lower()

    def test_quiet_when_equal(self, capsys):
        from kamiwaza_extensions.commands.login import _warn_if_roles_downgraded

        _warn_if_roles_downgraded(
            pat_roles={"member", "admin"},
            ui_roles={"member", "admin"},
        )
        assert capsys.readouterr().out == ""

    def test_quiet_when_pat_is_superset(self, capsys):
        """PAT roles larger than UI — unusual but not a downgrade; no warning."""
        from kamiwaza_extensions.commands.login import _warn_if_roles_downgraded

        _warn_if_roles_downgraded(
            pat_roles={"member", "admin"},
            ui_roles={"member"},
        )
        assert capsys.readouterr().out == ""

    def test_quiet_when_ui_roles_empty(self, capsys):
        """If /whoami failed or returned nothing, don't guess — stay silent."""
        from kamiwaza_extensions.commands.login import _warn_if_roles_downgraded

        _warn_if_roles_downgraded(pat_roles={"member"}, ui_roles=set())
        assert capsys.readouterr().out == ""


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
            with patch("kamiwaza_extensions.commands.login._validate_token", return_value=True):
                result = runner.invoke(app, ["login", "https://test.example/api", "--api-key", "my-key"])
                assert result.exit_code == 0
                mgr.add_connection.assert_called_once()
                call_kwargs = mgr.add_connection.call_args
                assert call_kwargs.kwargs["name"] == "default"
                assert call_kwargs.kwargs["url"] == "https://test.example/api"

    def test_login_with_bad_api_key_rejected(self):
        with patch("kamiwaza_extensions.connections.ConnectionManager") as MockMgr:
            mgr = MockMgr.return_value
            with patch("kamiwaza_extensions.commands.login._validate_token", return_value=False):
                result = runner.invoke(app, ["login", "https://test.example/api", "--api-key", "bad-key"])
                assert result.exit_code == 1
                assert "Could not validate" in result.output
                mgr.add_connection.assert_not_called()
