"""Unit tests for auth header forwarding helpers."""

from kamiwaza_extensions_lib.client import forward_auth_headers


def test_forward_auth_headers_includes_x_auth_token() -> None:
    headers = forward_auth_headers({"X-Auth-Token": "abc123"})
    assert headers["x-auth-token"] == "abc123"


def test_forward_auth_headers_preserves_existing_auth_headers() -> None:
    headers = forward_auth_headers(
        {
            "Authorization": "Bearer token",
            "Cookie": "access_token=session-token",
            "X-User-Id": "u-1",
        }
    )
    assert headers["authorization"] == "Bearer token"
    assert headers["cookie"] == "access_token=session-token"
    assert headers["x-user-id"] == "u-1"
