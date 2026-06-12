"""Tests for kamiwaza_extensions_lib.session."""

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kamiwaza_extensions_lib.session import create_session_router


def _make_jwt(exp: int) -> str:
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8")
    ).decode("utf-8").rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp}).encode("utf-8")
    ).decode("utf-8").rstrip("=")
    return f"{header}.{payload}.sig"


def _make_app(monkeypatch, use_auth: str = "true", **env_overrides) -> TestClient:
    """Create a test FastAPI app with the session router."""
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", use_auth)
    monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", env_overrides.get(
        "public_api_url", "https://cluster.test/api"))
    monkeypatch.setenv("KAMIWAZA_APP_URL", env_overrides.get(
        "app_url", "https://cluster.test/runtime/apps/my-app"))

    app = FastAPI()
    app.include_router(create_session_router())
    return TestClient(app)


@pytest.mark.unit
class TestSessionEndpoint:
    def test_authenticated_session(self, monkeypatch):
        client = _make_app(monkeypatch)
        token = _make_jwt(1711900800)
        resp = client.get(
            "/session",
            headers={
                "x-user-id": "usr-123",
                "x-user-email": "alice@example.com",
                "x-user-name": "Alice",
                "x-user-roles": "admin,user",
                "x-workroom-id": "wrk-456",
                "x-auth-token": token,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "usr-123"
        assert data["email"] == "alice@example.com"
        assert data["name"] == "Alice"
        assert data["roles"] == ["admin", "user"]
        assert data["workroom_id"] == "wrk-456"
        assert data["is_authenticated"] is True
        assert data["expires_at"] == 1711900800

    def test_authenticated_session_does_not_leak_sensitive_fields(self, monkeypatch):
        """Guard against regressions: /session MUST NOT expose the Bearer
        credential (``auth_token``), classification flag (``system_high``),
        or correlation tracer (``request_id``) to the browser."""
        client = _make_app(monkeypatch)
        resp = client.get(
            "/session",
            headers={
                "x-user-id": "usr-123",
                "x-user-email": "alice@example.com",
                "x-user-name": "Alice",
                "x-user-roles": "admin",
                "x-workroom-id": "wrk-456",
                "x-user-workroom-role": "editor",
                "x-auth-token": "secret-bearer-jwt",
                "x-user-system-high": "true",
                "x-request-id": "req-abc",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        # Positive: public fields present
        assert data["user_id"] == "usr-123"
        assert data["workroom_role"] == "editor"
        # Negative: private fields absent
        assert "auth_token" not in data
        assert "system_high" not in data
        assert "request_id" not in data

    def test_malformed_envelope_reported_as_logged_out(self, monkeypatch):
        """PR re-review High #1: a request with X-User-Id but no X-Workroom-Id
        triggers MisboundAuthError on require_auth (HTTP 401), so /session must
        also report this as logged-out — otherwise the frontend appears
        authenticated while every API call returns 401 (split-brain)."""
        client = _make_app(monkeypatch, use_auth="true")
        resp = client.get(
            "/session",
            headers={"x-user-id": "usr-123"},  # missing x-workroom-id
        )

        # Endpoint returns 200 (so the SessionProvider doesn't crash) but
        # the body matches the no-envelope/logged-out shape.
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_authenticated"] is False
        assert data["user_id"] is None
        assert data["workroom_id"] is None
        assert data["expires_at"] is None

    def test_whitespace_only_envelope_reported_as_logged_out(self, monkeypatch):
        """Symmetry with require_auth: whitespace-only headers don't satisfy
        the strict envelope check."""
        client = _make_app(monkeypatch, use_auth="true")
        resp = client.get(
            "/session",
            headers={"x-user-id": "usr-123", "x-workroom-id": "   "},
        )

        assert resp.status_code == 200
        assert resp.json()["is_authenticated"] is False

    def test_unauthenticated_session_with_auth_enabled(self, monkeypatch):
        client = _make_app(monkeypatch, use_auth="true")
        resp = client.get("/session")

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_authenticated"] is False
        assert data["user_id"] is None
        assert data["expires_at"] is None

    def test_local_dev_mode_returns_anonymous(self, monkeypatch):
        client = _make_app(monkeypatch, use_auth="false")
        resp = client.get("/session")

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_authenticated"] is False
        assert data["name"] == "Anonymous"
        assert data["expires_at"] is None

    def test_uses_authorization_header_when_auth_token_missing(self, monkeypatch):
        client = _make_app(monkeypatch)
        token = _make_jwt(1711900900)
        resp = client.get(
            "/session",
            headers={
                "x-user-id": "usr-123",
                "x-workroom-id": "wrk-456",  # required for strict envelope check
                "authorization": f"Bearer {token}",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["expires_at"] == 1711900900

    def test_invalid_token_omits_expiry(self, monkeypatch):
        client = _make_app(monkeypatch)
        resp = client.get(
            "/session",
            headers={
                "x-user-id": "usr-123",
                "x-auth-token": "not-a-jwt",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["expires_at"] is None

    def test_anonymous_session_ignores_bearer_expiry(self, monkeypatch):
        client = _make_app(monkeypatch)
        token = _make_jwt(1711901000)
        resp = client.get(
            "/session",
            headers={
                "authorization": f"Bearer {token}",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["is_authenticated"] is False
        assert resp.json()["expires_at"] is None


@pytest.mark.unit
class TestLoginUrlEndpoint:
    def test_returns_login_url_with_encoded_return_to(self, monkeypatch):
        client = _make_app(monkeypatch, use_auth="true")
        resp = client.get("/auth/login-url")

        assert resp.status_code == 200
        data = resp.json()
        # return_to should be URL-encoded
        assert data["login_url"] == (
            "https://cluster.test/api/auth/login"
            "?return_to=https%3A%2F%2Fcluster.test%2Fruntime%2Fapps%2Fmy-app"
        )

    def test_return_to_with_special_chars_encoded(self, monkeypatch):
        """Verify URLs with query params / fragments are safely encoded."""
        client = _make_app(
            monkeypatch,
            use_auth="true",
            app_url="https://cluster.test/apps/my-app?foo=bar&baz=1#section",
        )
        resp = client.get("/auth/login-url")
        data = resp.json()
        # The entire return_to value should be encoded — no raw & or #
        assert "&baz" not in data["login_url"]
        assert "#section" not in data["login_url"]
        assert "return_to=https%3A" in data["login_url"]

    def test_local_dev_returns_null(self, monkeypatch):
        client = _make_app(monkeypatch, use_auth="false")
        resp = client.get("/auth/login-url")

        assert resp.status_code == 200
        assert resp.json()["login_url"] is None


class _FakeCoreResponse:
    """Stands in for httpx.Response from core's POST /api/auth/logout."""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _fake_core_client(calls, core_body):
    """Build a FakeAsyncClient class recording calls and returning core_body."""

    class FakeAsyncClient:
        def __init__(self, *, verify, timeout):
            calls["verify"] = verify
            calls["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            calls["url"] = url
            calls["headers"] = headers or {}
            calls["json"] = json
            return _FakeCoreResponse(core_body)

    return FakeAsyncClient


# The response body core's POST /api/auth/logout actually returns
# (kamiwaza/services/auth/api.py). ``front_channel_logout_url`` is
# root-relative — the session router must absolutize it.
_CORE_LOGOUT_BODY = {
    "message": "Logged out successfully",
    "session_termination_requested": True,
    "front_channel_logout_url": (
        "/api/auth/logout/front-channel?redirect_uri=https%3A%2F%2Fcluster.test%2F"
    ),
    "post_logout_redirect_uri": "https://cluster.test/",
}


@pytest.mark.unit
class TestLogoutEndpoint:
    def test_returns_browser_routable_front_channel_logout_url(self, monkeypatch):
        """ENG-6911 — the logout response MUST carry core's front-channel
        logout URL, resolved to a browser-routable absolute URL. The
        previous contract returned only ``logout_url`` (the POST handler —
        a browser GET there 405s) so the WRM frontend fell through to
        ``/login`` and SSO silently re-authenticated."""
        import httpx

        calls = {}
        monkeypatch.setattr(
            httpx, "AsyncClient", _fake_core_client(calls, _CORE_LOGOUT_BODY)
        )
        client = _make_app(monkeypatch, use_auth="true")
        resp = client.post("/auth/logout")

        assert resp.status_code == 200
        data = resp.json()
        assert data["front_channel_logout_url"] == (
            "https://cluster.test/api/auth/logout/front-channel"
            "?redirect_uri=https%3A%2F%2Fcluster.test%2F"
        )
        assert data["post_logout_redirect_uri"] == "https://cluster.test/"
        # Legacy fields stay for back-compat with existing consumers.
        assert data["logout_url"] == "https://cluster.test/api/auth/logout"
        assert data["redirect_url"] == (
            "https://cluster.test/runtime/apps/my-app/logged-out"
        )

    def test_forwards_post_logout_redirect_uri_to_core(self, monkeypatch):
        """ENG-6911 — the browser's requested post-logout landing URL must
        reach core's POST so core can validate and echo it back."""
        import httpx

        calls = {}
        monkeypatch.setattr(
            httpx, "AsyncClient", _fake_core_client(calls, _CORE_LOGOUT_BODY)
        )
        client = _make_app(monkeypatch, use_auth="true")
        resp = client.post(
            "/auth/logout",
            json={"post_logout_redirect_uri": "https://cluster.test/login"},
        )

        assert resp.status_code == 200
        assert calls["json"] == {
            "post_logout_redirect_uri": "https://cluster.test/login"
        }

    def test_core_unreachable_returns_null_front_channel_fields(self, monkeypatch):
        """When the server-side POST to core fails, the proxied fields are
        null and the client falls back to its own login redirect."""

        class FailingAsyncClient:
            def __init__(self, *, verify, timeout):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, headers=None, json=None):
                raise RuntimeError("core unreachable")

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", FailingAsyncClient)
        client = _make_app(monkeypatch, use_auth="true")
        resp = client.post("/auth/logout")

        assert resp.status_code == 200
        data = resp.json()
        assert data["front_channel_logout_url"] is None
        assert data["post_logout_redirect_uri"] is None
        # Legacy fields still present so the redirect fallback works.
        assert data["logout_url"] == "https://cluster.test/api/auth/logout"

    def test_uses_configured_ssl_verification_for_logout_post(self, monkeypatch):
        import httpx

        calls = {}
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
        monkeypatch.setattr(
            httpx, "AsyncClient", _fake_core_client(calls, _CORE_LOGOUT_BODY)
        )
        client = _make_app(monkeypatch, use_auth="true")

        resp = client.post("/auth/logout", headers={"x-auth-token": "token-123"})

        assert resp.status_code == 200
        assert calls["verify"] is False
        assert calls["timeout"] == 5
        assert calls["url"] == "https://cluster.test/api/auth/logout"
        assert calls["headers"]["x-auth-token"] == "token-123"

    def test_local_dev_returns_null(self, monkeypatch):
        client = _make_app(monkeypatch, use_auth="false")
        resp = client.post("/auth/logout")

        assert resp.status_code == 200
        data = resp.json()
        assert data["logout_url"] is None
        assert data["redirect_url"] is None
        assert data["front_channel_logout_url"] is None
        assert data["post_logout_redirect_uri"] is None

    def test_logout_post_uses_container_url_under_auth_split(self, monkeypatch):
        """PR #87 round-8 review High #4 — under ``kz-ext dev local
        --auth`` the runner sets ``KAMIWAZA_API_URL`` to
        ``host.docker.internal`` (container-routable) and
        ``KAMIWAZA_PUBLIC_API_URL`` to ``localhost`` (browser-routable).
        The internal ``httpx.post(...)`` for server-side session
        termination MUST use the container-routable host or it silently
        fails (caught by the broad ``except Exception``); the response
        body's ``logout_url`` MUST stay browser-routable so the
        client-side redirect resolves.
        """
        calls = {}

        class FakeAsyncClient:
            def __init__(self, *, verify, timeout):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, headers=None, json=None):
                calls["url"] = url
                return _FakeCoreResponse(_CORE_LOGOUT_BODY)

        import httpx

        monkeypatch.setenv("KAMIWAZA_API_URL", "http://host.docker.internal:8000/api")
        monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "http://localhost:8000/api")
        monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
        client = _make_app(
            monkeypatch,
            use_auth="true",
            public_api_url="http://localhost:8000/api",
        )

        resp = client.post("/auth/logout")

        assert resp.status_code == 200
        # Server-side POST hits the container-routable host
        assert calls["url"] == "http://host.docker.internal:8000/api/auth/logout"
        # Browser-facing logout_url stays on the host the browser sees
        data = resp.json()
        assert data["logout_url"] == "http://localhost:8000/api/auth/logout"

    def test_login_url_falls_back_to_api_url_when_public_unset(self, monkeypatch):
        """PR #87 round-8 review High #5 — legacy deployments without
        KAMIWAZA_PUBLIC_API_URL must still produce a usable login URL.
        Without the fallback, ``base`` was the empty string and the
        response carried a malformed ``/auth/login?...`` relative URL.
        """
        client = _make_app(
            monkeypatch,
            use_auth="true",
            public_api_url="",
        )
        # _make_app sets KAMIWAZA_API_URL when public is empty by default,
        # but be explicit:
        monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "")
        monkeypatch.setenv("KAMIWAZA_API_URL", "https://cluster.test/api")
        resp = client.get("/auth/login-url")

        assert resp.status_code == 200
        login_url = resp.json()["login_url"]
        assert login_url.startswith("https://cluster.test/api/auth/login?")


@pytest.mark.unit
class TestSessionRouterWithPrefix:
    def test_prefix_applied(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "false")
        app = FastAPI()
        app.include_router(create_session_router(prefix="/api"))
        client = TestClient(app)

        resp = client.get("/api/session")
        assert resp.status_code == 200

        resp_no_prefix = client.get("/session")
        assert resp_no_prefix.status_code == 404
