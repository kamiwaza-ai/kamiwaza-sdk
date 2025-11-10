from __future__ import annotations

import pytest
import requests
from datetime import datetime, timedelta

from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import AuthenticationError
from kamiwaza_sdk.schemas.auth import TokenResponse

pytestmark = pytest.mark.unit


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


def test_user_password_authenticator_performs_password_grant():
    token = TokenResponse(access_token="token-1", expires_in=60, refresh_token="refresh-1")
    auth_service = DummyAuthService(login_response=token)
    authenticator = UserPasswordAuthenticator("admin", "secret", auth_service)

    session = requests.Session()
    authenticator.authenticate(session)

    assert auth_service.login_calls == [("admin", "secret")]
    assert session.headers["Authorization"] == f"Bearer {token.access_token}"
    assert authenticator.token == token.access_token
    assert authenticator.refresh_token_value == token.refresh_token


def test_user_password_authenticator_prefers_refresh_when_token_expires():
    login_token = TokenResponse(access_token="token-1", expires_in=1, refresh_token="refresh-1")
    refresh_token = TokenResponse(access_token="token-2", expires_in=60, refresh_token="refresh-2")
    auth_service = DummyAuthService(login_response=login_token, refresh_response=refresh_token)
    authenticator = UserPasswordAuthenticator("admin", "secret", auth_service)
    session = requests.Session()

    authenticator.authenticate(session)
    # Force expiry to trigger refresh path
    authenticator.token_expiry = datetime.utcnow() - timedelta(seconds=1)
    authenticator.authenticate(session)

    assert auth_service.login_calls == [("admin", "secret")]
    assert auth_service.refresh_calls == ["refresh-1"]
    assert session.headers["Authorization"] == f"Bearer {refresh_token.access_token}"
    assert authenticator.token == refresh_token.access_token
    assert authenticator.refresh_token_value == refresh_token.refresh_token


def test_user_password_authenticator_raises_when_login_fails():
    auth_service = DummyAuthService(
        login_error=RuntimeError("bad credentials"),
    )
    authenticator = UserPasswordAuthenticator("admin", "wrong", auth_service)
    session = requests.Session()

    with pytest.raises(AuthenticationError):
        authenticator.authenticate(session)
