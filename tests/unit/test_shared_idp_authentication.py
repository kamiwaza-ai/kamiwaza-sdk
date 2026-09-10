from __future__ import annotations

import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import requests
from filelock import FileLock

import kamiwaza_sdk.shared_idp_authentication as shared_auth
import kamiwaza_sdk.token_store as token_store_module
from kamiwaza_sdk.client import KamiwazaClient
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
        self.headers = {"content-type": "application/json"}
        self.text = ""

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
        allow_redirects=False,
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
        allow_redirects=False,
    )
    assert platform_session.headers["Authorization"] == "Bearer access-2"
    assert store.load() is not None
    assert store.load().refresh_token == "refresh-2"  # type: ignore[union-attr]


def test_shared_file_cache_reloads_refresh_rotation_before_reuse(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "shared-token.json"
    first_store = FileTokenStore(token_path)
    first_store.save(
        StoredToken(
            access_token="expired-access",
            refresh_token="refresh-1",
            expires_at=time.time() - 1,
        )
    )
    first, _first_http = _authenticator(
        [_token_response("access-2", "refresh-2")],
        token_store=first_store,
    )
    second, second_http = _authenticator(
        [],
        token_store=FileTokenStore(token_path),
    )

    first.authenticate(requests.Session())
    second_session = requests.Session()
    second.authenticate(second_session)

    assert second_session.headers["Authorization"] == "Bearer access-2"
    second_http.post.assert_not_called()


def test_shared_file_cache_reloads_access_rotation_when_refresh_is_reused(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "shared-token.json"
    first_store = FileTokenStore(token_path)
    expired_at = time.time() - 1
    first_store.save(
        StoredToken(
            access_token="expired-access",
            refresh_token="refresh-1",
            expires_at=expired_at,
        )
    )
    first, _first_http = _authenticator(
        [_token_response("access-2", None)],
        token_store=first_store,
    )
    second, second_http = _authenticator(
        [],
        token_store=FileTokenStore(token_path),
    )
    assert second._current_token() == StoredToken(
        access_token="expired-access",
        refresh_token="refresh-1",
        expires_at=expired_at,
    )

    first.authenticate(requests.Session())
    second_session = requests.Session()
    second.authenticate(second_session)

    assert second_session.headers["Authorization"] == "Bearer access-2"
    second_http.post.assert_not_called()


def test_invalidated_client_adopts_peer_refresh_before_reusing_old_token(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "shared-token.json"
    first_store = FileTokenStore(token_path)
    first_store.save(
        StoredToken(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at=time.time() + 300,
        )
    )
    first, _first_http = _authenticator(
        [_token_response("access-2", "refresh-2")],
        token_store=first_store,
    )
    second, second_http = _authenticator(
        [AssertionError("stale refresh token was replayed")],
        token_store=FileTokenStore(token_path),
    )
    second_session = requests.Session()

    first.refresh_token(requests.Session())
    second.invalidate_session(second_session)
    second.authenticate(second_session)

    assert second_session.headers["Authorization"] == "Bearer access-2"
    second_http.post.assert_not_called()


def test_concurrent_authentication_issues_only_one_token_grant() -> None:
    store = InMemoryTokenStore()
    store.save(
        StoredToken(
            access_token="expired-access",
            refresh_token="refresh-1",
            expires_at=time.time() - 1,
        )
    )
    entered = threading.Event()
    release = threading.Event()
    call_lock = threading.Lock()
    grant_count = 0
    http = Mock(spec=requests.Session)

    def grant(*_args: Any, **_kwargs: Any) -> StubResponse:
        nonlocal grant_count
        with call_lock:
            grant_count += 1
            call_number = grant_count
        if call_number == 1:
            entered.set()
            assert release.wait(timeout=2)
        return _token_response(f"access-{call_number}", f"refresh-{call_number}")

    http.post.side_effect = grant
    authenticator = SharedIdpAuthenticator(
        _config(), token_store=store, http_session=http
    )
    sessions = [requests.Session(), requests.Session()]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(authenticator.authenticate, sessions[0])
        assert entered.wait(timeout=2)
        second = executor.submit(authenticator.authenticate, sessions[1])
        time.sleep(0.05)
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)

    assert grant_count == 1
    assert {session.headers["Authorization"] for session in sessions} == {
        "Bearer access-1"
    }


def test_file_cache_lock_timeout_is_typed_and_bounded(tmp_path: Path) -> None:
    token_path = tmp_path / "shared-token.json"
    store = FileTokenStore(token_path)
    store.save(
        StoredToken(
            access_token="expired-access",
            refresh_token="refresh-1",
            expires_at=time.time() - 1,
        )
    )
    authenticator, http = _authenticator(
        [_token_response("access-2", "refresh-2")],
        config=_config(timeout_seconds=0.05),
        token_store=store,
    )
    lock_path = token_path.with_suffix(f"{token_path.suffix}.lock")

    held_lock = FileLock(lock_path)
    held_lock.acquire()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(authenticator.authenticate, requests.Session())
        time.sleep(0.2)
        completed_while_held = future.done()
        held_lock.release()
        with pytest.raises(AuthenticationError, match="token cache lock"):
            future.result(timeout=2)

    assert completed_while_held is True
    http.post.assert_not_called()


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
        config=_config(password_regrant_on_invalid_grant=True),
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
        StubResponse(400, {"error": "invalid_client"}),
        StubResponse(503, {"error": "temporarily_unavailable"}),
    ],
)
def test_password_regrant_never_runs_for_non_invalid_grant_failures(
    failure: StubResponse,
) -> None:
    store = InMemoryTokenStore()
    store.save(
        StoredToken(
            access_token="expired-access",
            refresh_token="refresh-1",
            expires_at=time.time() - 1,
        )
    )
    authenticator, http = _authenticator(
        [failure, _token_response("unexpected", "unexpected-refresh")],
        config=_config(password_regrant_on_invalid_grant=True),
        token_store=store,
    )

    with pytest.raises(AuthenticationError):
        authenticator.authenticate(requests.Session())

    assert http.post.call_count == 1


def test_password_regrant_clears_revoked_token_store() -> None:
    store = Mock(wraps=InMemoryTokenStore())
    store.load.return_value = StoredToken(
        access_token="expired-access",
        refresh_token="revoked-refresh",
        expires_at=time.time() - 1,
    )
    invalid_grant = StubResponse(400, {"error": "invalid_grant"})
    authenticator, _http = _authenticator(
        [invalid_grant, _token_response("access-2", "refresh-2")],
        config=_config(password_regrant_on_invalid_grant=True),
        token_store=store,
    )

    authenticator.authenticate(requests.Session())

    store.clear.assert_called_once_with()


def test_invalid_refresh_token_is_not_silently_regranted_by_default() -> None:
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
        [invalid_grant, invalid_grant], token_store=store
    )

    with pytest.raises(AuthenticationError, match="Session not active"):
        authenticator.authenticate(requests.Session())
    with pytest.raises(AuthenticationError, match="Session not active"):
        authenticator.authenticate(requests.Session())

    assert http.post.call_count == 2
    assert store.load() is not None
    assert store.load().refresh_token == "revoked-refresh"  # type: ignore[union-attr]


def test_client_refreshes_and_retries_one_unauthorized_request() -> None:
    authenticator, oidc_http = _authenticator(
        [
            _token_response("access-1", "refresh-1"),
            _token_response("access-2", "refresh-2"),
        ]
    )
    client = KamiwazaClient(
        base_url="https://cluster.example/api",
        authenticator=authenticator,
    )
    platform_responses = iter(
        [
            StubResponse(401, {"detail": "expired"}),
            StubResponse(200, {"ok": True}),
        ]
    )
    authorization_headers: list[str | None] = []

    def request(_method: str, _url: str, **_kwargs: Any) -> StubResponse:
        authorization_headers.append(client.session.headers.get("Authorization"))
        return next(platform_responses)

    client.session.request = request  # type: ignore[method-assign]

    assert client.get("/mesh/resource") == {"ok": True}
    assert authorization_headers == ["Bearer access-1", "Bearer access-2"]
    assert [
        call.kwargs["data"]["grant_type"] for call in oidc_http.post.call_args_list
    ] == [
        "password",
        "refresh_token",
    ]


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


def test_token_grant_refuses_redirects_before_replaying_credentials() -> None:
    authenticator, http = _authenticator(
        [StubResponse(307, {"location": "http://attacker.example/token"})],
    )

    with pytest.raises(AuthenticationError, match="redirect"):
        authenticator.authenticate(requests.Session())

    assert http.post.call_args.kwargs["allow_redirects"] is False


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


def test_get_access_token_returns_before_concurrent_invalidation() -> None:
    store = InMemoryTokenStore()
    store.save(
        StoredToken(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at=time.time() + 300,
        )
    )
    authenticator, _http = _authenticator([], token_store=store)
    authentication_finished = threading.Event()
    release_return = threading.Event()
    invalidation_started = threading.Event()
    invalidation_finished = threading.Event()
    original_authenticate = authenticator.authenticate

    def paused_authenticate(session: requests.Session) -> None:
        original_authenticate(session)
        authentication_finished.set()
        assert release_return.wait(timeout=2)

    authenticator.authenticate = paused_authenticate  # type: ignore[method-assign]
    token_session = requests.Session()
    rejected_session = requests.Session()

    def invalidate() -> None:
        invalidation_started.set()
        authenticator.invalidate_session(rejected_session)
        invalidation_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        read_token = executor.submit(authenticator.get_access_token, token_session)
        assert authentication_finished.wait(timeout=1)
        invalidation = executor.submit(invalidate)
        assert invalidation_started.wait(timeout=1)
        try:
            assert not invalidation_finished.wait(timeout=0.1)
        finally:
            release_return.set()
        assert read_token.result(timeout=1) == "access-1"
        invalidation.result(timeout=1)


def test_repeated_invalidation_preserves_token_identity_for_peer_adoption(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "shared-token.json"
    FileTokenStore(token_path).save(
        StoredToken(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at=time.time() + 300,
        )
    )
    authenticator, http = _authenticator(
        [AssertionError("stale refresh token was replayed")],
        token_store=FileTokenStore(token_path),
    )
    platform_session = requests.Session()

    authenticator.invalidate_session(platform_session)
    FileTokenStore(token_path).save(
        StoredToken(
            access_token="access-2",
            refresh_token="refresh-2",
            expires_at=time.time() + 300,
        )
    )
    authenticator.invalidate_session(platform_session)

    assert authenticator.get_access_token(platform_session) == "access-2"
    assert platform_session.headers["Authorization"] == "Bearer access-2"
    http.post.assert_not_called()


def test_access_token_inside_refresh_leeway_is_refreshed(monkeypatch) -> None:
    now = 1_000.0
    monkeypatch.setattr(shared_auth.time, "time", lambda: now)
    store = InMemoryTokenStore()
    store.save(
        StoredToken(
            access_token="almost-expired",
            refresh_token="refresh-1",
            expires_at=now + 20,
        )
    )
    authenticator, http = _authenticator(
        [_token_response("access-2", "refresh-2")], token_store=store
    )

    authenticator.authenticate(requests.Session())

    http.post.assert_called_once()


def test_token_expiry_uses_provider_expires_in(monkeypatch) -> None:
    now = 1_000.0
    monkeypatch.setattr(shared_auth.time, "time", lambda: now)
    store = InMemoryTokenStore()
    authenticator, _http = _authenticator(
        [_token_response("access-1", "refresh-1", expires_in=90)],
        token_store=store,
    )

    authenticator.authenticate(requests.Session())

    assert store.load() is not None
    assert store.load().expires_at == now + 90  # type: ignore[union-attr]


def test_empty_access_token_is_rejected() -> None:
    store = InMemoryTokenStore()
    authenticator, _http = _authenticator(
        [_token_response("", "refresh-1")],
        token_store=store,
    )

    with pytest.raises(AuthenticationError, match="access token is unavailable"):
        authenticator.authenticate(requests.Session())

    assert store.load() is None


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


def test_token_cache_path_is_scoped_by_normalized_oauth_scope(tmp_path: Path) -> None:
    openid = shared_idp_token_path(_config(scope="openid"), root=tmp_path)
    offline = shared_idp_token_path(
        _config(scope="openid offline_access"), root=tmp_path
    )
    reordered = shared_idp_token_path(
        _config(scope="offline_access   openid"), root=tmp_path
    )

    assert openid != offline
    assert offline == reordered


def test_plain_http_issuer_is_rejected_without_explicit_dev_override() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        _config(issuer="http://shared.example/realms/federation")


def test_plain_http_issuer_requires_explicit_dev_override() -> None:
    config = _config(
        issuer="http://shared.example/realms/federation",
        allow_insecure_http=True,
    )

    assert config.normalized_issuer == "http://shared.example/realms/federation"


def test_https_verification_cannot_be_disabled_implicitly() -> None:
    with pytest.raises(ValueError, match="allow_insecure_tls"):
        _config(verify=False)


def test_https_verification_requires_explicit_dev_override() -> None:
    config = _config(verify=False, allow_insecure_tls=True)

    assert config.verify is False


def test_new_custom_scoped_file_store_is_private(tmp_path: Path) -> None:
    token_root = tmp_path / "new-cache"
    config = _config(token_root=token_root)
    http = Mock(spec=requests.Session)
    http.post.return_value = _token_response("access-1", "refresh-1")
    authenticator = SharedIdpAuthenticator(
        config,
        http_session=http,
    )

    authenticator.authenticate(requests.Session())

    token_path = shared_idp_token_path(config, root=token_root)
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700


def test_token_file_is_created_private_before_any_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed_modes: list[int] = []
    real_open = token_store_module.os.open

    def recording_open(path, flags, mode=0o777):
        observed_modes.append(mode)
        return real_open(path, flags, mode)

    monkeypatch.setattr(token_store_module.os, "open", recording_open)
    FileTokenStore(tmp_path / "token.json").save(
        StoredToken("access", "refresh", time.time() + 300)
    )

    assert observed_modes == [0o600]


def test_existing_custom_token_root_permissions_are_not_changed(tmp_path: Path) -> None:
    token_root = tmp_path / "caller-managed-cache"
    token_root.mkdir(mode=0o755)
    token_root.chmod(0o755)

    SharedIdpAuthenticator(
        _config(token_root=token_root),
        http_session=Mock(spec=requests.Session),
    )

    assert stat.S_IMODE(token_root.stat().st_mode) == 0o755


def test_client_close_releases_explicitly_owned_oidc_authenticator() -> None:
    authenticator = SharedIdpAuthenticator(
        _config(),
        token_store=InMemoryTokenStore(),
    )
    close_oidc = Mock()
    authenticator._http.close = close_oidc
    client = KamiwazaClient(
        base_url="https://cluster.example/api",
        authenticator=authenticator,
        owns_authenticator=True,
    )

    client.close()

    close_oidc.assert_called_once_with()


def test_client_close_preserves_caller_owned_authenticator() -> None:
    authenticator = Mock()
    client = KamiwazaClient(
        base_url="https://cluster.example/api",
        authenticator=authenticator,
    )

    client.close()

    authenticator.close.assert_not_called()


def test_client_close_preserves_caller_owned_oidc_session() -> None:
    oidc_http = Mock(spec=requests.Session)
    authenticator = SharedIdpAuthenticator(
        _config(),
        token_store=InMemoryTokenStore(),
        http_session=oidc_http,
    )
    client = KamiwazaClient(
        base_url="https://cluster.example/api",
        authenticator=authenticator,
    )

    client.close()

    oidc_http.close.assert_not_called()


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
