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

    def test_returns_empty_for_none_token(self):
        """If create_pat returns no token (or a non-string), decoding must
        not crash — login already stored the connection and a NoneType
        AttributeError here would surprise the user (PR review High #4)."""
        from kamiwaza_extensions.commands.login import _decode_pat_roles

        assert _decode_pat_roles(None) == set()
        assert _decode_pat_roles(123) == set()  # type: ignore[arg-type]

    def test_returns_empty_for_oversized_payload(self):
        """Defensive cap on attacker-controlled JWT payload size (Medium Low #1)."""
        from kamiwaza_extensions.commands.login import (
            _PAT_PAYLOAD_MAX_BYTES,
            _decode_pat_roles,
        )

        bloated = "a" * (_PAT_PAYLOAD_MAX_BYTES + 1)
        token = f"hdr.{bloated}.sig"
        assert _decode_pat_roles(token) == set()


@pytest.mark.unit
class TestCaptureUIRoles:
    """TS-32: UI roles come from the password-session /auth/users/me call,
    not from the just-minted PAT (which would be self-referential)."""

    def test_reads_roles_from_sdk_current_user(self):
        from kamiwaza_extensions.commands.login import _capture_ui_roles

        user = MagicMock()
        user.roles = ["member", "admin"]
        client = MagicMock()
        client.auth.get_current_user.return_value = user

        assert _capture_ui_roles(client) == {"member", "admin"}
        client.auth.get_current_user.assert_called_once_with()

    def test_returns_empty_on_sdk_exception(self):
        from kamiwaza_extensions.commands.login import _capture_ui_roles

        client = MagicMock()
        client.auth.get_current_user.side_effect = RuntimeError("network")

        assert _capture_ui_roles(client) == set()

    def test_returns_empty_when_roles_missing(self):
        from kamiwaza_extensions.commands.login import _capture_ui_roles

        user = MagicMock()
        user.roles = None
        client = MagicMock()
        client.auth.get_current_user.return_value = user

        assert _capture_ui_roles(client) == set()

    def test_returns_empty_when_roles_not_a_list(self):
        from kamiwaza_extensions.commands.login import _capture_ui_roles

        user = MagicMock()
        user.roles = "admin"  # wrong shape
        client = MagicMock()
        client.auth.get_current_user.return_value = user

        assert _capture_ui_roles(client) == set()

    def test_verbose_emits_diagnostic_on_sdk_exception(self, capsys):
        """PR review High #3: silent failure masks the B3 warning. Under
        --verbose, operators should see why the role-set capture failed."""
        from kamiwaza_extensions.commands.login import _capture_ui_roles

        client = MagicMock()
        client.auth.get_current_user.side_effect = RuntimeError("network down")

        assert _capture_ui_roles(client, verbose=True) == set()
        out = capsys.readouterr().out
        # Diagnostic surfaces the exception type AND message
        assert "RuntimeError" in out
        assert "network down" in out

    def test_quiet_mode_emits_no_diagnostic_on_failure(self, capsys):
        """Default behavior must remain silent — the warning is best-effort
        and we don't pollute stdout when nothing went wrong from the user's POV."""
        from kamiwaza_extensions.commands.login import _capture_ui_roles

        client = MagicMock()
        client.auth.get_current_user.side_effect = RuntimeError("network down")

        assert _capture_ui_roles(client) == set()
        assert capsys.readouterr().out == ""


@pytest.mark.unit
class TestRoleSetWarning:
    """TS-32: warn when UI role-set has roles the PAT is missing."""

    def _run(self, **kwargs) -> str:
        """Invoke the helper via a CliRunner so typer.echo is captured."""
        import typer
        from kamiwaza_extensions.commands.login import _warn_if_roles_downgraded

        app = typer.Typer()

        @app.command()
        def _cmd() -> None:
            _warn_if_roles_downgraded(**kwargs)

        return runner.invoke(app, []).output

    def test_warns_on_strict_superset(self):
        out = self._run(pat_roles={"member"}, ui_roles={"member", "admin"})
        assert "admin" in out
        assert "PAT" in out or "role" in out.lower()

    def test_warns_on_overlapping_disjoint(self):
        """PR review High #6: pat={a,b}, ui={a,c} — c is a real downgrade
        even though pat is not a strict subset. Pre-fix this case was silent."""
        out = self._run(pat_roles={"a", "b"}, ui_roles={"a", "c"})
        assert "c" in out
        # Confirm the missing-set in the message is just {c}, not the full set
        assert "['c']" in out

    def test_quiet_when_equal(self):
        assert self._run(
            pat_roles={"member", "admin"}, ui_roles={"member", "admin"}
        ) == ""

    def test_quiet_when_pat_is_superset(self):
        """PAT roles larger than UI — unusual but not a downgrade; no warning."""
        assert self._run(
            pat_roles={"member", "admin"}, ui_roles={"member"}
        ) == ""

    def test_quiet_when_ui_roles_empty(self):
        """If capture failed or returned nothing, don't guess — stay silent."""
        assert self._run(pat_roles={"member"}, ui_roles=set()) == ""


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
