from __future__ import annotations

import stat
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import requests

from kamiwaza_sdk.exceptions import AuthenticationError
from kamiwaza_sdk.shared_idp_authentication import (
    SharedIdpAuthConfig,
    SharedIdpAuthenticator,
    shared_idp_token_path,
)
from kamiwaza_sdk.token_store import FileTokenStore, InMemoryTokenStore, StoredToken

pytestmark = pytest.mark.unit


class StubResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any] | None = None,
        *,
        invalid_json: bool = False,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._invalid_json = invalid_json

    def json(self) -> dict[str, Any]:
        if self._invalid_json:
            raise requests.exceptions.JSONDecodeError("invalid", "", 0)
        return self._payload or {}


def _config(**overrides: Any) -> SharedIdpAuthConfig:
    values: dict[str, Any] = {
        "issuer": "https://shared.example/realms/federation",
        "client_id": "kamiwaza-federation",
        "username": "fed-clr-s",
        "password": "test-password",
        "verify": "/tmp/test-ca.pem",
        "timeout_seconds": 12.0,
    }
    values.update(overrides)
    return SharedIdpAuthConfig(**values)


def _token_response(
    access_token: str,
    refresh_token: str | None,
    *,
    expires_in: int = 300,
) -> StubResponse:
    payload = {
        "access_token": access_token,
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
    }
    return StubResponse(200, payload)


def _authenticator(
    responses: list[StubResponse | Exception],
    *,
    config: SharedIdpAuthConfig | None = None,
    token_store: InMemoryTokenStore | FileTokenStore | None = None,
) -> tuple[SharedIdpAuthenticator, Mock]:
    http = Mock(spec=requests.Session)
    http.post.side_effect = responses
    authenticator = SharedIdpAuthenticator(
        config or _config(),
        token_store=token_store or InMemoryTokenStore(),
        http_session=http,
    )
    return authenticator, http


def test_password_grant_is_direct_and_fully_programmatic() -> None:
    store = InMemoryTokenStore()
    authenticator, http = _authenticator(
        [_token_response("access-1", "refresh-1")],
        token_store=store,
    )
    platform_session = requests.Session()

    authenticator.authenticate(platform_session)

    http.post.assert_called_once_with(
        "https://shared.example/realms/federation/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "kamiwaza-federation",
            "username": "fed-clr-s",
            "password": "test-password",
            "scope": "openid",
        },
        timeout=12.0,
        verify="/tmp/test-ca.pem",
    )
    assert platform_session.headers["Authorization"] == "Bearer access-1"
    assert platform_session.cookies.get("access_token") is None
    assert store.load() is not None
    assert store.load().refresh_token == "refresh-1"  # type: ignore[union-attr]


def test_valid_cached_token_avoids_network_authentication() -> None:
    store = InMemoryTokenStore()
    store.save(
        StoredToken(
            access_token="cached-access",
            refresh_token="cached-refresh",
            expires_at=time.time() + 300,
        )
    )
    authenticator, http = _authenticator([], token_store=store)
    platform_session = requests.Session()

    authenticator.authenticate(platform_session)

    assert platform_session.headers["Authorization"] == "Bearer cached-access"
    http.post.assert_not_called()


def test_expired_cached_access_token_refreshes_and_rotates_automatically() -> None:
    store = InMemoryTokenStore()
    store.save(
        StoredToken(
            access_token="expired-access",
            refresh_token="refresh-1",
            expires_at=time.time() - 1,
        )
    )
    authenticator, http = _authenticator(
        [_token_response("access-2", "refresh-2")],
        token_store=store,
    )
    platform_session = requests.Session()

    authenticator.authenticate(platform_session)

    http.post.assert_called_once_with(
        "https://shared.example/realms/federation/protocol/openid-connect/token",
        data={
            "grant_type": "refresh_token",
            "client_id": "kamiwaza-federation",
            "refresh_token": "refresh-1",
        },
        timeout=12.0,
        verify="/tmp/test-ca.pem",
    )
    assert platform_session.headers["Authorization"] == "Bearer access-2"
    assert store.load() is not None
    assert store.load().refresh_token == "refresh-2"  # type: ignore[union-attr]


def test_explicit_refresh_reuses_refresh_token_when_provider_does_not_rotate() -> None:
    store = InMemoryTokenStore()
    store.save(
        StoredToken(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at=time.time() + 300,
        )
    )
    authenticator, _http = _authenticator(
        [_token_response("access-2", None)],
        token_store=store,
    )
    platform_session = requests.Session()

    authenticator.refresh_token(platform_session)

    assert platform_session.headers["Authorization"] == "Bearer access-2"
    assert store.load() is not None
    assert store.load().refresh_token == "refresh-1"  # type: ignore[union-attr]


def test_invalid_refresh_token_performs_one_password_regrant() -> None:
    store = InMemoryTokenStore()
    store.save(
        StoredToken(
            access_token="expired-access",
            refresh_token="revoked-refresh",
            expires_at=time.time() - 1,
        )
    )
    invalid_grant = StubResponse(
        400,
        {"error": "invalid_grant", "error_description": "Session not active"},
    )
    authenticator, http = _authenticator(
        [invalid_grant, _token_response("access-2", "refresh-2")],
        token_store=store,
    )

    authenticator.authenticate(requests.Session())

    assert http.post.call_count == 2
    assert http.post.call_args_list[1].kwargs["data"]["grant_type"] == "password"
    assert store.load() is not None
    assert store.load().access_token == "access-2"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "failure",
    [
        requests.ConnectionError("issuer unavailable"),
        StubResponse(503, {"error": "temporarily_unavailable"}),
        StubResponse(502, invalid_json=True),
    ],
)
def test_refresh_transport_or_server_failure_does_not_password_regrant(
    failure: StubResponse | Exception,
) -> None:
    store = InMemoryTokenStore()
    cached = StoredToken(
        access_token="expired-access",
        refresh_token="refresh-1",
        expires_at=time.time() - 1,
    )
    store.save(cached)
    authenticator, http = _authenticator([failure], token_store=store)

    with pytest.raises(AuthenticationError):
        authenticator.authenticate(requests.Session())

    assert http.post.call_count == 1
    assert store.load() == cached


def test_initial_password_grant_requires_refresh_token() -> None:
    authenticator, _http = _authenticator(
        [_token_response("access-only", None)],
    )

    with pytest.raises(AuthenticationError, match="refresh token"):
        authenticator.authenticate(requests.Session())


def test_malformed_success_response_is_a_typed_authentication_failure() -> None:
    authenticator, _http = _authenticator(
        [StubResponse(200, {"expires_in": 300, "refresh_token": "refresh-1"})],
    )

    with pytest.raises(AuthenticationError, match="invalid token response"):
        authenticator.authenticate(requests.Session())


def test_non_json_success_response_is_a_typed_authentication_failure() -> None:
    authenticator, _http = _authenticator(
        [StubResponse(200, invalid_json=True)],
    )

    with pytest.raises(AuthenticationError, match="invalid JSON"):
        authenticator.authenticate(requests.Session())


def test_invalidation_retains_refresh_and_get_access_token_renews() -> None:
    authenticator, http = _authenticator(
        [
            _token_response("access-1", "refresh-1"),
            _token_response("access-2", "refresh-2"),
        ],
    )
    platform_session = requests.Session()
    authenticator.authenticate(platform_session)

    assert authenticator.invalidate_session(platform_session) is True
    assert "Authorization" not in platform_session.headers
    assert authenticator.get_access_token(platform_session) == "access-2"
    assert http.post.call_args_list[-1].kwargs["data"]["grant_type"] == "refresh_token"


def test_empty_access_token_is_rejected() -> None:
    authenticator, _http = _authenticator(
        [_token_response("", "refresh-1")],
    )

    with pytest.raises(AuthenticationError, match="access token is unavailable"):
        authenticator.authenticate(requests.Session())


def test_token_cache_path_is_scoped_by_issuer_client_and_username(
    tmp_path: Path,
) -> None:
    base = _config()
    identities = [
        base,
        _config(issuer="https://other.example/realms/federation"),
        _config(client_id="other-client"),
        _config(username="fed-clr-u"),
    ]

    paths = {shared_idp_token_path(identity, root=tmp_path) for identity in identities}

    assert len(paths) == 4
    assert all(path.parent == tmp_path for path in paths)


def test_default_scoped_file_store_is_private(tmp_path: Path) -> None:
    config = _config(token_root=tmp_path)
    http = Mock(spec=requests.Session)
    http.post.return_value = _token_response("access-1", "refresh-1")
    authenticator = SharedIdpAuthenticator(
        config,
        http_session=http,
    )

    authenticator.authenticate(requests.Session())

    token_path = shared_idp_token_path(config, root=tmp_path)
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700


def test_client_secret_is_sent_only_to_the_oidc_token_endpoint() -> None:
    config = _config(client_secret="client-secret")
    authenticator, http = _authenticator(
        [_token_response("access-1", "refresh-1")],
        config=config,
    )
    platform_session = requests.Session()

    authenticator.authenticate(platform_session)

    assert http.post.call_args.kwargs["data"]["client_secret"] == "client-secret"
    assert "client_secret" not in platform_session.headers
    assert platform_session.headers["Authorization"] == "Bearer access-1"


def test_configuration_repr_never_discloses_password_or_client_secret() -> None:
    config = _config(client_secret="client-secret")

    rendered = repr(config)

    assert "test-password" not in rendered
    assert "client-secret" not in rendered
