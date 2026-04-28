"""Session management router for extension backends."""

from __future__ import annotations

import base64
import json
from urllib.parse import quote

from fastapi import APIRouter, Request

from .config import AuthConfig
from .errors import MisboundAuthError
from .identity import (
    anonymous_identity,
    extract_identity,
    get_identity,
    identity_from_headers,
)

# Fields that are safe to expose in /session responses. Anything on
# ``Identity`` not in this set — notably ``system_high`` (a classification
# string) and ``request_id`` — MUST NOT cross the HTTP boundary to the
# browser. The bearer credential (``X-Auth-Token``) is deliberately *not*
# on the Identity model at all; see kamiwaza_extensions_lib.identity for
# the rationale. Allowlist (not denylist) so new Identity fields default
# to *private*.
SESSION_PUBLIC_FIELDS = frozenset(
    {
        "user_id",
        "email",
        "name",
        "roles",
        "workroom_id",
        "workroom_role",
        "is_authenticated",
    }
)


def _public_session_payload(identity) -> dict:
    """Project the public subset of an Identity for the /session response."""
    return identity.model_dump(include=SESSION_PUBLIC_FIELDS)


def _decode_jwt_exp(token: str) -> int | None:
    """Extract the ``exp`` claim from a JWT **without** signature verification.

    This is intentional — the token has already been validated by the
    platform's ForwardAuth layer before reaching the extension.  We only
    read the expiry so the frontend can display a countdown / trigger a
    refresh.  Do NOT use this for access-control decisions.
    """
    parts = token.split(".")
    if len(parts) < 2:
        return None

    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{payload}{padding}")
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    exp = data.get("exp")
    if isinstance(exp, (int, float)):
        return int(exp)
    if isinstance(exp, str) and exp.isdigit():
        return int(exp)
    return None


def _session_expires_at(request: Request) -> int | None:
    auth_token = request.headers.get("x-auth-token")
    if auth_token:
        expires_at = _decode_jwt_exp(auth_token)
        if expires_at is not None:
            return expires_at

    authorization = request.headers.get("authorization")
    if not authorization:
        return None

    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        return _decode_jwt_exp(authorization[len(prefix) :].strip())
    return _decode_jwt_exp(authorization.strip())


def create_session_router(prefix: str = "") -> APIRouter:
    """Create a FastAPI router with session management endpoints.

    Endpoints consumed by the frontend ``SessionProvider``:

    * ``GET  {prefix}/session``         — current user session info
    * ``GET  {prefix}/auth/login-url``  — login redirect URL
    * ``POST {prefix}/auth/logout``     — logout + redirect URLs

    When ``KAMIWAZA_USE_AUTH`` is ``false`` (local dev), ``/session``
    returns an anonymous identity and ``/auth/login-url`` returns
    ``null``.
    """
    router = APIRouter(prefix=prefix)

    @router.get("/session")
    async def session(request: Request) -> dict:
        config = AuthConfig.from_env()

        # USE_AUTH=false: permissive — local dev returns anonymous when no
        # envelope is present, otherwise reflects whatever headers were set.
        if not config.use_auth:
            identity = await get_identity(request)
            if not identity.is_authenticated:
                return {
                    **_public_session_payload(anonymous_identity()),
                    "expires_at": None,
                }
            return {
                **_public_session_payload(identity),
                "expires_at": _session_expires_at(request),
            }

        # USE_AUTH=true: validate the envelope strictly so /session and
        # require_auth report the same auth state. Without this symmetry,
        # a malformed envelope (e.g., X-User-Id present but X-Workroom-Id
        # missing) shows a logged-in /session while every protected call
        # returns 401 — frontend split-brain. Treat malformed envelopes
        # as "logged out" so the frontend's SessionProvider routes to
        # the login flow rather than appearing authenticated.
        try:
            identity = extract_identity(request.headers)
        except MisboundAuthError:
            return {
                **_public_session_payload(identity_from_headers({})),
                "expires_at": None,
            }
        return {
            **_public_session_payload(identity),
            "expires_at": _session_expires_at(request),
        }

    @router.get("/auth/login-url")
    async def login_url(request: Request) -> dict:
        config = AuthConfig.from_env()
        if not config.use_auth:
            return {"login_url": None}

        base = config.public_api_url.rstrip("/")
        return_to = config.app_url or str(request.base_url).rstrip("/")
        return {"login_url": f"{base}/auth/login?return_to={quote(return_to, safe='')}"}

    @router.post("/auth/logout")
    async def logout(request: Request) -> dict:
        config = AuthConfig.from_env()
        if not config.use_auth:
            return {"logout_url": None, "redirect_url": None}

        base = config.public_api_url.rstrip("/")
        app_url = config.app_url or str(request.base_url).rstrip("/")
        logout_url = f"{base}/auth/logout"

        # Terminate the platform session server-side so the user is
        # actually logged out (the client only redirects to redirect_url).
        from .auth import forward_auth_headers

        try:
            import httpx

            headers = forward_auth_headers(request.headers)
            async with httpx.AsyncClient(
                verify=config.verify_ssl,
                timeout=5,
            ) as client:
                await client.post(logout_url, headers=headers)
        except Exception:
            pass  # Best-effort — redirect still happens

        return {
            "logout_url": logout_url,
            "redirect_url": f"{app_url}/logged-out",
        }

    return router
