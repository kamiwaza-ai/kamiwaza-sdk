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

    def test_path_traversal_rejected(self, mgr):
        with pytest.raises(ValueError, match="Invalid connection name"):
            mgr.add_connection("../../.ssh", "https://evil.example", _make_token())

    def test_path_traversal_in_remove_rejected(self, mgr):
        with pytest.raises(ValueError, match="Invalid connection name"):
            mgr.remove_connection("../etc")

    def test_special_chars_rejected(self, mgr):
        with pytest.raises(ValueError, match="Invalid connection name"):
            mgr.add_connection("bad name!", "https://a.example", _make_token())


@pytest.mark.unit
class TestEffectiveVerifySsl:
    """``ConnectionInfo.effective_verify_ssl`` collapses three inputs
    (env var, dev TLD, persisted setting) into a single answer the rest
    of the codebase consumes."""

    def _make(self, url: str, verify_ssl: bool = True):
        from kamiwaza_extensions.connections import ConnectionInfo
        return ConnectionInfo(
            name="test", url=url, active=True, created_at=0.0, verify_ssl=verify_ssl
        )

    def test_env_false_wins_over_everything(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
        # Even a public hostname with persisted strict mode flips off.
        conn = self._make("https://api.kamiwaza.ai/api", verify_ssl=True)
        assert conn.effective_verify_ssl() is False

    def test_env_true_wins_over_dev_tld(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "true")
        # Dev TLD would normally auto-disable, but explicit env wins.
        conn = self._make("https://kamiwaza.test/api", verify_ssl=False)
        assert conn.effective_verify_ssl() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "FALSE", "No"])
    def test_env_false_value_variants(self, monkeypatch, value):
        """Iter-8 review (Codex): the SDK client accepts ``false``,
        ``0``, ``no`` (case-insensitive). ``effective_verify_ssl``
        must accept the same set or the host CLI and the deployed
        extension end up with divergent SSL settings — exactly the
        bug class this method exists to prevent."""
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", value)
        conn = self._make("https://api.kamiwaza.ai/api", verify_ssl=True)
        assert conn.effective_verify_ssl() is False, value

    @pytest.mark.parametrize("value", ["true", "1", "yes", "TRUE", "Yes"])
    def test_env_true_value_variants(self, monkeypatch, value):
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", value)
        conn = self._make("https://kamiwaza.test/api", verify_ssl=False)
        assert conn.effective_verify_ssl() is True, value

    @pytest.mark.parametrize(
        "url",
        [
            "https://kamiwaza.test/api",
            "https://anything.test/",
            "https://my-host.local",
            "https://localhost:8080",
            "http://127.0.0.1:7777/api",
            "https://192.168.1.10/api",
            "https://[::1]/api",
            "https://traefik.kamiwaza.svc.cluster.local/api",
        ],
    )
    def test_dev_tlds_auto_disable(self, monkeypatch, url):
        monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
        conn = self._make(url, verify_ssl=True)
        assert conn.effective_verify_ssl() is False, url

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.kamiwaza.ai/api",
            "https://platform.example.com/api",
            "https://kamiwaza.io/api",
        ],
    )
    def test_production_urls_keep_persisted(self, monkeypatch, url):
        monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
        conn = self._make(url, verify_ssl=True)
        assert conn.effective_verify_ssl() is True, url
        # And False persisted stays False.
        assert self._make(url, verify_ssl=False).effective_verify_ssl() is False

    def test_word_boundary_avoids_substring_match(self, monkeypatch):
        """``host.testing.example.com`` must NOT match ``.test`` TLD."""
        monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
        conn = self._make("https://api.testing.example.com/api", verify_ssl=True)
        assert conn.effective_verify_ssl() is True

    def test_malformed_url_falls_back_to_persisted(self, monkeypatch):
        monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
        conn = self._make("not a url", verify_ssl=True)
        assert conn.effective_verify_ssl() is True

    def test_empty_env_var_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "")
        # Empty string falls through to dev-TLD check.
        conn = self._make("https://kamiwaza.test", verify_ssl=True)
        assert conn.effective_verify_ssl() is False
