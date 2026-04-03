"""Identity extraction from platform-injected headers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fastapi import Request

from .local_dev import get_local_dev_auth_headers


# Headers set by Kamiwaza ForwardAuth on authenticated requests.
_HEADER_USER_ID = "x-user-id"
_HEADER_USER_EMAIL = "x-user-email"
_HEADER_USER_NAME = "x-user-name"
_HEADER_USER_ROLES = "x-user-roles"
_HEADER_WORKROOM_ID = "x-workroom-id"
_HEADER_REQUEST_ID = "x-request-id"


@dataclass
class Identity:
    """User identity extracted from platform headers.

    Always constructed via ``get_identity()``; never raises on missing
    headers.  Check ``is_authenticated`` to determine whether the
    request came from a logged-in user.
    """

    user_id: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    roles: list[str] = field(default_factory=list)
    workroom_id: Optional[str] = None
    request_id: Optional[str] = None
    is_authenticated: bool = False


def _parse_roles(raw: str) -> list[str]:
    """Split comma-separated roles, filtering blanks."""
    return [r.strip() for r in raw.split(",") if r.strip()]


def identity_from_headers(headers: dict[str, str]) -> Identity:
    """Build an Identity from a plain header dict (lowercase keys).

    This is the underlying parser; ``get_identity`` wraps it for
    FastAPI requests.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    user_id = lower.get(_HEADER_USER_ID) or None
    return Identity(
        user_id=user_id,
        email=lower.get(_HEADER_USER_EMAIL) or None,
        name=lower.get(_HEADER_USER_NAME) or None,
        roles=_parse_roles(lower.get(_HEADER_USER_ROLES, "")),
        workroom_id=lower.get(_HEADER_WORKROOM_ID) or None,
        request_id=lower.get(_HEADER_REQUEST_ID) or None,
        is_authenticated=user_id is not None,
    )


async def get_identity(request: Request) -> Identity:
    """Extract identity from a FastAPI request.

    Always returns an ``Identity`` — never raises.  When no identity
    headers are present, ``is_authenticated`` is ``False`` and all
    fields are ``None``.
    """
    identity = identity_from_headers(dict(request.headers))
    if identity.is_authenticated:
        return identity
    return identity_from_headers(get_local_dev_auth_headers())
