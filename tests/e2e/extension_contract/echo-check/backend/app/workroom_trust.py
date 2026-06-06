"""Trust model for the echo-check contract template.

Three concentric layers, weakest to strongest:

1. **Network isolation** — the platform's routing layer (Traefik / istio
   ingress + pod NetworkPolicies) is the *primary* trust boundary. The
   container port is not externally exposed in any deployed configuration;
   a caller capable of reaching the port directly is already inside the
   pod network and the platform's documented threat model treats them as
   trusted for identity (the ``X-User-*`` envelope below).

2. **Platform identity envelope** — ``X-User-Id``, ``X-User-Email``,
   ``X-Workroom-Id``, etc. are populated by the platform's ext_authz
   filter and the mesh proxy. ``kamiwaza_extensions_lib.require_auth``
   trusts these headers when present and well-formed. **Identity echo**
   (``/api/whoami``, ``/api/session``, ``/api/observability``) relies on
   this layer — see ``require_routed_request`` below for the
   defense-in-depth gate that prevents direct-container hits to those
   protected routes when the platform routing layer is absent.

3. **Routed-binding marker** — ``x-kamiwaza-trusted-proxy`` carrying
   ``$KAMIWAZA_TRUSTED_PROXY_SECRET``, injected by the routing layer on
   routed traffic. This gates the **workroom-binding helpers**
   (``current_workroom_id`` / ``workroom_role``): the most
   authorization-sensitive surface. Direct traffic that didn't pass
   through the routing layer never carries the marker → workroom binding
   stays fail-closed.

Extension authors copying this template MUST:
- Set ``$KAMIWAZA_TRUSTED_PROXY_SECRET`` in both the local
  ``docker-compose.yml`` and the deployed ``docker-compose.appgarden.yml``.
- Configure their routing layer to inject ``x-kamiwaza-trusted-proxy:
  <secret>`` on routed traffic only.
- Apply ``Depends(require_routed_request)`` to any route they consider
  sensitive enough to deserve the marker check, in addition to
  ``require_auth``.

ENG-5956 (kamiwaza-sdk#134) self-review H1 + RE-REVIEW H1 — the trust
boundary is documented here in source so the pattern propagates with the
template.
"""

from __future__ import annotations

import hmac
import os
import re
from typing import Any

from fastapi import HTTPException, Request

TRUSTED_ROUTED_ROOT_PATH_PREFIX = "/runtime/apps/"
_FALSEY_ENV_VALUES = frozenset({"", "0", "false", "no", "off", "n", "f"})
_MAX_LOG_FIELD_LENGTH = 256
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def runtime_value(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def auth_enabled() -> bool:
    raw = os.getenv("KAMIWAZA_USE_AUTH")
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSEY_ENV_VALUES


def runtime_prefix() -> str:
    value = runtime_value("KAMIWAZA_APP_PATH")
    if not value:
        return ""
    normalized = value if value.startswith("/") else f"/{value}"
    return normalized.rstrip("/") or "/"


# Header name the trusted proxy (Traefik / istio ingress / similar) injects so
# the app can distinguish a real routed request from direct container traffic.
# Fixed name; the SECRET that must match is in KAMIWAZA_TRUSTED_PROXY_SECRET.
TRUSTED_PROXY_HEADER_NAME = "x-kamiwaza-trusted-proxy"


def trusted_proxy_secret() -> str | None:
    """Shared secret the routed proxy injects in ``x-kamiwaza-trusted-proxy``.

    Returning ``None`` means trust-routed is disabled — the app fails CLOSED
    on every routed-trust check, regardless of ``root_path``. Extension
    authors copying this template MUST set the env var (and configure their
    routing layer to inject the header) before the trusted-routed path will
    flip identity-header forwarding on.

    ENG-5956 follow-up: dropping the app-level ``prefix=runtime_prefix`` from
    ``_register_routes`` removed the implicit 404-on-direct defense; the
    trusted-routed signal must now be stronger than ``root_path`` alone.
    """
    return runtime_value("KAMIWAZA_TRUSTED_PROXY_SECRET")


def has_trusted_proxy_marker(request: Request) -> bool:
    secret = trusted_proxy_secret()
    if not secret:
        return False
    header_value = request.headers.get(TRUSTED_PROXY_HEADER_NAME)
    if not header_value:
        return False
    # ENG-5956 follow-up — kamiwaza-sdk#134 RE-REVIEW H2: use a
    # constant-time comparison to avoid a timing side-channel on the
    # shared secret. Amplified by echo-check being a starter template
    # extension authors copy verbatim — a weak primitive here would
    # propagate downstream.
    return hmac.compare_digest(header_value, secret)


async def require_routed_request(request: Request) -> None:
    """Defense-in-depth: protected routes must be reached via the routing layer.

    ENG-5956 follow-up (kamiwaza-sdk#134 RE-REVIEW #2): dropping the
    app-level ``prefix=runtime_prefix`` made protected routes directly
    addressable on the container port. The platform's network isolation
    + ext_authz envelope is the documented primary trust boundary, but
    this gate restores the pre-PR shape (direct access → 404) as an
    additional layer for identity-echo routes (``/api/whoami``,
    ``/api/session``, ``/api/observability``). Combined with the
    workroom-binding helpers' own marker checks, the trust model is
    consistent across surfaces: direct traffic that didn't pass through
    the routing layer never carries the marker, and protected routes
    silently 404 instead of returning a spoofable identity payload.

    Returns 404 (not 401/403) to preserve the pre-PR external behavior
    for unrouted access — there is no useful information for an
    unrouted caller to receive.

    When no ``KAMIWAZA_TRUSTED_PROXY_SECRET`` is configured, the route
    fails closed (404) — extensions MUST opt in to the trust model.

    Apply as a FastAPI dependency on protected routes:
        ``Depends(require_routed_request)``
    """
    if not has_trusted_proxy_marker(request):
        raise HTTPException(status_code=404)


def safe_log_field(value: str | None) -> str:
    if not value:
        return ""
    sanitized = "".join(
        char for char in value if char not in "\r\n=" and ord(char) >= 32
    )
    return sanitized[:_MAX_LOG_FIELD_LENGTH]


def normalized_workroom_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized or not _UUID_RE.fullmatch(normalized):
        return None
    return normalized


def normalized_workroom_role(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    return normalized or None


def routed_root_path(request: Request) -> str:
    root_path = str(request.scope.get("root_path") or "").strip()
    if not root_path:
        return ""
    normalized = root_path if root_path.startswith("/") else f"/{root_path}"
    normalized = normalized.rstrip("/") or "/"
    return normalized if normalized.startswith(TRUSTED_ROUTED_ROOT_PATH_PREFIX) else ""


def has_forwarded_identity_headers(request: Request) -> bool:
    return any(
        request.headers.get(header)
        for header in (
            "x-user-id",
            "x-user-email",
            "x-user-workroom-id",
            "x-user-workroom-role",
        )
    )


def trusted_routed_workroom_context(request: Request, identity: Any) -> bool:
    if not getattr(identity, "is_authenticated", False):
        return False
    if not auth_enabled():
        return False
    expected_runtime_prefix = runtime_prefix()
    if not expected_runtime_prefix:
        return False
    # ENG-5956 follow-up (kamiwaza-sdk#134 self-review H1): require BOTH a
    # matching root_path AND the trusted-proxy shared-secret marker.
    # root_path alone is forgeable by a direct container caller (uvicorn
    # --root-path sets it on every request, including direct). The marker
    # closes that gap — direct traffic doesn't carry the proxy-injected
    # header. Fails CLOSED when no secret is configured.
    if not has_trusted_proxy_marker(request):
        return False
    return routed_root_path(request) == expected_runtime_prefix


def trust_identity_workroom_fields(request: Request, identity: Any) -> bool:
    if not getattr(identity, "is_authenticated", False):
        return False
    if trusted_routed_workroom_context(request, identity):
        return True
    return not has_forwarded_identity_headers(request)


def current_workroom_id(request: Request, identity: Any) -> str | None:
    workroom_id = getattr(identity, "workroom_id", None)
    normalized = normalized_workroom_id(workroom_id if isinstance(workroom_id, str) else None)
    if normalized and trust_identity_workroom_fields(request, identity):
        return normalized
    if not trusted_routed_workroom_context(request, identity):
        return None
    return normalized_workroom_id(request.headers.get("x-user-workroom-id"))


def workroom_role(request: Request, identity: Any) -> str | None:
    role = getattr(identity, "workroom_role", None)
    if isinstance(role, str) and role and trust_identity_workroom_fields(request, identity):
        return normalized_workroom_role(role)
    if not trusted_routed_workroom_context(request, identity):
        return None
    return normalized_workroom_role(request.headers.get("x-user-workroom-role"))
