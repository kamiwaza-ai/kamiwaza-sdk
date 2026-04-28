"""Identity extraction from platform-injected headers.

Two entry points:

* ``identity_from_headers`` — permissive; never raises.  Used when the
  caller wants to handle missing-envelope cases itself (local dev,
  ``USE_AUTH=false``, ``/session`` anonymous responses).
* ``extract_identity`` — strict; raises ``MisboundAuthError`` when
  required envelope headers are missing or empty.  Matches the UAC-9d
  contract (design §4.2.7).

Note on ``X-Auth-Token``: deliberately *not* stored on ``Identity``.
The bearer credential lives in request headers; consumers that need it
(``TokenRefreshMiddleware``, ``/session`` expiry decoder, etc.) read it
from ``request.headers`` directly.  Putting it on the model would mean
any ``identity.model_dump()`` call (logs, metrics, exception payloads,
serialized error responses) would leak the credential.
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
_HEADER_REQUEST_ID = "x-request-id"


class Identity(BaseModel):
    """User identity extracted from platform headers.

    Pydantic model — supports ``.model_dump()`` for JSON serialization.

    ``extra="ignore"`` is set explicitly: unknown kwargs are dropped at
    construction.  Identity is only constructed internally with explicit
    kwargs (see ``identity_from_headers`` and ``extract_identity``), so
    surfacing untrusted extras via ``model_dump()`` would be a leak, not
    a feature.  The explicit setting also guards against future Pydantic
    v2 default changes.

    ``system_high`` is the platform's ``X-User-System-High`` header — a
    classification string (e.g. ``"U"``, ``"TS"`` per
    ``kamiwaza_sdk/services/enclaves.py``), NOT a boolean.  Consumers
    making trust decisions should compare to the platform's classification
    constants, not truthiness.
    """

    model_config = ConfigDict(extra="ignore")

    user_id: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    system_high: Optional[str] = None
    workroom_id: Optional[str] = None
    workroom_role: Optional[str] = None
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


def _lower(headers: dict[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _stripped(headers: dict[str, str], key: str) -> Optional[str]:
    """Return the stripped header value or None if absent / blank."""
    return (headers.get(key) or "").strip() or None


def _project_identity_fields(lower: dict[str, str]) -> dict:
    """Project the lower-cased header dict onto Identity field kwargs.

    Shared by ``identity_from_headers`` (permissive) and ``extract_identity``
    (strict) so the projection rule lives in exactly one place.
    """
    return {
        "email": _stripped(lower, _HEADER_USER_EMAIL),
        "name": _stripped(lower, _HEADER_USER_NAME),
        "roles": _parse_roles(lower.get(_HEADER_USER_ROLES, "")),
        "system_high": _stripped(lower, _HEADER_USER_SYSTEM_HIGH),
        "workroom_role": _stripped(lower, _HEADER_USER_WORKROOM_ROLE),
        "request_id": _stripped(lower, _HEADER_REQUEST_ID),
    }


def identity_from_headers(headers: dict[str, str]) -> Identity:
    """Build an Identity from a plain header dict.  Permissive — never raises.

    Missing fields become ``None``/defaults; ``is_authenticated`` reflects
    whether ``x-user-id`` was supplied.
    """
    lower = _lower(headers)
    user_id = _stripped(lower, _HEADER_USER_ID)
    return Identity(
        user_id=user_id,
        workroom_id=_stripped(lower, _HEADER_WORKROOM_ID),
        is_authenticated=user_id is not None,
        **_project_identity_fields(lower),
    )


def extract_identity(headers: dict[str, str]) -> Identity:
    """Strict header parsing for UAC-9d.

    Raises ``MisboundAuthError`` when ``X-User-Id`` or ``X-Workroom-Id``
    is missing or empty (including whitespace-only values — the request
    did not reach the extension via Traefik, or the platform did not
    populate the envelope).
    """
    lower = _lower(headers)
    user_id = _stripped(lower, _HEADER_USER_ID)
    workroom_id = _stripped(lower, _HEADER_WORKROOM_ID)
    if not user_id:
        raise MisboundAuthError("Required envelope header X-User-Id missing or empty")
    if not workroom_id:
        raise MisboundAuthError(
            "Required envelope header X-Workroom-Id missing or empty"
        )
    return Identity(
        user_id=user_id,
        workroom_id=workroom_id,
        is_authenticated=True,
        **_project_identity_fields(lower),
    )


async def get_identity(request: Request) -> Identity:
    """Permissive Identity extraction from a FastAPI ``Request``.

    Never raises; see ``identity_from_headers``.
    """
    return identity_from_headers(dict(request.headers))
