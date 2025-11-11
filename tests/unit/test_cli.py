from __future__ import annotations

import argparse
import time
from types import SimpleNamespace

import pytest

from kamiwaza_sdk import cli
from kamiwaza_sdk.exceptions import AuthenticationError
from kamiwaza_sdk.token_store import StoredToken, TokenStore

pytestmark = pytest.mark.unit


class MemoryStore(TokenStore):
    def __init__(self):
        self.value: StoredToken | None = None

    def load(self):
        return self.value

    def save(self, token: StoredToken):
        self.value = token

    def clear(self):
        self.value = None


def test_login_command_uses_authenticator(monkeypatch):
    store = MemoryStore()
    args = argparse.Namespace(base_url="https://localhost/api", username="admin", password="secret", token_path=None)
    fake_client = SimpleNamespace(auth="auth-service", session=SimpleNamespace(headers={}))

    def factory(base_url, **_):
        assert base_url == args.base_url
        return fake_client

    class FakeAuthenticator:
        def __init__(self, username, password, auth_service, *, token_store):
            self.username = username
            self.password = password
            self.auth_service = auth_service
            self.token_store = token_store

        def authenticate(self, session):
            session.headers["Authorization"] = "Bearer fake"
            self.token_store.save(StoredToken(access_token="fake", refresh_token=None, expires_at=time.time() + 60))

    cli.login_command(
        args,
        client_factory=factory,
        token_store=store,
        authenticator_cls=FakeAuthenticator,
    )

    assert store.value and store.value.access_token == "fake"


def test_pat_create_command_requires_cached_token():
    store = MemoryStore()
    args = argparse.Namespace(
        base_url="https://localhost/api",
        token_path=None,
        name="cli",
        ttl=120,
        scope="openid",
        aud="kamiwaza-platform",
        cache_token=True,
        revoke_jti=None,
    )

    with pytest.raises(AuthenticationError):
        cli.pat_create_command(args, token_store=store, client_factory=lambda *_args, **_kwargs: None)  # type: ignore


def test_pat_create_command_creates_and_caches_token(monkeypatch):
    store = MemoryStore()
    store.save(StoredToken(access_token="session", refresh_token="ref", expires_at=time.time() + 60))

    args = argparse.Namespace(
        base_url="https://localhost/api",
        token_path=None,
        name="cli",
        ttl=300,
        scope="openid",
        aud="kamiwaza-platform",
        cache_token=True,
        revoke_jti="old-jti",
    )

    create_called = {}

    class FakeAuth:
        def create_pat(self, payload):
            create_called["payload"] = payload
            pat = SimpleNamespace(exp=time.time() + 300)
            return SimpleNamespace(token="new-token", pat=pat)

        def revoke_pat(self, jti):
            create_called["revoked"] = jti

    def factory(base_url, api_key):
        assert api_key == "session"
        return SimpleNamespace(auth=FakeAuth())

    token = cli.pat_create_command(args, token_store=store, client_factory=factory)

    assert token == "new-token"
    assert store.value and store.value.access_token == "new-token"
    assert create_called["payload"].name == "cli"
    assert create_called["revoked"] == "old-jti"
