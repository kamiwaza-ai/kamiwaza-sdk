"""Session management endpoints for Kamiwaza auth.

Provides standard session endpoints that all App Garden applications need:
- GET /session - Get current session info
- GET /auth/login-url - Build login redirect URL
- POST /auth/logout - Logout and get redirect URLs
"""

from __future__ import annotations

import enum
import logging
import os
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from ..errors import SessionExpiredError
from ..identity import get_identity
from ..jwt import calculate_session_expires_at

logger = logging.getLogger(__name__)

_FALSEY = {"0", "false", "no", "off"}


class LogoutRequest(BaseModel):
    """Request body for logout endpoint."""

    post_logout_redirect_uri: str | None = None


class LogoutResponse(BaseModel):
    """Response from logout endpoint."""

    success: bool
    message: str
    redirect_url: str | None = None
    front_channel_logout_url: str | None = None


def _auth_enabled() -> bool:
    """Check if authentication is enabled via environment variable."""
    return (os.getenv("KAMIWAZA_USE_AUTH") or "true").lower() not in _FALSEY


def _anonymous_identity() -> dict:
    """Return anonymous identity when auth is disabled."""
    return {
        "user_id": "anonymous",
        "email": "anonymous@local",
        "name": "Anonymous",
        "roles": [],
        "request_id": None,
        "auth_enabled": False,
        # Note: session_expires_at is intentionally omitted when auth_enabled=False
    }


def create_session_router(
    prefix: str = "",
    tags: list[str] | None = None,
    auth_enabled_fn: Callable[[], bool] | None = None,
) -> APIRouter:
    """Create a session management router for FastAPI.

    Provides standard session endpoints:
    - GET {prefix}/session - Get current session info
    - GET {prefix}/auth/login-url - Build login redirect URL
    - POST {prefix}/auth/logout - Logout and get redirect URLs

    Args:
        prefix: URL prefix for all routes (default: "")
        tags: OpenAPI tags for the endpoints (default: ["session"])
        auth_enabled_fn: Custom function to check if auth is enabled
                        (default: checks KAMIWAZA_USE_AUTH env var)

    Returns:
        Configured APIRouter with session endpoints

    Example:
        from kamiwaza_extensions_lib.endpoints import create_session_router

        app = FastAPI()
        app.include_router(create_session_router())

        # Or with custom prefix
        app.include_router(create_session_router(prefix="/api"))
    """
    effective_tags: list[str | enum.Enum] = list(tags) if tags else ["session"]

    router = APIRouter(prefix=prefix, tags=effective_tags)
    check_auth = auth_enabled_fn or _auth_enabled

    @router.get("/session")
    async def get_session(request: Request):
        """Get current session information.

        Returns user identity, auth mode, and session timing.
        When auth is disabled, returns anonymous identity without session timing.
        """
        if not check_auth():
            return _anonymous_identity()

        identity = await get_identity(request)
        if not identity.user_id or not identity.email:
            raise SessionExpiredError()

        # Calculate session expiry from JWT iat claim
        session_expires_at = calculate_session_expires_at(request)

        response: dict[str, Any] = {
            "user_id": identity.user_id,
            "email": identity.email,
            "name": identity.name,
            "roles": identity.roles,
            "request_id": identity.request_id,
            "auth_enabled": True,
        }

        # Only include session_expires_at if we could calculate it
        if session_expires_at:
            response["session_expires_at"] = session_expires_at

        return response

    @router.get("/auth/login-url")
    async def build_login_url(
        request: Request,
        redirect_uri: str = Query(..., description="App URL to return to"),
    ):
        """Build login URL for redirecting to Kamiwaza auth.

        Args:
            redirect_uri: URL to redirect back to after login

        Returns:
            JSON with login_url field containing the full login URL
        """
        # Use public URL for browser redirects, with fallback chain
        api_base = (
            os.getenv("KAMIWAZA_PUBLIC_API_URL")
            or os.getenv("NEXT_PUBLIC_API_BASE")
            or "https://localhost/api"
        )
        login_base = f"{api_base.rstrip('/')}/auth/login"

        params = {"redirect_uri": redirect_uri, "state": redirect_uri}

        login_url = f"{login_base}?{urllib.parse.urlencode(params)}"
        return {"login_url": login_url}

    @router.post("/auth/logout", response_model=LogoutResponse)
    async def logout(request: Request, body: LogoutRequest | None = None):
        """Logout current user.

        Terminates the user's session by calling Kamiwaza logout endpoint.
        Returns redirect URLs for front-channel logout and post-logout redirect.

        Even if Kamiwaza logout fails, the user is still logged out locally
        and provided with a redirect URL.
        """
        # Get Kamiwaza API base URL
        api_base = (
            os.getenv("KAMIWAZA_API_URL")
            or os.getenv("KAMIWAZA_PUBLIC_API_URL")
            or "http://host.docker.internal:8080"
        )
        logout_url = f"{api_base.rstrip('/')}/auth/logout"

        # Default redirect URL (Kamiwaza login page)
        default_redirect = (
            os.getenv("KAMIWAZA_PUBLIC_API_URL", "https://localhost")
            .rstrip("/")
            .replace("/api", "")
            + "/login"
        )

        # Use custom redirect if provided
        post_logout_redirect = (
            body.post_logout_redirect_uri if body else None
        ) or default_redirect

        try:
            # Forward auth headers and x-forwarded headers to Kamiwaza
            headers = {}
            if "cookie" in request.headers:
                headers["cookie"] = request.headers["cookie"]
            if "authorization" in request.headers:
                headers["authorization"] = request.headers["authorization"]

            # Forward x-forwarded headers
            forwarded_headers = [
                "x-forwarded-host",
                "x-forwarded-proto",
                "x-forwarded-for",
                "x-forwarded-port",
                "x-forwarded-prefix",
            ]
            for header in forwarded_headers:
                if header in request.headers:
                    headers[header] = request.headers[header]

            # Debug logging for logout flow
            logger.info(
                "Logout: calling %s with headers: cookie=%s, authorization=%s",
                logout_url,
                "present" if "cookie" in headers else "missing",
                "present" if "authorization" in headers else "missing",
            )
            if "cookie" in headers:
                # Log cookie names (not values) for debugging
                cookie_names = [
                    c.split("=")[0].strip()
                    for c in headers["cookie"].split(";")
                    if "=" in c
                ]
                logger.info("Logout: cookie names in request: %s", cookie_names)

            # Respect TLS verification setting
            tls_verify = (
                os.getenv("KAMIWAZA_TLS_REJECT_UNAUTHORIZED", "true").lower()
                not in _FALSEY
            )
            async with httpx.AsyncClient(timeout=10.0, verify=tls_verify) as client:
                response = await client.post(
                    logout_url,
                    headers=headers,
                    json={"post_logout_redirect_uri": post_logout_redirect},
                )
                logger.info(
                    "Logout: Kamiwaza response status=%d, body=%s",
                    response.status_code,
                    response.text[:500] if response.text else "empty",
                )

                if response.status_code == 200:
                    data = response.json()
                    return LogoutResponse(
                        success=True,
                        message=data.get("message", "Logged out successfully"),
                        redirect_url=data.get(
                            "post_logout_redirect_uri", post_logout_redirect
                        ),
                        front_channel_logout_url=data.get("front_channel_logout_url"),
                    )
                else:
                    logger.warning(
                        f"Kamiwaza logout returned {response.status_code}: {response.text}"
                    )
                    return LogoutResponse(
                        success=False,
                        message="Kamiwaza logout failed, but local session cleared",
                        redirect_url=post_logout_redirect,
                    )

        except Exception as e:
            logger.error(f"Failed to call Kamiwaza logout: {e}")
            return LogoutResponse(
                success=False,
                message="Kamiwaza logout failed, but local session cleared",
                redirect_url=post_logout_redirect,
            )

    return router
