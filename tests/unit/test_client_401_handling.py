"""Tests for the HTTP 401 handling path in :class:`KamiwazaClient`.

These cover the behavior that PATs (``ApiKeyAuthenticator``) cannot be
refreshed client-side and must not trigger a pointless retry, and that the
resulting error surfaces the server's actual response instead of the old
"after token refresh" message.

See PR following ENG-29xx (apps session heartbeat 401 cleanup).
"""

from __future__ import annotations

import pytest
import requests

from kamiwaza_sdk.authentication import (
    ApiKeyAuthenticator,
    Authenticator,
    OAuthAuthenticator,
)
from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.exceptions import AuthenticationError


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


def _client_with_responses(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[_StubResponse],
    authenticator: Authenticator | None = None,
) -> tuple[KamiwazaClient, list[dict]]:
    client = KamiwazaClient(base_url="https://example.test/api")
    if authenticator is not None:
        client.authenticator = authenticator
    calls: list[dict] = []
    iterator = iter(responses)

    def _fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        try:
            return next(iterator)
        except StopIteration:  # pragma: no cover - test setup bug
            raise AssertionError("Too many requests made by client")

    monkeypatch.setattr(client.session, "request", _fake_request)
    return client, calls


def test_api_key_authenticator_cannot_refresh() -> None:
    """PAT-backed auth must declare it cannot refresh."""
    auth = ApiKeyAuthenticator("pat-token")
    assert auth.can_refresh() is False


def test_401_with_pat_does_not_retry_and_surfaces_server_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401 under PAT auth raises immediately with the server's response text.

    This is the core fix for the misleading "Authentication failed after
    token refresh" message. Because ``ApiKeyAuthenticator.refresh_token`` is
    a no-op, retrying is pointless and the old error text was a lie.
    """
    body = '{"detail":"Invalid session token"}'
    response = _StubResponse(status_code=401, text=body, json_data={"detail": "Invalid session token"})
    client, calls = _client_with_responses(
        monkeypatch, [response], authenticator=ApiKeyAuthenticator("pat-token")
    )

    with pytest.raises(AuthenticationError) as exc_info:
        client.post("/apps/sessions/heartbeat", headers={"X-App-Session-Token": "bogus"})

    message = str(exc_info.value)
    assert "Invalid session token" in message  # server body preserved
    assert "/apps/sessions/heartbeat" in message  # endpoint included
    assert "after token refresh" not in message  # regression guard
    # Exactly one request — no retry.
    assert len(calls) == 1


def test_401_with_refreshable_auth_retries_once_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refreshable authenticators retain the refresh-then-retry behavior.

    Also asserts the post-refresh error message carries the endpoint and
    server response body so users can debug why the refresh failed.
    """

    class _RefreshableAuth(Authenticator):
        def __init__(self) -> None:
            self.refresh_calls = 0

        def authenticate(self, session: requests.Session) -> None:
            session.headers.update({"Authorization": "Bearer stale"})

        def refresh_token(self, session: requests.Session) -> None:
            self.refresh_calls += 1
            session.headers.update({"Authorization": "Bearer fresh"})

    body = '{"detail":"token expired"}'
    response_first = _StubResponse(status_code=401, text=body, json_data={"detail": "token expired"})
    response_retry = _StubResponse(status_code=401, text=body, json_data={"detail": "token expired"})
    auth = _RefreshableAuth()
    client, calls = _client_with_responses(
        monkeypatch, [response_first, response_retry], authenticator=auth
    )

    with pytest.raises(AuthenticationError) as exc_info:
        client.get("/models")

    message = str(exc_info.value)
    assert "after token refresh" in message
    assert "/models" in message  # endpoint surfaces in the error
    assert "token expired" in message  # server body preserved
    assert auth.refresh_calls == 1
    assert len(calls) == 2


def test_401_legacy_authenticator_without_can_refresh_still_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy subclasses that pre-date ``can_refresh`` must not break.

    The client uses ``getattr(authenticator, "can_refresh", True)`` with a
    ``callable()`` check so legacy authenticators (no hook at all, or hook
    exposed as a plain attribute/property) continue to receive the
    refresh-then-retry behavior they always had.
    """

    class _LegacyAuth:
        """Duck-typed authenticator predating ``can_refresh``."""

        def __init__(self) -> None:
            self.refresh_calls = 0

        def authenticate(self, session: requests.Session) -> None:
            session.headers.update({"Authorization": "Bearer legacy"})

        def refresh_token(self, session: requests.Session) -> None:
            self.refresh_calls += 1

    body = '{"detail":"legacy 401"}'
    responses = [
        _StubResponse(status_code=401, text=body, json_data={"detail": "legacy 401"}),
        _StubResponse(status_code=401, text=body, json_data={"detail": "legacy 401"}),
    ]
    auth = _LegacyAuth()
    # _client_with_responses only accepts Authenticator typed param, but
    # assigning after construction via the attribute works the same way.
    client, calls = _client_with_responses(monkeypatch, responses)
    client.authenticator = auth  # type: ignore[assignment]

    with pytest.raises(AuthenticationError):
        client.get("/legacy")

    # Retried exactly once (refresh + replay) before raising.
    assert auth.refresh_calls == 1
    assert len(calls) == 2


def test_401_authenticator_with_can_refresh_as_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``can_refresh`` may be exposed as a bool attribute, not just a method."""

    class _AttrAuth:
        can_refresh = False

        def authenticate(self, session: requests.Session) -> None:
            session.headers.update({"Authorization": "Bearer attr"})

        def refresh_token(self, session: requests.Session) -> None:
            raise AssertionError("refresh_token must not be called when can_refresh is False")

    body = '{"detail":"attr 401"}'
    response = _StubResponse(status_code=401, text=body, json_data={"detail": "attr 401"})
    client, calls = _client_with_responses(monkeypatch, [response])
    client.authenticator = _AttrAuth()  # type: ignore[assignment]

    with pytest.raises(AuthenticationError) as exc_info:
        client.get("/attr-endpoint")

    message = str(exc_info.value)
    assert "attr 401" in message
    assert "/attr-endpoint" in message
    assert "after token refresh" not in message
    assert len(calls) == 1


def test_401_no_authenticator_raises_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without any authenticator, 401 raises the existing specific error."""
    # Clear env-sourced API keys so the client picks up no authenticator.
    monkeypatch.delenv("KAMIWAZA_API_KEY", raising=False)
    monkeypatch.delenv("KAMIWAZA_API_TOKEN", raising=False)
    response = _StubResponse(status_code=401, text='{"detail":"unauth"}')
    client, calls = _client_with_responses(monkeypatch, [response], authenticator=None)
    # Defensive: ensure no authenticator was injected from env.
    client.authenticator = None

    with pytest.raises(AuthenticationError) as exc_info:
        client.get("/whoami")

    assert "No authenticator provided" in str(exc_info.value)
    assert len(calls) == 1


def test_401_detail_surfaces_json_detail_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON ``{"detail": "..."}`` bodies surface just the detail string."""
    detail = "Invalid session token"
    # Raw text also contains JSON wrapping, but the raised message should be
    # the unwrapped detail (no braces, no ``detail`` key).
    response = _StubResponse(
        status_code=401,
        text=f'{{"detail":"{detail}"}}',
        json_data={"detail": detail},
    )
    client, _ = _client_with_responses(
        monkeypatch, [response], authenticator=ApiKeyAuthenticator("pat-token")
    )

    with pytest.raises(AuthenticationError) as exc_info:
        client.get("/apps/sessions/heartbeat")

    message = str(exc_info.value)
    assert detail in message
    # JSON scaffolding should be stripped — we surface only the detail value.
    assert '"detail"' not in message


def test_401_detail_truncates_non_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-JSON bodies (e.g. proxy HTML pages) are truncated to <=500 chars.

    Uses a distinctive filler character (``Z``) that cannot appear in the
    constant error-message prefix so the count assertion cleanly measures
    the embedded body's contribution.
    """
    huge_body = "Z" * 5000
    response = _StubResponse(
        status_code=401,
        text=huge_body,
        content_type="text/html",
        # json_data=None -> response.json() raises ValueError
    )
    client, _ = _client_with_responses(
        monkeypatch, [response], authenticator=ApiKeyAuthenticator("pat-token")
    )

    with pytest.raises(AuthenticationError) as exc_info:
        client.get("/gateway-guarded")

    message = str(exc_info.value)
    # The body contribution must be capped at the max length. No ``Z`` appears
    # in the constant prefix, so message.count("Z") is exactly the truncated
    # body length.
    assert message.count("Z") == 500
    assert "Z" * 500 in message  # present up to the cap


def test_oauth_authenticator_cannot_refresh() -> None:
    """OAuth refresh is a placeholder; ``can_refresh`` must return False.

    Otherwise a 401 would trigger ``refresh_token`` and raise
    ``NotImplementedError`` instead of a clean ``AuthenticationError``
    carrying the server's actual response.
    """
    auth = OAuthAuthenticator(
        client_id="cid", client_secret="csecret", auth_service=None
    )
    assert auth.can_refresh() is False
