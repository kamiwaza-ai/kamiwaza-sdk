"""Direct shared-identity-provider authentication for automated SDK clients."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests  # type: ignore[import-untyped]
from pydantic import ValidationError

from .authentication import Authenticator
from .exceptions import AuthenticationError
from .schemas.auth import TokenResponse
from .token_store import FileTokenStore, StoredToken, TokenStore

_DEFAULT_TIMEOUT_SECONDS = 30.0
_REFRESH_LEEWAY_SECONDS = 30.0


@dataclass(frozen=True)
class SharedIdpAuthConfig:
    """Configuration for a browser-independent shared-realm OIDC session."""

    issuer: str
    client_id: str
    username: str
    password: str = field(repr=False)
    client_secret: str | None = field(default=None, repr=False)
    scope: str = "openid"
    verify: bool | str = True
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    token_root: Path | str | None = None

    @property
    def normalized_issuer(self) -> str:
        """Return the issuer without a trailing slash."""
        return self.issuer.rstrip("/")


class _OidcGrantError(AuthenticationError):
    """Internal typed OAuth grant failure used to control safe fallback."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        oauth_error: str | None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.oauth_error = oauth_error


def shared_idp_token_path(
    config: SharedIdpAuthConfig,
    *,
    root: Path | str | None = None,
) -> Path:
    """Return a deterministic token path scoped to issuer, client, and user."""
    identity = [config.normalized_issuer, config.client_id, config.username]
    encoded = json.dumps(identity, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    token_root = Path(
        root or config.token_root or Path.home() / ".kamiwaza" / "shared-idp"
    )
    return token_root / f"{digest}.json"


def _default_token_store(config: SharedIdpAuthConfig) -> FileTokenStore:
    token_path = shared_idp_token_path(config)
    token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    token_path.parent.chmod(0o700)
    return FileTokenStore(token_path)


class SharedIdpAuthenticator(Authenticator):
    """Maintain a direct shared-realm session without browser interaction."""

    def __init__(
        self,
        config: SharedIdpAuthConfig,
        *,
        token_store: TokenStore | None = None,
        http_session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.token_store = (
            token_store if token_store is not None else _default_token_store(config)
        )
        self._http = http_session or requests.Session()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float | None = None
        self._load_cached_token()

    def authenticate(self, session: requests.Session) -> None:
        """Attach a valid bearer token, refreshing it proactively."""
        if self._needs_refresh():
            self.refresh_token(session)
            return
        self._attach_access_token(session)

    def refresh_token(self, session: requests.Session) -> None:
        """Refresh the shared-realm token or perform a programmatic login."""
        refresh_token = self._refresh_token
        if not refresh_token:
            response = self._password_grant()
            self._store_response(response, previous_refresh=None)
            self._attach_access_token(session)
            return

        previous_refresh: str | None = refresh_token
        try:
            response = self._refresh_grant(refresh_token)
        except _OidcGrantError as exc:
            if exc.oauth_error != "invalid_grant":
                raise
            self._clear_tokens()
            response = self._password_grant()
            previous_refresh = None
        self._store_response(response, previous_refresh=previous_refresh)
        self._attach_access_token(session)

    def get_access_token(self, session: requests.Session) -> str | None:
        """Return a current bearer token for callers that need token access."""
        self.authenticate(session)
        return self._access_token

    def invalidate_session(self, session: requests.Session) -> bool:
        """Expire the access token while retaining refresh capability."""
        self._access_token = None
        self._expires_at = None
        session.headers.pop("Authorization", None)
        return True

    def _needs_refresh(self) -> bool:
        if not self._access_token or self._expires_at is None:
            return True
        return time.time() >= self._expires_at - _REFRESH_LEEWAY_SECONDS

    def _password_grant(self) -> TokenResponse:
        data = {
            "grant_type": "password",
            "client_id": self.config.client_id,
            "username": self.config.username,
            "password": self.config.password,
            "scope": self.config.scope,
        }
        return self._request_token(self._with_client_secret(data))

    def _refresh_grant(self, refresh_token: str) -> TokenResponse:
        data = {
            "grant_type": "refresh_token",
            "client_id": self.config.client_id,
            "refresh_token": refresh_token,
        }
        return self._request_token(self._with_client_secret(data))

    def _with_client_secret(self, data: dict[str, str]) -> dict[str, str]:
        if not self.config.client_secret:
            return data
        return {**data, "client_secret": self.config.client_secret}

    def _request_token(self, data: dict[str, str]) -> TokenResponse:
        endpoint = f"{self.config.normalized_issuer}/protocol/openid-connect/token"
        try:
            response = self._http.post(
                endpoint,
                data=data,
                timeout=self.config.timeout_seconds,
                verify=self.config.verify,
            )
        except requests.RequestException as exc:
            raise AuthenticationError(
                "Shared identity provider is unavailable"
            ) from exc

        payload = self._response_payload(response)
        if response.status_code >= 400:
            raise self._grant_error(response.status_code, payload)
        if payload is None:
            raise AuthenticationError("Shared identity provider returned invalid JSON")
        try:
            return TokenResponse.model_validate(payload)
        except ValidationError as exc:
            raise AuthenticationError(
                "Shared identity provider returned an invalid token response"
            ) from exc

    @staticmethod
    def _response_payload(response: requests.Response) -> dict[str, Any] | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _grant_error(
        status_code: int,
        payload: dict[str, Any] | None,
    ) -> _OidcGrantError:
        oauth_error = payload.get("error") if payload else None
        description = payload.get("error_description") if payload else None
        detail = description or oauth_error or "token grant failed"
        return _OidcGrantError(
            f"Shared identity provider rejected the token grant: {detail}",
            status_code=status_code,
            oauth_error=oauth_error if isinstance(oauth_error, str) else None,
        )

    def _store_response(
        self,
        response: TokenResponse,
        *,
        previous_refresh: str | None,
    ) -> None:
        refresh_token = response.refresh_token or previous_refresh
        if not refresh_token:
            raise AuthenticationError(
                "Shared identity provider did not return a refresh token"
            )
        expires_at = time.time() + response.expires_in
        stored = StoredToken(
            access_token=response.access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        self.token_store.save(stored)
        self._access_token = stored.access_token
        self._refresh_token = stored.refresh_token
        self._expires_at = stored.expires_at

    def _attach_access_token(self, session: requests.Session) -> None:
        if not self._access_token:
            raise AuthenticationError(
                "Shared identity provider access token is unavailable"
            )
        session.headers["Authorization"] = f"Bearer {self._access_token}"

    def _clear_tokens(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self._expires_at = None
        self.token_store.clear()

    def _load_cached_token(self) -> None:
        cached = self.token_store.load()
        if not cached:
            return
        self._access_token = cached.access_token
        self._refresh_token = cached.refresh_token
        self._expires_at = cached.expires_at
