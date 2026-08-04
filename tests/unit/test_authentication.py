from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

from kamiwaza_sdk.authentication import ApiKeyAuthenticator, UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import AuthenticationError
from kamiwaza_sdk.schemas.auth import TokenResponse
from kamiwaza_sdk.token_store import StoredToken, TokenStore

pytestmark = pytest.mark.unit


class MemoryTokenStore(TokenStore):
    def __init__(self):
        self.value: StoredToken | None = None

    def load(self):
        return self.value

    def save(self, token: StoredToken):
        self.value = token

    def clear(self):
        self.value = None


class DummyAuthService:
    def __init__(
        self,
        *,
        login_response: TokenResponse | None = None,
        refresh_response: TokenResponse | None = None,
        login_error: Exception | None = None,
        refresh_error: Exception | None = None,
    ):
        self.login_response = login_response
        self.refresh_response = refresh_response
        self.login_error = login_error
        self.refresh_error = refresh_error
        self.login_calls: list[tuple[str, str]] = []
        self.refresh_calls: list[str] = []

    def login_with_password(self, username: str, password: str) -> TokenResponse:
        self.login_calls.append((username, password))
        if self.login_error:
            raise self.login_error
        if not self.login_response:
            raise RuntimeError("login response not configured")
        return self.login_response

    def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        self.refresh_calls.append(refresh_token)
        if self.refresh_error:
            raise self.refresh_error
        if not self.refresh_response:
            raise RuntimeError("refresh response not configured")
        return self.refresh_response


def test_api_key_authenticator_invalidation_is_fail_closed() -> None:
    authenticator = ApiKeyAuthenticator("fixed-token")
    session = requests.Session()
    authenticator.authenticate(session)

    assert authenticator.invalidate_session(session) is False
    assert session.headers["Authorization"] == "Bearer fixed-token"


def test_user_password_authenticator_performs_password_grant():
    token = TokenResponse(access_token="token-1", expires_in=60, refresh_token="refresh-1")
    auth_service = DummyAuthService(login_response=token)
    store = MemoryTokenStore()
    authenticator = UserPasswordAuthenticator("admin", "secret", auth_service, token_store=store)

    session = requests.Session()
    authenticator.authenticate(session)

    assert auth_service.login_calls == [("admin", "secret")]
    assert session.headers["Authorization"] == f"Bearer {token.access_token}"
    assert authenticator.token == token.access_token
    assert authenticator.refresh_token_value == token.refresh_token
    assert store.value is not None


def test_user_password_authenticator_prefers_refresh_when_token_expires():
    login_token = TokenResponse(access_token="token-1", expires_in=1, refresh_token="refresh-1")
    refresh_token = TokenResponse(access_token="token-2", expires_in=60, refresh_token="refresh-2")
    auth_service = DummyAuthService(login_response=login_token, refresh_response=refresh_token)
    store = MemoryTokenStore()
    authenticator = UserPasswordAuthenticator("admin", "secret", auth_service, token_store=store)
    session = requests.Session()

    authenticator.authenticate(session)
    # Force expiry to trigger refresh path
    authenticator.token_expiry = datetime.now(timezone.utc) - timedelta(seconds=1)
    authenticator.authenticate(session)

    assert auth_service.login_calls == [("admin", "secret")]
    assert auth_service.refresh_calls == ["refresh-1"]
    assert session.headers["Authorization"] == f"Bearer {refresh_token.access_token}"
    assert authenticator.token == refresh_token.access_token
    assert authenticator.refresh_token_value == refresh_token.refresh_token
    assert store.value and store.value.access_token == refresh_token.access_token


def test_user_password_authenticator_raises_when_login_fails():
    auth_service = DummyAuthService(
        login_error=RuntimeError("bad credentials"),
    )
    authenticator = UserPasswordAuthenticator("admin", "wrong", auth_service, token_store=MemoryTokenStore())
    session = requests.Session()

    with pytest.raises(AuthenticationError):
        authenticator.authenticate(session)


def test_user_password_authenticator_uses_cached_token():
    store = MemoryTokenStore()
    store.value = StoredToken(access_token="cached", refresh_token=None, expires_at=time.time() + 60)
    auth_service = DummyAuthService(login_error=RuntimeError("should not login"))
    authenticator = UserPasswordAuthenticator("admin", "secret", auth_service, token_store=store)
    session = requests.Session()

    authenticator.authenticate(session)

    assert auth_service.login_calls == []
    assert session.headers["Authorization"] == "Bearer cached"


def test_user_password_authenticator_invalidates_cached_session() -> None:
    store = MemoryTokenStore()
    store.value = StoredToken(
        access_token="scoped",
        refresh_token="scoped-refresh",
        expires_at=time.time() + 60,
    )
    auth_service = DummyAuthService(login_error=RuntimeError("not used"))
    authenticator = UserPasswordAuthenticator(
        "admin", "secret", auth_service, token_store=store
    )
    session = requests.Session()
    session.headers["Authorization"] = "Bearer scoped"
    session.cookies.set("access_token", "scoped")
    session.cookies.set("access_token", "other-scope", domain="other.example")
    session.cookies.set("load_balancer_affinity", "sticky")

    assert authenticator.invalidate_session(session) is True

    assert authenticator.token is None
    assert authenticator.refresh_token_value is None
    assert authenticator.token_expiry is None
    assert store.value is not None
    assert store.value.access_token == "scoped"
    assert "Authorization" not in session.headers
    assert all(cookie.name != "access_token" for cookie in session.cookies)
    assert session.cookies.get("load_balancer_affinity") == "sticky"


def test_invalidated_session_clears_stale_cache_after_password_grant_fails() -> None:
    store = MemoryTokenStore()
    store.value = StoredToken(
        access_token="scoped",
        refresh_token="scoped-refresh",
        expires_at=time.time() + 60,
    )
    authenticator = UserPasswordAuthenticator(
        "admin",
        "wrong",
        DummyAuthService(login_error=RuntimeError("bad credentials")),
        token_store=store,
    )
    session = requests.Session()

    authenticator.invalidate_session(session)
    with pytest.raises(AuthenticationError, match="bad credentials"):
        authenticator.authenticate(session)

    assert store.value is None

    replacement = StoredToken(
        access_token="unrelated-process-token",
        refresh_token=None,
        expires_at=time.time() + 60,
    )
    store.save(replacement)
    with pytest.raises(AuthenticationError, match="bad credentials"):
        authenticator.authenticate(session)

    assert store.value is replacement
