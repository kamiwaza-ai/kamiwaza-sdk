"""Session management router for extension backends."""

from __future__ import annotations

import base64
import json
from urllib.parse import quote

from fastapi import APIRouter, Request

from .config import AuthConfig
from .identity import Identity, get_identity


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
        return _decode_jwt_exp(authorization[len(prefix):].strip())
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
        identity = await get_identity(request)
        expires_at = _session_expires_at(request) if identity.is_authenticated else None

        if not config.use_auth and not identity.is_authenticated:
            return {
                "user_id": None,
                "email": None,
                "name": "Anonymous",
                "roles": [],
                "workroom_id": None,
                "is_authenticated": False,
                "expires_at": None,
            }

        return {
            "user_id": identity.user_id,
            "email": identity.email,
            "name": identity.name,
            "roles": identity.roles,
            "workroom_id": identity.workroom_id,
            "is_authenticated": identity.is_authenticated,
            "expires_at": expires_at,
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
        return {
            "logout_url": f"{base}/auth/logout",
            "redirect_url": f"{app_url}/logged-out",
        }

    return router
