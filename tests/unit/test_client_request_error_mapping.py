from __future__ import annotations

import pytest

from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, VectorDBUnavailableError


pytestmark = pytest.mark.unit


class _StubResponse:
    def __init__(
        self,
        *,
        status_code: int,
        text: str = "",
        content_type: str = "application/json",
        json_data: object | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}
        self._json_data = json_data

    def json(self) -> object:
        if self._json_data is None:
            raise ValueError("No JSON payload")
        return self._json_data


def _make_client_with_response(
    monkeypatch: pytest.MonkeyPatch,
    response: _StubResponse,
) -> KamiwazaClient:
    client = KamiwazaClient(base_url="https://example.test/api")
    monkeypatch.setattr(client.session, "request", lambda *_args, **_kwargs: response)
    return client


def test_501_vectordb_path_maps_to_vectordb_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _StubResponse(
        status_code=501,
        text='{"detail":"no vector backend configured"}',
        json_data={"detail": "no vector backend configured"},
    )
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(VectorDBUnavailableError) as exc_info:
        client.get("/vectordb/collections")

    assert exc_info.value.status_code == 501
    assert exc_info.value.response_data == {"detail": "no vector backend configured"}


def test_501_context_vectordbs_path_maps_to_vectordb_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _StubResponse(
        status_code=501,
        text='{"detail":"no vector backend configured"}',
        json_data={"detail": "no vector backend configured"},
    )
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(VectorDBUnavailableError) as exc_info:
        client.get("/context/vectordbs")

    assert exc_info.value.status_code == 501
    assert exc_info.value.response_data == {"detail": "no vector backend configured"}


def test_501_non_vectordb_path_raises_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _StubResponse(
        status_code=501,
        text='{"detail":"feature not implemented"}',
        json_data={"detail": "feature not implemented"},
    )
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(APIError) as exc_info:
        client.get("/context/ontologies")

    assert not isinstance(exc_info.value, VectorDBUnavailableError)
    assert exc_info.value.status_code == 501


# ========== absolute_url same-origin guard ==========


def test_absolute_url_rejects_different_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """absolute_url that targets a different host must be rejected to
    prevent credential leakage via SSRF."""
    response = _StubResponse(status_code=200, json_data={})
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(ValueError, match="same origin"):
        client._request(
            "GET", "/endpoint",
            absolute_url="https://evil.example.com/steal",
        )


def test_absolute_url_rejects_scheme_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """absolute_url with http:// against an https:// base_url must be
    rejected to prevent transport-security downgrade."""
    response = _StubResponse(status_code=200, json_data={})
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(ValueError, match="same origin"):
        client._request(
            "GET", "/endpoint",
            absolute_url="http://example.test/callback",
        )


def test_absolute_url_accepts_same_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """absolute_url targeting the same host as base_url should succeed."""
    response = _StubResponse(status_code=200, json_data={"ok": True})
    client = _make_client_with_response(monkeypatch, response)

    result = client._request(
        "GET", "/endpoint",
        absolute_url="https://example.test/oauth-broker/callback",
    )
    assert result == {"ok": True}


# ========== skip_auth strips Authorization header ==========


def test_skip_auth_strips_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """When skip_auth=True, the session-level Authorization header must
    not be sent — otherwise stale credentials leak to public endpoints."""
    captured_kwargs: dict = {}

    def _capture_request(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return _StubResponse(status_code=200, json_data={"ok": True})

    client = KamiwazaClient(base_url="https://example.test/api")
    client.session.headers["Authorization"] = "Bearer stale-token"
    monkeypatch.setattr(client.session, "request", _capture_request)

    client._request("GET", "/public-endpoint", skip_auth=True)

    # The per-request headers must set Authorization to None so that
    # requests.Session removes it from the merged result.
    assert captured_kwargs["headers"]["Authorization"] is None


def test_skip_auth_with_none_headers_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing headers=None with skip_auth=True must not raise TypeError."""
    captured_kwargs: dict = {}

    def _capture_request(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return _StubResponse(status_code=200, json_data={"ok": True})

    client = KamiwazaClient(base_url="https://example.test/api")
    monkeypatch.setattr(client.session, "request", _capture_request)

    client._request("GET", "/public-endpoint", skip_auth=True, headers=None)

    assert captured_kwargs["headers"]["Authorization"] is None


def test_skip_auth_does_not_send_session_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    """When skip_auth=True, the access_token session cookie (set by
    UserPasswordAuthenticator) must be removed so it is not sent to
    public endpoints.  Other cookies (LB affinity, CSRF) are preserved.
    """

    def _capture_request(*args, **kwargs):
        return _StubResponse(status_code=200, json_data={"ok": True})

    client = KamiwazaClient(base_url="https://example.test/api")
    client.session.cookies.set("access_token", "stale-bearer")
    client.session.cookies.set("lb_affinity", "node-3")
    monkeypatch.setattr(client.session, "request", _capture_request)

    client._request("GET", "/public/callback", skip_auth=True)

    assert "access_token" not in client.session.cookies
    assert client.session.cookies.get("lb_affinity") == "node-3"


def test_skip_auth_handles_duplicate_access_token_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    """If multiple access_token cookies exist at different paths,
    RequestsCookieJar.pop() raises CookieConflictError.  _apply_skip_auth
    must handle this gracefully and clear all of them."""

    def _capture_request(*args, **kwargs):
        return _StubResponse(status_code=200, json_data={"ok": True})

    client = KamiwazaClient(base_url="https://example.test/api")
    client.session.cookies.set("access_token", "root-scoped", path="/")
    client.session.cookies.set("access_token", "api-scoped", path="/api")
    client.session.cookies.set("lb_affinity", "node-3")
    monkeypatch.setattr(client.session, "request", _capture_request)

    client._request("GET", "/public/callback", skip_auth=True)

    assert all(c.name != "access_token" for c in client.session.cookies)
    assert client.session.cookies.get("lb_affinity") == "node-3"


def test_debug_log_masks_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authorization header must be masked in debug logs to prevent
    token leakage into CI logs and support bundles."""
    import logging

    response = _StubResponse(status_code=200, json_data={"ok": True})
    client = _make_client_with_response(monkeypatch, response)
    client.session.headers["Authorization"] = "Bearer secret-token-value"

    log_messages: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: log_messages.append(record.getMessage())  # type: ignore[assignment]
    client.logger.addHandler(handler)
    client.logger.setLevel(logging.DEBUG)

    try:
        client._request("GET", "/endpoint")
    finally:
        client.logger.removeHandler(handler)

    header_logs = [m for m in log_messages if "Request headers" in m]
    assert header_logs, "Expected at least one 'Request headers' log"
    for msg in header_logs:
        assert "secret-token-value" not in msg
        assert "***" in msg


# ========== 401 → refresh → retry success path ==========


def test_401_triggers_refresh_then_retries_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 on the first attempt must trigger authenticator.refresh_token
    and then retry the request.  On a 200 retry the result is returned."""
    call_count = 0

    def _mock_request(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _StubResponse(status_code=401, text="Unauthorized")
        return _StubResponse(status_code=200, json_data={"retried": True})

    refresh_calls: list[object] = []

    class _StubAuthenticator:
        def authenticate(self, session):
            session.headers["Authorization"] = "Bearer fresh-token"

        def refresh_token(self, session):
            refresh_calls.append(session)
            session.headers["Authorization"] = "Bearer refreshed-token"

    client = KamiwazaClient(base_url="https://example.test/api")
    client.authenticator = _StubAuthenticator()  # type: ignore[assignment]
    monkeypatch.setattr(client.session, "request", _mock_request)

    result = client._request("GET", "/protected")

    assert result == {"retried": True}
    assert call_count == 2
    assert len(refresh_calls) == 1


def test_401_after_refresh_raises_authentication_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the retry after refresh also returns 401, an AuthenticationError
    must be raised rather than retrying infinitely."""
    from kamiwaza_sdk.exceptions import AuthenticationError

    def _always_401(*args, **kwargs):
        return _StubResponse(status_code=401, text="Unauthorized")

    class _StubAuthenticator:
        def authenticate(self, session):
            pass

        def refresh_token(self, session):
            pass

    client = KamiwazaClient(base_url="https://example.test/api")
    client.authenticator = _StubAuthenticator()  # type: ignore[assignment]
    monkeypatch.setattr(client.session, "request", _always_401)

    with pytest.raises(AuthenticationError, match="after token refresh"):
        client._request("GET", "/protected")


def test_skip_auth_401_does_not_invoke_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """When skip_auth=True a 401 must raise immediately without calling
    refresh_token — it's a public endpoint that doesn't use credentials."""
    from kamiwaza_sdk.exceptions import AuthenticationError

    def _always_401(*args, **kwargs):
        return _StubResponse(status_code=401, text="Unauthorized")

    refresh_called = False

    class _StubAuthenticator:
        def authenticate(self, session):
            pass

        def refresh_token(self, session):
            nonlocal refresh_called
            refresh_called = True

    client = KamiwazaClient(base_url="https://example.test/api")
    client.authenticator = _StubAuthenticator()  # type: ignore[assignment]
    monkeypatch.setattr(client.session, "request", _always_401)

    with pytest.raises(AuthenticationError, match="Unauthenticated"):
        client._request("GET", "/public-endpoint", skip_auth=True)

    assert not refresh_called


def test_error_log_redacts_bearer_token_in_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a 4xx/5xx response body echoes a bearer token, it must be
    redacted in log output and exception messages to prevent token
    leakage into CI logs and support bundles."""
    import logging

    token_body = 'Error: invalid Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.secret for scope'
    response = _StubResponse(
        status_code=403,
        text=token_body,
        json_data=None,
        content_type="text/plain",
    )
    client = _make_client_with_response(monkeypatch, response)

    log_messages: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: log_messages.append(record.getMessage())  # type: ignore[assignment]
    client.logger.addHandler(handler)
    client.logger.setLevel(logging.DEBUG)

    try:
        with pytest.raises(APIError):
            client._request("GET", "/protected")
    finally:
        client.logger.removeHandler(handler)

    error_logs = [m for m in log_messages if "Request failed" in m]
    assert error_logs, "Expected 'Request failed' log"
    for msg in error_logs:
        assert "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9" not in msg
        assert "Bearer ***" in msg


def test_401_redacts_bearer_token_in_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 response that echoes a bearer token must have it redacted
    in the AuthenticationError message."""
    from kamiwaza_sdk.exceptions import AuthenticationError

    token_body = "Rejected Bearer eyJsZWFrZWQ.token for this endpoint"
    response = _StubResponse(status_code=401, text=token_body)
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(AuthenticationError) as exc_info:
        client._request("GET", "/public-endpoint", skip_auth=True)

    assert "eyJsZWFrZWQ" not in str(exc_info.value)
    assert "Bearer ***" in str(exc_info.value)


def test_request_does_not_mutate_caller_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """_request must not modify the caller-provided headers dict."""
    response = _StubResponse(status_code=200, json_data={"ok": True})
    client = _make_client_with_response(monkeypatch, response)

    caller_headers = {"X-Custom": "value"}
    client._request("GET", "/endpoint", skip_auth=True, headers=caller_headers)

    assert "Authorization" not in caller_headers
