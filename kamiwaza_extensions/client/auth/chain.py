"""Auth resolution chain.

Priority:
  1. Explicit credentials (pass-thru)
  2. ~/.aws/credentials (pass-thru)
  3. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from env (pass-thru)
  4. R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY from env (pass-thru)
  5. Cloudflare SSO via broker (refreshable)
"""

from __future__ import annotations

from typing import Literal

from kamiwaza_extensions.client.auth.static import (
    StaticCredentials,
    get_static_credentials,
)

AuthMode = Literal["auto", "static", "sso"]
AuthMethod = Literal["static", "sso"]


def resolve_credentials(
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    session_token: str | None = None,
    auth_mode: AuthMode = "auto",
) -> tuple[StaticCredentials | None, AuthMethod]:
    """Resolve credentials via the auth chain.

    Returns (credentials, method) where method is "static" or "sso".
    For static, credentials may be None if neither explicit nor env are set.
    """
    if auth_mode not in {"auto", "static", "sso"}:
        raise ValueError(f"Unknown authentication mode: {auth_mode}")
    if auth_mode == "sso":
        return None, "sso"

    static = get_static_credentials(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
    )
    if static is not None:
        return static, "static"
    if auth_mode == "static":
        raise RuntimeError(
            "Static object-storage credentials were requested but none were found"
        )
    return None, "sso"
