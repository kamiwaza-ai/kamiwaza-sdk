"""Session management router for extension backends."""

from __future__ import annotations

from urllib.parse import quote, urljoin

from fastapi import APIRouter, Request

from ._jwt import decode_jwt_exp as _decode_jwt_exp
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

        # Browser-facing — must resolve from the user's browser.
        # Round-8 review High #5: fall back to ``api_url`` if
        # ``public_api_url`` is unset so we don't render a malformed
        # ``/auth/login`` relative URL on legacy deployments. The
        # platform's auth endpoints live under ``/api/auth/*``, so we
        # use the raw API URL (with trailing slash trimmed) — NOT the
        # ``public_base_url`` runtime helper that strips ``/api``.
        base = (config.public_api_url or config.api_url).rstrip("/")
        return_to = config.app_url or str(request.base_url).rstrip("/")
        return {"login_url": f"{base}/auth/login?return_to={quote(return_to, safe='')}"}

    @router.post("/auth/logout")
    async def logout(request: Request) -> dict:
        config = AuthConfig.from_env()
        if not config.use_auth:
            return {
                "logout_url": None,
                "redirect_url": None,
                "front_channel_logout_url": None,
                "post_logout_redirect_uri": None,
            }

        # Two URLs, two consumers (round-8 review High #4):
        # - ``logout_url`` returned to the client is browser-facing — the
        #   browser navigates to it after the response, so it MUST use
        #   the host the browser can resolve. Prefer ``public_api_url``.
        # - The internal ``httpx.post(...)`` runs INSIDE the backend
        #   container — it MUST use a container-routable host or the
        #   server-side session termination silently fails (under the
        #   broad ``except Exception`` below). Prefer ``api_url``.
        # The original code used ``public_api_url`` for both, so under
        # ``kz-ext dev local --auth`` the browser side worked but the
        # server-side logout silently no-op'd against ``localhost``.
        # The platform's auth endpoints live under ``/api/auth/*`` so we
        # use the raw API URLs here, not the ``/api``-stripping runtime
        # helpers.
        browser_base = (config.public_api_url or config.api_url).rstrip("/")
        backend_base = (config.api_url or config.public_api_url).rstrip("/")
        app_url = config.app_url or str(request.base_url).rstrip("/")
        browser_logout_url = f"{browser_base}/auth/logout"
        backend_logout_url = f"{backend_base}/auth/logout"

        # The browser sends the URL it wants to land on after logout; core
        # validates it against its allowed hosts before echoing it back.
        try:
            payload = await request.json()
        except Exception:
            payload = None
        requested_redirect = None
        if isinstance(payload, dict):
            requested_redirect = payload.get("post_logout_redirect_uri") or payload.get(
                "redirect_uri"
            )

        # Terminate the platform session server-side AND proxy core's logout
        # response to the client (ENG-6911). The server-side POST clears
        # core's session, but the auth-gateway / Keycloak SSO cookies live in
        # the *browser* — only core's front-channel GET can clear those. Core
        # returns that GET's URL as ``front_channel_logout_url``; we must hand
        # it to the client or SSO silently re-authenticates on the next visit.
        from .auth import forward_auth_headers

        front_channel_logout_url = None
        post_logout_redirect_uri = None
        try:
            import httpx

            headers = forward_auth_headers(request.headers)
            body = (
                {"post_logout_redirect_uri": requested_redirect}
                if requested_redirect
                else {}
            )
            async with httpx.AsyncClient(
                verify=config.verify_ssl,
                timeout=5,
            ) as client:
                core_response = await client.post(
                    backend_logout_url, headers=headers, json=body
                )
            core_data = core_response.json()
            if isinstance(core_data, dict):
                core_front_channel = core_data.get("front_channel_logout_url")
                if core_front_channel:
                    # Core returns a root-relative path; resolve it against
                    # the browser-routable base so the client can navigate
                    # to it from any origin (e.g. localhost:3000 under
                    # ``kz-ext dev local --auth``).
                    front_channel_logout_url = urljoin(
                        f"{browser_base}/", core_front_channel
                    )
                post_logout_redirect_uri = core_data.get("post_logout_redirect_uri")
        except Exception:
            pass  # Best-effort — client falls back to its login redirect

        return {
            "logout_url": browser_logout_url,
            "redirect_url": f"{app_url}/logged-out",
            "front_channel_logout_url": front_channel_logout_url,
            "post_logout_redirect_uri": post_logout_redirect_uri,
        }

    return router
