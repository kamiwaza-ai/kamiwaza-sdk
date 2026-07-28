"""Tests for kamiwaza_extensions_lib.errors.

Traces to: ENG-3885 (UAC-9d runtime-lib exception hierarchy), design §4.2.7.
"""

import pytest


@pytest.mark.unit
class TestPackageExports:
    def test_runtime_public_surface_reexported_from_package_root(self):
        """Package root exports the runtime-lib UAC-9d public surface."""
        import kamiwaza_extensions_lib as lib

        expected_exports = {
            "KamiwazaRuntimeError",
            "MisboundAuthError",
            "UnexpectedContextError",
            "OutOfEnvelopeAccessError",
            "PlatformOutageError",
            "PlatformRedirectError",
            "extract_identity",
            "anonymous_identity",
        }

        assert expected_exports <= set(lib.__all__)
        for name in expected_exports:
            assert getattr(lib, name) is not None


@pytest.mark.unit
class TestRuntimeErrorHierarchy:
    def test_base_class_has_class_name(self):
        from kamiwaza_extensions_lib.errors import KamiwazaRuntimeError

        err = KamiwazaRuntimeError("boom")
        assert err.class_name == "kamiwaza_runtime_error"
        assert str(err) == "boom"

    def test_misbound_auth_error(self):
        from kamiwaza_extensions_lib.errors import (
            KamiwazaRuntimeError,
            MisboundAuthError,
        )

        err = MisboundAuthError("missing X-User-Id")
        assert isinstance(err, KamiwazaRuntimeError)
        assert err.class_name == "misbound_auth"

    def test_unexpected_context_error(self):
        from kamiwaza_extensions_lib.errors import (
            KamiwazaRuntimeError,
            UnexpectedContextError,
        )

        err = UnexpectedContextError("wrong context")
        assert isinstance(err, KamiwazaRuntimeError)
        assert err.class_name == "unexpected_context"

    def test_out_of_envelope_access_error(self):
        from kamiwaza_extensions_lib.errors import (
            KamiwazaRuntimeError,
            OutOfEnvelopeAccessError,
        )

        err = OutOfEnvelopeAccessError("cross-workroom attempt")
        assert isinstance(err, KamiwazaRuntimeError)
        assert err.class_name == "out_of_envelope_access"

    def test_platform_outage_error(self):
        from kamiwaza_extensions_lib.errors import (
            KamiwazaRuntimeError,
            PlatformOutageError,
        )

        err = PlatformOutageError("5xx from platform")
        assert isinstance(err, KamiwazaRuntimeError)
        assert err.class_name == "platform_outage"

    def test_platform_redirect_error_is_typed_unexpected_context(self):
        from kamiwaza_extensions_lib.errors import (
            PlatformRedirectError,
            UnexpectedContextError,
        )

        err = PlatformRedirectError(
            307,
            "/api/catalog/datasets",
            "/api/catalog/datasets/",
        )

        assert isinstance(err, UnexpectedContextError)
        assert err.class_name == "platform_redirect"
        assert err.status_code == 307
        assert err.path == "/api/catalog/datasets"
        assert err.location == "/api/catalog/datasets/"
        assert "redirect target path" in str(err)
        assert "canonical platform path" in str(err)

    def test_platform_redirect_error_does_not_label_external_target_canonical(self):
        from kamiwaza_extensions_lib.errors import PlatformRedirectError

        err = PlatformRedirectError(
            302,
            "/api/catalog/datasets",
            "https://unexpected.example/admin",
        )

        assert err.location == "/admin"
        assert err.location_origin == "https://unexpected.example"
        assert "origin is 'https://unexpected.example'" in str(err)
        assert "path is '/admin'" in str(err)
        assert "canonical location" not in str(err)

    def test_platform_redirect_error_redacts_userinfo_from_origin(self):
        from kamiwaza_extensions_lib.errors import PlatformRedirectError

        err = PlatformRedirectError(
            302,
            "/api/catalog/datasets",
            "https://user:secret@unexpected.example:8443/admin?token=also-secret",
        )

        assert err.location_origin == "https://unexpected.example:8443"
        assert "user" not in str(err)
        assert "secret" not in str(err)
        assert "token" not in str(err)


@pytest.mark.unit
class TestExtractIdentity:
    """Strict header parsing: raises MisboundAuthError on missing envelope.

    Contrast with identity_from_headers (permissive — never raises).
    """

    # TS-4: MisboundAuthError raised when X-User-Id missing
    def test_raises_when_user_id_missing(self):
        from kamiwaza_extensions_lib.errors import MisboundAuthError
        from kamiwaza_extensions_lib.identity import extract_identity

        with pytest.raises(MisboundAuthError):
            extract_identity({"x-workroom-id": "wrk-456"})

    def test_raises_when_user_id_empty(self):
        from kamiwaza_extensions_lib.errors import MisboundAuthError
        from kamiwaza_extensions_lib.identity import extract_identity

        with pytest.raises(MisboundAuthError):
            extract_identity({"x-user-id": "", "x-workroom-id": "wrk-456"})

    # TS-5: MisboundAuthError raised when X-Workroom-Id missing
    def test_raises_when_workroom_id_missing(self):
        from kamiwaza_extensions_lib.errors import MisboundAuthError
        from kamiwaza_extensions_lib.identity import extract_identity

        with pytest.raises(MisboundAuthError):
            extract_identity({"x-user-id": "usr-123"})

    def test_raises_when_workroom_id_empty(self):
        from kamiwaza_extensions_lib.errors import MisboundAuthError
        from kamiwaza_extensions_lib.identity import extract_identity

        with pytest.raises(MisboundAuthError):
            extract_identity({"x-user-id": "usr-123", "x-workroom-id": ""})

    def test_raises_when_required_header_is_whitespace_only(self):
        """Whitespace-only header values are semantically empty."""
        from kamiwaza_extensions_lib.errors import MisboundAuthError
        from kamiwaza_extensions_lib.identity import extract_identity

        with pytest.raises(MisboundAuthError):
            extract_identity({"x-user-id": "   ", "x-workroom-id": "wrk-1"})
        with pytest.raises(MisboundAuthError):
            extract_identity({"x-user-id": "usr-1", "x-workroom-id": "\t\n "})

    def test_happy_path_returns_authenticated_identity(self):
        from kamiwaza_extensions_lib.identity import extract_identity

        identity = extract_identity(
            {
                "x-user-id": "usr-123",
                "x-user-email": "alice@example.com",
                "x-user-name": "Alice",
                "x-user-roles": "admin,user",
                "x-workroom-id": "wrk-456",
                "x-user-workroom-role": "editor",
                "x-request-id": "req-789",
            }
        )

        assert identity.user_id == "usr-123"
        assert identity.email == "alice@example.com"
        assert identity.name == "Alice"
        assert identity.roles == ["admin", "user"]
        assert identity.workroom_id == "wrk-456"
        assert identity.workroom_role == "editor"
        assert identity.request_id == "req-789"
        assert identity.is_authenticated is True
