"""Session management router for extension backends."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .config import AuthConfig
from .identity import Identity, get_identity


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

        if not config.use_auth and not identity.is_authenticated:
            return {
                "user_id": None,
                "email": None,
                "name": "Anonymous",
                "roles": [],
                "workroom_id": None,
                "is_authenticated": False,
            }

        return {
            "user_id": identity.user_id,
            "email": identity.email,
            "name": identity.name,
            "roles": identity.roles,
            "workroom_id": identity.workroom_id,
            "is_authenticated": identity.is_authenticated,
        }

    @router.get("/auth/login-url")
    async def login_url(request: Request) -> dict:
        config = AuthConfig.from_env()
        if not config.use_auth:
            return {"login_url": None}

        base = config.public_api_url.rstrip("/")
        return_to = config.app_url or str(request.base_url).rstrip("/")
        return {"login_url": f"{base}/auth/login?return_to={return_to}"}

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
