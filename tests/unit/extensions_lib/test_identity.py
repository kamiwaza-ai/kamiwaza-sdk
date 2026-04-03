"""Tests for kamiwaza_extensions_lib.identity."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from kamiwaza_extensions_lib.identity import Identity, get_identity, identity_from_headers


class TestIdentityFromHeaders:
    def test_full_headers(self):
        headers = {
            "x-user-id": "usr-123",
            "x-user-email": "alice@example.com",
            "x-user-name": "Alice",
            "x-user-roles": "admin,user",
            "x-workroom-id": "wrk-456",
            "x-request-id": "req-789",
        }
        identity = identity_from_headers(headers)

        assert identity.user_id == "usr-123"
        assert identity.email == "alice@example.com"
        assert identity.name == "Alice"
        assert identity.roles == ["admin", "user"]
        assert identity.workroom_id == "wrk-456"
        assert identity.request_id == "req-789"
        assert identity.is_authenticated is True

    def test_missing_headers_returns_unauthenticated(self):
        identity = identity_from_headers({})

        assert identity.user_id is None
        assert identity.email is None
        assert identity.name is None
        assert identity.roles == []
        assert identity.workroom_id is None
        assert identity.is_authenticated is False

    def test_partial_headers(self):
        headers = {
            "x-user-id": "usr-123",
            "x-user-email": "alice@example.com",
        }
        identity = identity_from_headers(headers)

        assert identity.user_id == "usr-123"
        assert identity.email == "alice@example.com"
        assert identity.name is None
        assert identity.roles == []
        assert identity.workroom_id is None
        assert identity.is_authenticated is True

    def test_case_insensitive_headers(self):
        headers = {
            "X-User-Id": "usr-123",
            "X-USER-EMAIL": "alice@example.com",
            "X-User-Name": "Alice",
        }
        identity = identity_from_headers(headers)

        assert identity.user_id == "usr-123"
        assert identity.email == "alice@example.com"
        assert identity.name == "Alice"
        assert identity.is_authenticated is True

    def test_malformed_roles_with_extra_commas(self):
        headers = {
            "x-user-id": "usr-123",
            "x-user-roles": "admin,,user,,,viewer,",
        }
        identity = identity_from_headers(headers)

        assert identity.roles == ["admin", "user", "viewer"]

    def test_empty_roles_string(self):
        headers = {
            "x-user-id": "usr-123",
            "x-user-roles": "",
        }
        identity = identity_from_headers(headers)

        assert identity.roles == []

    def test_single_role(self):
        headers = {
            "x-user-id": "usr-123",
            "x-user-roles": "admin",
        }
        identity = identity_from_headers(headers)

        assert identity.roles == ["admin"]

    def test_roles_with_whitespace(self):
        headers = {
            "x-user-id": "usr-123",
            "x-user-roles": " admin , user , viewer ",
        }
        identity = identity_from_headers(headers)

        assert identity.roles == ["admin", "user", "viewer"]

    def test_empty_user_id_is_unauthenticated(self):
        headers = {"x-user-id": ""}
        identity = identity_from_headers(headers)

        assert identity.user_id is None
        assert identity.is_authenticated is False

    def test_workroom_id_without_user_id(self):
        headers = {"x-workroom-id": "wrk-456"}
        identity = identity_from_headers(headers)

        assert identity.workroom_id == "wrk-456"
        assert identity.is_authenticated is False

    def test_extra_headers_ignored(self):
        headers = {
            "x-user-id": "usr-123",
            "content-type": "application/json",
            "x-custom-header": "value",
        }
        identity = identity_from_headers(headers)

        assert identity.user_id == "usr-123"
        assert identity.is_authenticated is True


@pytest.mark.unit
class TestGetIdentity:
    @pytest.mark.asyncio
    async def test_extracts_from_request(self):
        request = MagicMock()
        request.headers = {
            "x-user-id": "usr-123",
            "x-user-email": "alice@example.com",
            "x-user-name": "Alice",
            "x-user-roles": "admin",
            "x-workroom-id": "wrk-456",
        }

        identity = await get_identity(request)

        assert identity.user_id == "usr-123"
        assert identity.workroom_id == "wrk-456"
        assert identity.is_authenticated is True

    @pytest.mark.asyncio
    async def test_empty_request_headers(self):
        request = MagicMock()
        request.headers = {}

        identity = await get_identity(request)

        assert identity.is_authenticated is False

    @pytest.mark.asyncio
    async def test_falls_back_to_local_dev_bridge_headers(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        monkeypatch.setenv("KAMIWAZA_LOCAL_DEV_AUTH_BRIDGE", "true")
        monkeypatch.setenv(
            "KAMIWAZA_LOCAL_DEV_AUTH_HEADERS_JSON",
            json.dumps(
                {
                    "x-user-id": "usr-123",
                    "x-user-name": "Alice",
                    "x-user-roles": "user,editor",
                }
            ),
        )
        request = MagicMock()
        request.headers = {}

        identity = await get_identity(request)

        assert identity.user_id == "usr-123"
        assert identity.name == "Alice"
        assert identity.roles == ["user", "editor"]
        assert identity.is_authenticated is True
