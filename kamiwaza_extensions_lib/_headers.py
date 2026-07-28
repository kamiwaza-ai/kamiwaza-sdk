"""Internal HTTP header encoding shared by runtime platform clients."""

from __future__ import annotations

_HTTP_TOKEN_CHARACTERS = frozenset(
    "!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)
_HTTP_CONTROL_CHARACTERS = frozenset(chr(code) for code in range(32)) | {"\x7f"}


def has_http_control_character(value: str) -> bool:
    """Return whether an HTTP field value contains a prohibited control."""
    return any(character in _HTTP_CONTROL_CHARACTERS for character in value)


def header_bytes(key: str, value: str) -> tuple[bytes, bytes]:
    """Encode a validated header while preserving ASGI wire-byte round trips."""
    if (
        not key
        or any(character not in _HTTP_TOKEN_CHARACTERS for character in key)
        or has_http_control_character(value)
    ):
        raise ValueError(f"received an invalid header {key!r}")
    encoded_value = (
        value.encode("latin-1")
        if all(ord(character) <= 255 for character in value)
        else value.encode("utf-8")
    )
    return (key.encode("ascii"), encoded_value)


def is_http_token(value: str) -> bool:
    """Return whether *value* is a non-empty RFC HTTP token."""
    return bool(value) and all(
        character in _HTTP_TOKEN_CHARACTERS for character in value
    )
