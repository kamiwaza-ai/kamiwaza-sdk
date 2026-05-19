"""Tests for kamiwaza_extensions_lib.identity."""

import asyncio
from unittest.mock import MagicMock

import pytest

from kamiwaza_extensions_lib.identity import (
    Identity,
    anonymous_identity,
    get_identity,
    identity_from_headers,
)


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


@pytest.mark.unit
class TestIdentityPydantic:
    """TS-6: Identity.model_dump() produces dict with all envelope fields."""

    def test_model_dump_round_trip(self):
        identity = identity_from_headers(
            {
                "x-user-id": "usr-123",
                "x-user-email": "alice@example.com",
                "x-user-name": "Alice",
                "x-user-roles": "admin,user",
                "x-workroom-id": "wrk-456",
                "x-user-workroom-role": "editor",
                "x-auth-token": "jwt-abc",
                "x-request-id": "req-789",
                "x-user-system-high": "true",
            }
        )
        dumped = identity.model_dump()
        assert dumped["user_id"] == "usr-123"
        assert dumped["email"] == "alice@example.com"
        assert dumped["name"] == "Alice"
        assert dumped["roles"] == ["admin", "user"]
        assert dumped["workroom_id"] == "wrk-456"
        assert dumped["workroom_role"] == "editor"
        assert dumped["request_id"] == "req-789"
        assert dumped["system_high"] == "true"
        assert dumped["is_authenticated"] is True
        # auth_token is intentionally NOT a field on Identity — the bearer
        # credential lives in headers and would leak via every model_dump()
        # call (logs, metrics, exception payloads).
        assert "auth_token" not in dumped

    def test_system_high_preserves_classification_string(self):
        """X-User-System-High carries a classification ("U", "TS", ...) not a bool.
        The string must round-trip unchanged so trust decisions can compare it."""
        for marker in ("U", "TS", "S", "C"):
            identity = identity_from_headers(
                {"x-user-id": "u", "x-user-system-high": marker}
            )
            assert identity.system_high == marker

    def test_unknown_kwargs_dropped_dont_leak_via_model_dump(self):
        """extra='ignore' guard: future Pydantic-default changes can't silently
        turn Identity into an extra-leaking surface."""
        identity = Identity(user_id="u", secret_field="should-be-dropped")  # type: ignore[call-arg]
        dumped = identity.model_dump()
        assert "secret_field" not in dumped

    def test_model_dump_for_anonymous(self):
        identity = identity_from_headers({})
        dumped = identity.model_dump()
        assert dumped["user_id"] is None
        assert dumped["is_authenticated"] is False
        assert dumped["roles"] == []


@pytest.mark.unit
class TestAnonymousIdentity:
    """TS-7: unified anonymous shape under USE_AUTH=false (§4.8 P5)."""

    def test_returns_named_anonymous_identity(self):
        identity = anonymous_identity()
        assert isinstance(identity, Identity)
        assert identity.name == "Anonymous"
        assert identity.user_id is None
        assert identity.email is None
        assert identity.is_authenticated is False
        assert identity.roles == []

    def test_require_auth_returns_anonymous_under_use_auth_false(self, monkeypatch):
        """require_auth() under USE_AUTH=false yields Identity(name='Anonymous')."""
        from kamiwaza_extensions_lib.auth import require_auth

        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "false")
        request = MagicMock()
        request.headers = {}

        identity = asyncio.run(require_auth(request))
        assert identity.name == "Anonymous"
        assert identity.is_authenticated is False

    def test_require_auth_and_session_produce_matching_identity(self, monkeypatch):
        """Under USE_AUTH=false with no envelope, both paths yield the same
        Identity shape (byte-identical ``Identity.model_dump()`` subset)."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from kamiwaza_extensions_lib.auth import require_auth
        from kamiwaza_extensions_lib.session import create_session_router

        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "false")

        # /session path
        app = FastAPI()
        app.include_router(create_session_router())
        client = TestClient(app)
        session_body = client.get("/session").json()

        # require_auth path
        request = MagicMock()
        request.headers = {}
        identity = asyncio.run(require_auth(request))
        identity_fields = identity.model_dump()

        # Every Identity field in /session response must match require_auth's output
        identity_keys_in_session = {
            k: session_body[k] for k in identity_fields if k in session_body
        }
        assert identity_keys_in_session == {
            k: identity_fields[k] for k in identity_keys_in_session
        }
        assert session_body["name"] == "Anonymous"
        assert session_body["is_authenticated"] is False
