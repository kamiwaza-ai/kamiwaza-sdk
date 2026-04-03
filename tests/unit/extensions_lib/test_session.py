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


@pytest.mark.unit
class TestLogoutEndpoint:
    def test_returns_logout_urls(self, monkeypatch):
        client = _make_app(monkeypatch, use_auth="true")
        resp = client.post("/auth/logout")

        assert resp.status_code == 200
        data = resp.json()
        assert data["logout_url"] == "https://cluster.test/api/auth/logout"
        assert data["redirect_url"] == (
            "https://cluster.test/runtime/apps/my-app/logged-out"
        )

    def test_local_dev_returns_null(self, monkeypatch):
        client = _make_app(monkeypatch, use_auth="false")
        resp = client.post("/auth/logout")

        assert resp.status_code == 200
        assert resp.json()["logout_url"] is None
        assert resp.json()["redirect_url"] is None


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
