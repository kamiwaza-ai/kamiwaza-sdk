"""Direct tests for the shared signature-less JWT decoder.

The ``kamiwaza_extensions_lib._jwt`` module is the single source of
truth for the runtime lib's JWT decoding (PR #87 round-10
consolidated ``session._decode_jwt_exp`` + ``local_dev._decode_jwt_claims``
onto this module). Both call sites delegate, so a regression here
would silently affect session expiry display AND the local-dev-auth
bridge's identity synthesis.

Round-11 review (Comprehensive + Claude consensus High) flagged the
absence of direct unit coverage — every boundary case below was
formerly only verified indirectly through caller-level assertions,
which means a future refactor could regress one branch without
failing any test.
"""

from __future__ import annotations

import base64
import json

import pytest

from kamiwaza_extensions_lib._jwt import decode_jwt_exp, decode_jwt_payload


def _encode(payload: object) -> str:
    """Encode ``payload`` as a JWT (3-segment, base64url, no signature)."""
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    return f"h.{encoded}.s"


# ---------------------------------------------------------------------------
# decode_jwt_payload — segment-count + decode behavior
# ---------------------------------------------------------------------------


class TestDecodeJwtPayload:
    def test_returns_payload_for_canonical_three_segment_jwt(self):
        token = _encode({"sub": "u-1", "email": "a@b"})
        assert decode_jwt_payload(token) == {"sub": "u-1", "email": "a@b"}

    @pytest.mark.parametrize(
        "token",
        [
            "",  # empty
            "only-one-segment",
            "header.payload",  # 2 segments — round-10 tightened to 3
        ],
    )
    def test_rejects_fewer_than_three_segments(self, token):
        # The TS bridge `decodeJwt` also requires 3 segments — round-10
        # locked Python in lock-step to prevent drift.
        assert decode_jwt_payload(token) == {}

    def test_returns_empty_on_malformed_base64(self):
        # Invalid base64 in the payload segment.
        assert decode_jwt_payload("h.!!!not-b64!!!.s") == {}

    def test_returns_empty_on_non_json_payload(self):
        # Valid base64, but the decoded bytes aren't JSON.
        garbage_b64 = (
            base64.urlsafe_b64encode(b"this is not json")
            .rstrip(b"=")
            .decode("ascii")
        )
        assert decode_jwt_payload(f"h.{garbage_b64}.s") == {}

    def test_returns_empty_on_non_object_root(self):
        # Defense against hostile payloads — JSON `null`, arrays, scalars
        # are all valid JSON but not a claims object.
        for non_object in [None, [], [1, 2, 3], 42, "string"]:
            assert decode_jwt_payload(_encode(non_object)) == {}

    def test_handles_utf8_claims(self):
        # Round-4 review (codex): platform users with non-ASCII names
        # must round-trip cleanly. The TS side has the equivalent
        # ``atob`` UTF-8 fix; this asserts the Python side stays correct.
        token = _encode({"sub": "u-1", "name": "名前", "email": "a🌸@b"})
        claims = decode_jwt_payload(token)
        assert claims["name"] == "名前"
        assert claims["email"] == "a🌸@b"

    def test_handles_payload_without_padding(self):
        # JWT base64url payloads strip ``=`` padding. The decoder must
        # re-add the right amount before calling urlsafe_b64decode.
        # ``{"a":1}`` encodes to 8 bytes → 12 raw chars → trims to 12 with no
        # padding needed; ``{"a":12}`` encodes to 9 bytes → 12 chars + 1 pad
        # → trimmed. Both shapes must decode.
        for payload in [{"a": 1}, {"a": 12}, {"a": 123}, {"a": 1234}]:
            assert decode_jwt_payload(_encode(payload)) == payload


# ---------------------------------------------------------------------------
# decode_jwt_exp — NumericDate coercion
# ---------------------------------------------------------------------------


class TestDecodeJwtExp:
    def test_returns_int_for_int_exp(self):
        assert decode_jwt_exp(_encode({"exp": 1700000000})) == 1700000000

    def test_truncates_float_exp(self):
        # Per docstring: real IdPs emit integer seconds; fractional
        # values are truncated rather than rejected.
        assert decode_jwt_exp(_encode({"exp": 1700000000.7})) == 1700000000
        assert decode_jwt_exp(_encode({"exp": 1700000000.0})) == 1700000000

    def test_returns_int_for_string_digit_exp(self):
        # Some IdPs JSON-encode NumericDate as a string.
        assert decode_jwt_exp(_encode({"exp": "1700000000"})) == 1700000000

    def test_returns_none_for_missing_exp(self):
        assert decode_jwt_exp(_encode({"sub": "u-1"})) is None

    def test_returns_none_for_bool_exp(self):
        # ``isinstance(True, int)`` is True in Python — explicit guard
        # required so a hostile ``"exp": true`` payload doesn't coerce
        # to ``1`` and falsely report the token as expired in 1970.
        assert decode_jwt_exp(_encode({"exp": True})) is None
        assert decode_jwt_exp(_encode({"exp": False})) is None

    def test_returns_none_for_negative_string_exp(self):
        # ``"-1".isdigit()`` is False — sign char fails the guard.
        # NumericDate is monotonically positive, so this is correct.
        assert decode_jwt_exp(_encode({"exp": "-1"})) is None

    def test_returns_none_for_decimal_string_exp(self):
        # ``"1234.5".isdigit()`` is False — decimal point fails. Out of
        # spec for NumericDate's string-form representation.
        assert decode_jwt_exp(_encode({"exp": "1234.5"})) is None

    def test_returns_none_for_non_numeric_exp(self):
        for bad in [None, "tomorrow", [1700000000], {"unix": 1700000000}]:
            assert decode_jwt_exp(_encode({"exp": bad})) is None

    def test_returns_none_for_malformed_token(self):
        assert decode_jwt_exp("not-a-jwt") is None
        assert decode_jwt_exp("") is None
