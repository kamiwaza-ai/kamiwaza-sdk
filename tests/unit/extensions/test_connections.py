"""Tests for ConnectionManager."""

import json

import pytest

from kamiwaza_extensions.connections import ConnectionManager
from kamiwaza_sdk.token_store import StoredToken


def _make_token(name: str = "test") -> StoredToken:
    return StoredToken(access_token=f"tok-{name}", refresh_token=None, expires_at=0.0)


@pytest.mark.unit
class TestConnectionManager:
    @pytest.fixture
    def mgr(self, tmp_path):
        return ConnectionManager(config_dir=tmp_path / ".kamiwaza")

    def test_add_first_connection_becomes_active(self, mgr):
        mgr.add_connection("default", "https://a.example/api", _make_token())
        active = mgr.get_active_connection()
        assert active is not None
        assert active.name == "default"
        assert active.url == "https://a.example/api"
        assert active.active is True

    def test_add_named_connection(self, mgr):
        mgr.add_connection("prod", "https://prod.example/api", _make_token("prod"))
        mgr.add_connection("staging", "https://staging.example/api", _make_token("staging"))
        connections = mgr.list_connections()
        assert len(connections) == 2
        names = {c.name for c in connections}
        assert names == {"prod", "staging"}

    def test_switch_active(self, mgr):
        mgr.add_connection("a", "https://a.example/api", _make_token("a"))
        mgr.add_connection("b", "https://b.example/api", _make_token("b"))
        mgr.set_active("b")
        assert mgr.get_active_connection().name == "b"

    def test_switch_to_nonexistent_raises(self, mgr):
        mgr.add_connection("a", "https://a.example/api", _make_token())
        with pytest.raises(ValueError, match="not found"):
            mgr.set_active("nonexistent")

    def test_remove_connection(self, mgr):
        mgr.add_connection("a", "https://a.example/api", _make_token("a"))
        mgr.add_connection("b", "https://b.example/api", _make_token("b"))
        mgr.remove_connection("a")
        connections = mgr.list_connections()
        assert len(connections) == 1
        assert connections[0].name == "b"

    def test_remove_active_switches_to_remaining(self, mgr):
        mgr.add_connection("a", "https://a.example/api", _make_token("a"))
        mgr.add_connection("b", "https://b.example/api", _make_token("b"))
        assert mgr.get_active_connection().name == "a"
        mgr.remove_connection("a")
        active = mgr.get_active_connection()
        assert active is not None
        assert active.name == "b"

    def test_remove_nonexistent_raises(self, mgr):
        with pytest.raises(ValueError, match="not found"):
            mgr.remove_connection("nope")

    def test_token_load_save(self, mgr):
        tok = _make_token("round-trip")
        mgr.add_connection("default", "https://a.example/api", tok)
        loaded = mgr.get_token("default")
        assert loaded is not None
        assert loaded.access_token == "tok-round-trip"

    def test_get_token_default_uses_active(self, mgr):
        mgr.add_connection("default", "https://a.example/api", _make_token("default"))
        loaded = mgr.get_token()  # no name → active
        assert loaded is not None
        assert loaded.access_token == "tok-default"

    def test_get_token_no_connections_returns_none(self, mgr):
        assert mgr.get_token() is None

    def test_corrupt_config_recovery(self, mgr):
        # Write garbage to config
        mgr.config_dir.mkdir(parents=True, exist_ok=True)
        (mgr.config_dir / "config").write_text("not json {{{")
        # Should still work (returns empty)
        connections = mgr.list_connections()
        assert connections == []

    def test_empty_list(self, mgr):
        assert mgr.list_connections() == []
        assert mgr.get_active_connection() is None
