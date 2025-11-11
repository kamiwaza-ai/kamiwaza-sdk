"""Authentication helpers for the Kamiwaza SDK."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
import time
from typing import Optional, TYPE_CHECKING

import requests

from .exceptions import AuthenticationError
from .schemas.auth import TokenResponse
from .token_store import FileTokenStore, StoredToken, TokenStore

LOGGER = logging.getLogger(__name__)
_REFRESH_LEEWAY = timedelta(seconds=30)


class Authenticator(ABC):
    """Interface for client authentication strategies."""

    @abstractmethod
    def authenticate(self, session: requests.Session) -> None:
        """Ensure the session carries valid authentication state."""

    @abstractmethod
    def refresh_token(self, session: requests.Session) -> None:
        """Refresh credentials backing the session."""

    def get_access_token(self, session: requests.Session) -> Optional[str]:
        """Return the active access token, refreshing when necessary."""
        return None


class ApiKeyAuthenticator(Authenticator):
    """Simple bearer token authenticator backed by a PAT/API key."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def authenticate(self, session: requests.Session) -> None:
        session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    def refresh_token(self, session: requests.Session) -> None:  # pragma: no cover - nothing to refresh
        pass

    def get_access_token(self, session: requests.Session) -> Optional[str]:
        return self.api_key


class UserPasswordAuthenticator(Authenticator):
    """Authenticator that performs password grant and manages refresh tokens."""

    def __init__(
        self,
        username: str,
        password: str,
        auth_service,
        *,
        token_store: Optional[TokenStore] = None,
    ):
        self.username = username
        self.password = password
        self.auth_service = auth_service
        self.token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.refresh_token_value: Optional[str] = None
        self.token_store = token_store or FileTokenStore()
        self._load_cached_token()

    def authenticate(self, session: requests.Session) -> None:
        now = datetime.now(timezone.utc)
        if (
            not self.token
            or self.token_expiry is None
            or now >= self.token_expiry - _REFRESH_LEEWAY
        ):
            self.refresh_token(session)

        session.headers.update({"Authorization": f"Bearer {self.token}"})
        session.cookies.set("access_token", self.token)
        LOGGER.debug("Set bearer token via UserPasswordAuthenticator")

    def refresh_token(self, session: requests.Session) -> None:
        token_response: Optional[TokenResponse] = None
        errors: list[str] = []

        if self.refresh_token_value:
            try:
                token_response = self.auth_service.refresh_access_token(
                    self.refresh_token_value
                )
                LOGGER.debug("Refreshed access token via refresh token grant")
            except Exception as exc:  # pylint: disable=broad-except
                errors.append(f"refresh token grant failed: {exc}")
                self.refresh_token_value = None

        if token_response is None:
            try:
                token_response = self.auth_service.login_with_password(
                    self.username,
                    self.password,
                )
                LOGGER.debug("Performed password grant for new access token")
            except Exception as exc:  # pylint: disable=broad-except
                errors.append(str(exc))
                raise AuthenticationError(
                    f"Failed to obtain access token ({'; '.join(errors)})"
                ) from exc

        self._store_token_response(token_response)
        session.headers.update({"Authorization": f"Bearer {self.token}"})

    def get_access_token(self, session: requests.Session) -> Optional[str]:
        now = datetime.now(timezone.utc)
        if (
            not self.token
            or self.token_expiry is None
            or now >= self.token_expiry - _REFRESH_LEEWAY
        ):
            self.refresh_token(session)
        return self.token

    def _store_token_response(self, token_response: TokenResponse) -> None:
        self.token = token_response.access_token
        expiry_epoch = time.time() + token_response.expires_in
        self.token_expiry = datetime.fromtimestamp(expiry_epoch, tz=timezone.utc)
        if token_response.refresh_token:
            self.refresh_token_value = token_response.refresh_token
        stored = StoredToken(
            access_token=self.token,
            refresh_token=self.refresh_token_value,
            expires_at=expiry_epoch,
        )
        if self.token_store:
            self.token_store.save(stored)

    def _load_cached_token(self) -> None:
        if not self.token_store:
            return
        cached = self.token_store.load()
        if not cached:
            return
        if cached.is_expired:
            self.token_store.clear()
            return
        self.token = cached.access_token
        self.refresh_token_value = cached.refresh_token
        self.token_expiry = datetime.fromtimestamp(cached.expires_at, tz=timezone.utc)


class OAuthAuthenticator(Authenticator):
    """Placeholder for future interactive OIDC support."""

    def __init__(self, client_id: str, client_secret: str, auth_service):
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_service = auth_service
        self.token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None

    def authenticate(self, session: requests.Session) -> None:  # pragma: no cover - not implemented
        raise NotImplementedError("OAuth authentication is not yet implemented.")

    def refresh_token(self, session: requests.Session) -> None:  # pragma: no cover - not implemented
        raise NotImplementedError("OAuth authentication is not yet implemented.")
