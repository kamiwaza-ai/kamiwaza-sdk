"""Tests for kamiwaza_extensions_lib.errors.

Traces to: ENG-3885 (UAC-9d runtime-lib exception hierarchy), design §4.2.7.
"""

import pytest


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
