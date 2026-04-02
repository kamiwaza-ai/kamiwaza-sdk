"""Tests for kamiwaza_extensions_lib.auth."""

import pytest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from kamiwaza_extensions_lib.auth import forward_auth_headers, require_auth, require_role
from kamiwaza_extensions_lib.identity import Identity


@pytest.mark.unit
class TestForwardAuthHeaders:
    def test_extracts_auth_headers(self):
        headers = {
            "Authorization": "Bearer token123",
            "X-Auth-Token": "jwt-abc",
            "X-User-Id": "usr-123",
            "X-User-Email": "alice@example.com",
            "X-User-Name": "Alice",
            "X-User-Roles": "admin,user",
            "X-Workroom-Id": "wrk-456",
            "X-Request-Id": "req-789",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        result = forward_auth_headers(headers)

        assert result == {
            "Authorization": "Bearer token123",
            "X-Auth-Token": "jwt-abc",
            "X-User-Id": "usr-123",
            "X-User-Email": "alice@example.com",
            "X-User-Name": "Alice",
            "X-User-Roles": "admin,user",
            "X-Workroom-Id": "wrk-456",
            "X-Request-Id": "req-789",
        }

    def test_returns_empty_when_no_auth_headers(self):
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/html",
        }
        result = forward_auth_headers(headers)
        assert result == {}

    def test_returns_empty_for_empty_input(self):
        assert forward_auth_headers({}) == {}

    def test_partial_auth_headers(self):
        headers = {
            "X-User-Id": "usr-123",
            "Content-Type": "application/json",
        }
        result = forward_auth_headers(headers)
        assert result == {"X-User-Id": "usr-123"}

    def test_case_insensitive_matching(self):
        headers = {
            "x-user-id": "usr-123",
            "x-auth-token": "jwt-abc",
        }
        result = forward_auth_headers(headers)
        assert "x-user-id" in result
        assert "x-auth-token" in result


@pytest.mark.unit
class TestRequireAuth:
    @pytest.mark.asyncio
    async def test_authenticated_request(self):
        request = MagicMock()
        request.headers = {"x-user-id": "usr-123", "x-user-email": "a@b.com"}

        identity = await require_auth(request)

        assert identity.user_id == "usr-123"
        assert identity.is_authenticated is True

    @pytest.mark.asyncio
    async def test_unauthenticated_request_raises_401(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        request = MagicMock()
        request.headers = {}

        with pytest.raises(HTTPException) as exc_info:
            await require_auth(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_local_dev_mode_allows_unauthenticated(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "false")
        request = MagicMock()
        request.headers = {}

        identity = await require_auth(request)

        assert identity.is_authenticated is False
        # Should not raise — local dev mode


@pytest.mark.unit
class TestRequireRole:
    @pytest.mark.asyncio
    async def test_user_has_role(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        request = MagicMock()
        request.headers = {
            "x-user-id": "usr-123",
            "x-user-roles": "admin,user",
        }

        dep = require_role("admin")
        # Simulate FastAPI dependency injection
        identity = await require_auth(request)
        result = await dep(identity=identity)

        assert result.user_id == "usr-123"

    @pytest.mark.asyncio
    async def test_user_lacks_role_raises_403(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        identity = Identity(
            user_id="usr-123",
            roles=["user"],
            is_authenticated=True,
        )

        dep = require_role("admin")
        with pytest.raises(HTTPException) as exc_info:
            await dep(identity=identity)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_role_check_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        identity = Identity(
            user_id="usr-123",
            roles=["Admin"],
            is_authenticated=True,
        )

        dep = require_role("admin")
        result = await dep(identity=identity)
        assert result.user_id == "usr-123"
