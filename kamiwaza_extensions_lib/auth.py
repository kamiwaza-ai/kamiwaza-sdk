"""Authentication dependencies and header forwarding."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Optional

from fastapi import Depends, HTTPException, Request

from .config import AuthConfig
from .errors import MisboundAuthError
from .identity import Identity, anonymous_identity, extract_identity, get_identity

# Headers to forward when calling other Kamiwaza services.
_FORWARD_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "x-auth-token",
        "x-user-id",
        "x-user-email",
        "x-user-name",
        "x-user-roles",
        "x-workroom-id",
        "x-request-id",
    }
)


def forward_auth_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Extract auth-related headers for forwarding to other services.

    Returns a dict containing only the platform auth / identity headers.
    Safe to call with any mapping (returns empty dict when nothing matches).
    """
    return {k: v for k, v in headers.items() if k.lower() in _FORWARD_HEADERS}


async def require_auth(request: Request) -> Identity:
    """FastAPI dependency that requires an authenticated identity.

    When ``KAMIWAZA_USE_AUTH`` is ``false`` (local dev), returns an
    anonymous identity without raising.

    Otherwise, parses the platform envelope strictly via
    ``extract_identity`` — a request that reaches the extension without
    a complete envelope (e.g. missing ``X-Workroom-Id``) is rejected
    with HTTP 401 carrying the canonical ``MisboundAuthError`` message.
    Without this strict path the new ``MisboundAuthError`` class would
    be defined but never raised on the auth surface the rest of the
    codebase actually uses.

    Raises:
        HTTPException(401): If auth is enabled and the request envelope
            is missing or malformed.
    """
    config = AuthConfig.from_env()
    if not config.use_auth:
        # Local dev — unified anonymous shape, matching /session (§4.8 P5).
        identity = await get_identity(request)
        return identity if identity.is_authenticated else anonymous_identity()
    try:
        return extract_identity(dict(request.headers))
    except MisboundAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_role(role: str) -> Callable:
    """Return a FastAPI dependency that validates the user has *role*.

    Usage::

        @app.get("/admin")
        async def admin(identity: Identity = Depends(require_role("admin"))):
            ...

    Raises:
        HTTPException(403): If the user lacks the required role.
    """

    async def _dependency(identity: Identity = Depends(require_auth)) -> Identity:
        config = AuthConfig.from_env()
        if not config.use_auth:
            return identity
        if role.lower() not in {r.lower() for r in identity.roles}:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{role}' required",
            )
        return identity

    return _dependency
