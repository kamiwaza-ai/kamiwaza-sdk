"""Tests for kamiwaza_extensions_lib.session."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kamiwaza_extensions_lib.session import create_session_router


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
        resp = client.get(
            "/session",
            headers={
                "x-user-id": "usr-123",
                "x-user-email": "alice@example.com",
                "x-user-name": "Alice",
                "x-user-roles": "admin,user",
                "x-workroom-id": "wrk-456",
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

    def test_unauthenticated_session_with_auth_enabled(self, monkeypatch):
        client = _make_app(monkeypatch, use_auth="true")
        resp = client.get("/session")

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_authenticated"] is False
        assert data["user_id"] is None

    def test_local_dev_mode_returns_anonymous(self, monkeypatch):
        client = _make_app(monkeypatch, use_auth="false")
        resp = client.get("/session")

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_authenticated"] is False
        assert data["name"] == "Anonymous"


@pytest.mark.unit
class TestLoginUrlEndpoint:
    def test_returns_login_url(self, monkeypatch):
        client = _make_app(monkeypatch, use_auth="true")
        resp = client.get("/auth/login-url")

        assert resp.status_code == 200
        data = resp.json()
        assert data["login_url"] == (
            "https://cluster.test/api/auth/login"
            "?return_to=https://cluster.test/runtime/apps/my-app"
        )

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
