"""Tests for kamiwaza_extensions_lib.auth."""

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from kamiwaza_extensions_lib.auth import (
    forward_auth_headers,
    forward_auth_httpx_headers,
    require_auth,
    require_role,
)
from kamiwaza_extensions_lib.errors import MisboundAuthError
from kamiwaza_extensions_lib.identity import Identity


@pytest.mark.unit
class TestForwardAuthHeaders:
    def test_extracts_auth_headers(self):
        headers = {
            "Authorization": "Bearer token123",
            "Cookie": "access_token=abc123; other=value",
            "X-Auth-Token": "jwt-abc",
            "X-User-Id": "usr-123",
            "X-User-Email": "alice@example.com",
            "X-User-Name": "Alice",
            "X-User-Roles": "admin,user",
            "X-User-Groups": "engineering,search",
            "X-User-Attributes-Hash": "sha256:attributes",
            "X-User-System-High": "TS",
            "X-User-Workroom-Role": "editor",
            "X-Workroom-Id": "wrk-456",
            "X-User-Workroom-Id": "wrk-456",
            "X-Auth-Azp": "chat-with-docs",
            "X-Request-Id": "req-789",
            "X-User-Signature": "legacy-signature",
            "X-User-Signature-Stable": "stable-signature",
            "X-User-Signature-Ts": "1784390400",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        result = forward_auth_headers(headers)

        assert result == {
            "Authorization": "Bearer token123",
            "Cookie": "access_token=abc123; other=value",
            "X-Auth-Token": "jwt-abc",
            "X-User-Id": "usr-123",
            "X-User-Email": "alice@example.com",
            "X-User-Name": "Alice",
            "X-User-Roles": "admin,user",
            "X-User-Groups": "engineering,search",
            "X-User-Attributes-Hash": "sha256:attributes",
            "X-User-System-High": "TS",
            "X-User-Workroom-Role": "editor",
            "X-Workroom-Id": "wrk-456",
            "X-User-Workroom-Id": "wrk-456",
            "X-Auth-Azp": "chat-with-docs",
            "X-Request-Id": "req-789",
            "X-User-Signature": "legacy-signature",
            "X-User-Signature-Stable": "stable-signature",
            "X-User-Signature-Ts": "1784390400",
        }

    def test_forwards_classification_and_workroom_role(self):
        """Regression guard: the new envelope headers MUST be forwarded so
        downstream services can re-establish the caller's classification
        and workroom role when the extension calls another Kamiwaza service."""
        result = forward_auth_headers(
            {"X-User-System-High": "U", "X-User-Workroom-Role": "viewer"}
        )
        assert result == {
            "X-User-System-High": "U",
            "X-User-Workroom-Role": "viewer",
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

    def test_combines_duplicate_cookie_fields(self):
        headers = Headers(
            raw=[
                (b"cookie", b"session=opaque"),
                (b"cookie", b"csrf=bound"),
                (b"x-user-id", b"usr-123"),
            ]
        )

        result = forward_auth_headers(headers)

        assert result["cookie"] == "session=opaque; csrf=bound"
        assert result["x-user-id"] == "usr-123"

    def test_httpx_headers_preserve_non_ascii_wire_bytes(self):
        headers = Headers(
            raw=[
                (b"x-user-name", "José".encode()),
                (b"x-user-groups", "Ingénierie".encode()),
            ]
        )

        result = forward_auth_httpx_headers(headers)

        assert (b"x-user-name", "José".encode()) in result.raw
        assert (b"x-user-groups", "Ingénierie".encode()) in result.raw

    def test_httpx_headers_map_invalid_envelope_to_typed_error(self):
        with pytest.raises(MisboundAuthError, match="invalid HTTP header"):
            forward_auth_httpx_headers({"X-User-Name": "unsafe\r\nvalue"})

    def test_raw_httpx_headers_map_invalid_envelope_to_typed_error(self):
        headers = httpx.Headers([(b"x-user-name", b"eve\r\nx-user-roles: admin")])

        with pytest.raises(MisboundAuthError, match="invalid HTTP header"):
            forward_auth_httpx_headers(headers)

    @pytest.mark.parametrize("name", [b"authorization", b"x-user-id"])
    def test_raw_httpx_headers_reject_ambiguous_duplicate_fields(self, name):
        headers = httpx.Headers(
            [
                (name, b"first"),
                (name, b"second"),
            ]
        )

        with pytest.raises(MisboundAuthError, match="duplicate HTTP header"):
            forward_auth_httpx_headers(headers)

    def test_string_helper_rejects_duplicate_fields_from_httpx_headers(self):
        headers = httpx.Headers(
            [
                (b"x-request-id", b"first"),
                (b"x-request-id", b"second"),
            ]
        )

        with pytest.raises(MisboundAuthError, match="duplicate HTTP header"):
            forward_auth_headers(headers)

    def test_starlette_headers_reject_ambiguous_duplicate_fields(self):
        headers = Headers(
            raw=[
                (b"authorization", b"Bearer user"),
                (b"authorization", b"Bearer attacker"),
                (b"x-user-id", b"real-user"),
                (b"x-user-id", b"attacker-user"),
            ]
        )

        with pytest.raises(MisboundAuthError, match="duplicate HTTP header"):
            forward_auth_httpx_headers(headers)

    def test_case_variant_mapping_rejects_ambiguous_duplicate_fields(self):
        headers = {
            "Authorization": "Bearer user",
            "authorization": "Bearer attacker",
            "X-User-Id": "real-user",
            "x-user-id": "attacker-user",
        }

        with pytest.raises(MisboundAuthError, match="duplicate HTTP header"):
            forward_auth_httpx_headers(headers)


@pytest.mark.unit
class TestRequireAuth:
    @pytest.mark.asyncio
    async def test_authenticated_request(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        request = MagicMock()
        request.headers = {
            "x-user-id": "usr-123",
            "x-user-email": "a@b.com",
            "x-workroom-id": "wrk-456",
        }

        identity = await require_auth(request)

        assert identity.user_id == "usr-123"
        assert identity.workroom_id == "wrk-456"
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
    async def test_missing_workroom_id_rejected_under_strict_auth(self, monkeypatch):
        """Critical: a request with X-User-Id but no X-Workroom-Id MUST NOT
        reach protected handlers. Pre-fix, the permissive get_identity()
        path treated such requests as authenticated with workroom_id=None
        — exactly the malformed envelope MisboundAuthError exists to catch.
        """
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        request = MagicMock()
        request.headers = {"x-user-id": "usr-123"}  # no x-workroom-id

        with pytest.raises(HTTPException) as exc_info:
            await require_auth(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_user_id_rejected_under_strict_auth(self, monkeypatch):
        """Symmetry counterpart to the workroom-id test: missing X-User-Id
        with X-Workroom-Id present must also reject (PR re-review request)."""
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        request = MagicMock()
        request.headers = {"x-workroom-id": "wrk-456"}  # no x-user-id

        with pytest.raises(HTTPException) as exc_info:
            await require_auth(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_duplicate_identity_header_rejected_under_strict_auth(
        self, monkeypatch
    ):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/protected"
        request.headers = Headers(
            raw=[
                (b"x-user-id", b"usr-123"),
                (b"x-workroom-id", b"wrk-456"),
                (b"x-user-roles", b"viewer"),
                (b"x-user-roles", b"admin"),
            ]
        )

        with pytest.raises(HTTPException) as exc_info:
            await require_auth(request)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Authentication required"

    @pytest.mark.asyncio
    async def test_401_detail_does_not_leak_envelope_internals(self, monkeypatch):
        """The 401 body should be scrubbed to the canonical "Authentication
        required" — the raw exception text naming the missing header is
        useful for server-side triage, harmful as an HTTP response body
        (PR re-review Medium #1)."""
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/protected"
        request.headers = {"x-user-id": "usr-123"}  # missing workroom-id

        with pytest.raises(HTTPException) as exc_info:
            await require_auth(request)
        assert exc_info.value.detail == "Authentication required"
        # Per-header detail leaked into the public 401 body would be a
        # regression — these strings come from MisboundAuthError messages.
        assert "X-Workroom-Id" not in str(exc_info.value.detail)
        assert "X-User-Id" not in str(exc_info.value.detail)
        # Canonical class name lives in WWW-Authenticate per RFC 6750.
        assert exc_info.value.headers is not None
        assert 'error="misbound_auth"' in exc_info.value.headers["WWW-Authenticate"]

    @pytest.mark.asyncio
    async def test_whitespace_only_workroom_id_rejected(self, monkeypatch):
        """Whitespace-only headers must be treated as empty, not as
        workroom_id="   ", or a misconfigured Traefik bypasses the gate."""
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        request = MagicMock()
        request.headers = {"x-user-id": "usr-123", "x-workroom-id": "   "}

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

    @pytest.mark.asyncio
    async def test_local_dev_mode_does_not_validate_envelope(self, monkeypatch):
        """USE_AUTH=false uses the permissive parser — extension authors
        running locally without a platform must not hit MisboundAuthError."""
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "false")
        request = MagicMock()
        request.headers = {"x-user-id": "usr-123"}  # no workroom — fine in dev

        identity = await require_auth(request)

        assert identity.user_id == "usr-123"
        assert identity.workroom_id is None


@pytest.mark.unit
class TestRequireRole:
    @pytest.mark.asyncio
    async def test_user_has_role(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        request = MagicMock()
        request.headers = {
            "x-user-id": "usr-123",
            "x-workroom-id": "wrk-456",
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

    @pytest.mark.asyncio
    async def test_local_dev_mode_skips_role_check(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "false")
        identity = Identity(
            user_id=None,
            roles=[],
            is_authenticated=False,
        )

        dep = require_role("admin")
        result = await dep(identity=identity)
        # Should not raise 403 — local dev mode bypasses role enforcement
        assert result.is_authenticated is False
