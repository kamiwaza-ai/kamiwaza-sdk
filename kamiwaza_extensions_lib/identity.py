"""Identity extraction from platform-injected headers.

Two entry points:

* ``identity_from_headers`` — permissive; never raises.  Used when the
  caller wants to handle missing-envelope cases itself (local dev,
  ``USE_AUTH=false``, ``/session`` anonymous responses).
* ``extract_identity`` — strict; raises ``MisboundAuthError`` when
  required envelope headers are missing or empty.  Matches the UAC-9d
  contract (design §4.2.7).
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from .errors import MisboundAuthError

# Headers set by Kamiwaza ForwardAuth on authenticated requests.
_HEADER_USER_ID = "x-user-id"
_HEADER_USER_EMAIL = "x-user-email"
_HEADER_USER_NAME = "x-user-name"
_HEADER_USER_ROLES = "x-user-roles"
_HEADER_USER_SYSTEM_HIGH = "x-user-system-high"
_HEADER_WORKROOM_ID = "x-workroom-id"
_HEADER_USER_WORKROOM_ROLE = "x-user-workroom-role"
_HEADER_AUTH_TOKEN = "x-auth-token"
_HEADER_REQUEST_ID = "x-request-id"


class Identity(BaseModel):
    """User identity extracted from platform headers.

    Pydantic model — supports ``.model_dump()`` for JSON serialization
    and pass-through of unknown fields via ``extra="allow"`` for
    forward compatibility as the envelope evolves.
    """

    model_config = ConfigDict(extra="allow")

    user_id: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    system_high: bool = False
    workroom_id: Optional[str] = None
    workroom_role: Optional[str] = None
    auth_token: Optional[str] = None
    request_id: Optional[str] = None
    is_authenticated: bool = False


#: Canonical display name used for the anonymous Identity under USE_AUTH=false.
#: Shared by ``require_auth`` and the ``/session`` endpoint so the frontend sees
#: a consistent placeholder (§4.8 P5).
ANONYMOUS_NAME = "Anonymous"


def anonymous_identity() -> "Identity":
    """Return the canonical anonymous Identity used under ``USE_AUTH=false``.

    Guarantees ``require_auth()`` and the ``/session`` endpoint produce a
    byte-identical Identity shape for local dev (§4.2.12 P5).
    """
    return Identity(name=ANONYMOUS_NAME, is_authenticated=False)


def _parse_roles(raw: str) -> list[str]:
    """Split comma-separated roles, filtering blanks."""
    return [r.strip() for r in raw.split(",") if r.strip()]


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes"}


def _lower(headers: dict[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def identity_from_headers(headers: dict[str, str]) -> Identity:
    """Build an Identity from a plain header dict.  Permissive — never raises.

    Missing fields become ``None``/defaults; ``is_authenticated`` reflects
    whether ``x-user-id`` was supplied.
    """
    lower = _lower(headers)
    user_id = lower.get(_HEADER_USER_ID) or None
    return Identity(
        user_id=user_id,
        email=lower.get(_HEADER_USER_EMAIL) or None,
        name=lower.get(_HEADER_USER_NAME) or None,
        roles=_parse_roles(lower.get(_HEADER_USER_ROLES, "")),
        system_high=_parse_bool(lower.get(_HEADER_USER_SYSTEM_HIGH, "")),
        workroom_id=lower.get(_HEADER_WORKROOM_ID) or None,
        workroom_role=lower.get(_HEADER_USER_WORKROOM_ROLE) or None,
        auth_token=lower.get(_HEADER_AUTH_TOKEN) or None,
        request_id=lower.get(_HEADER_REQUEST_ID) or None,
        is_authenticated=user_id is not None,
    )


def extract_identity(headers: dict[str, str]) -> Identity:
    """Strict header parsing for UAC-9d.

    Raises ``MisboundAuthError`` when ``X-User-Id`` or ``X-Workroom-Id``
    is missing or empty — the request did not reach the extension via
    Traefik, or the platform did not populate the envelope.
    """
    lower = _lower(headers)
    user_id = lower.get(_HEADER_USER_ID) or None
    workroom_id = lower.get(_HEADER_WORKROOM_ID) or None
    if not user_id:
        raise MisboundAuthError("Required envelope header X-User-Id missing or empty")
    if not workroom_id:
        raise MisboundAuthError(
            "Required envelope header X-Workroom-Id missing or empty"
        )
    return Identity(
        user_id=user_id,
        email=lower.get(_HEADER_USER_EMAIL) or None,
        name=lower.get(_HEADER_USER_NAME) or None,
        roles=_parse_roles(lower.get(_HEADER_USER_ROLES, "")),
        system_high=_parse_bool(lower.get(_HEADER_USER_SYSTEM_HIGH, "")),
        workroom_id=workroom_id,
        workroom_role=lower.get(_HEADER_USER_WORKROOM_ROLE) or None,
        auth_token=lower.get(_HEADER_AUTH_TOKEN) or None,
        request_id=lower.get(_HEADER_REQUEST_ID) or None,
        is_authenticated=True,
    )


async def get_identity(request: Request) -> Identity:
    """Permissive Identity extraction from a FastAPI ``Request``.

    Never raises; see ``identity_from_headers``.
    """
    return identity_from_headers(dict(request.headers))
