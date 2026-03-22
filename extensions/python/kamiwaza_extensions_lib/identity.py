"""Identity management for Kamiwaza forward auth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request

from .client import KamiwazaClient
from .config import AuthConfig

if TYPE_CHECKING:
    pass


def _split_roles(raw: str | None) -> list[str]:
    """Parse comma-separated roles string into a list."""
    if not raw:
        return []
    return [r.strip() for r in raw.split(",") if r.strip()]


@dataclass
class Identity:
    """Represents an authenticated user identity.

    Attributes:
        user_id: Unique identifier for the user
        email: User's email address
        name: User's display name
        roles: List of roles assigned to the user
        request_id: Unique identifier for the current request

    Example:
        identity = await get_identity(request)
        if identity.user_id:
            print(f"Authenticated as {identity.email}")
    """

    user_id: str | None
    email: str | None
    name: str | None
    roles: list[str]
    request_id: str | None

    @property
    def is_authenticated(self) -> bool:
        """Check if this identity represents an authenticated user."""
        return bool(self.user_id and self.email)

    @property
    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return "admin" in self.roles


def anonymous_identity(request_id: str | None = None) -> Identity:
    """Create an anonymous (unauthenticated) identity."""
    return Identity(
        user_id="anonymous",
        email="anonymous@local",
        name="Anonymous",
        roles=[],
        request_id=request_id,
    )


async def get_identity(
    request: Request,
    config: AuthConfig | None = None,
) -> Identity:
    """Extract user identity from request headers or validate with API.

    This function first checks for forwarded headers from Traefik forward auth:
    - x-user-id
    - x-user-email
    - x-user-name
    - x-user-roles (comma-separated)

    If forwarded headers are not present, it falls back to calling the
    Kamiwaza auth/validate endpoint to verify the session.

    Args:
        request: The FastAPI request object
        config: Optional AuthConfig, defaults to AuthConfig.from_env()

    Returns:
        Identity object with user information, or unauthenticated identity
        if validation fails.

    Example:
        @app.get("/api/me")
        async def get_me(request: Request):
            identity = await get_identity(request)
            if not identity.is_authenticated:
                raise HTTPException(401, "Not authenticated")
            return {"email": identity.email}
    """
    config = config or AuthConfig.from_env()
    request_id = request.headers.get("x-request-id")

    # Check for forwarded headers from Traefik forward auth
    user_id = request.headers.get("x-user-id")
    email = request.headers.get("x-user-email")
    name = request.headers.get("x-user-name")
    roles = _split_roles(request.headers.get("x-user-roles"))

    if user_id and email:
        return Identity(
            user_id=user_id,
            email=email,
            name=name,
            roles=roles,
            request_id=request_id,
        )

    # Fallback: validate via Kamiwaza API
    client = KamiwazaClient.from_config(config)
    try:
        data = await client.validate(request.headers)
    except Exception:
        data = None

    if not data:
        return Identity(
            user_id=None,
            email=None,
            name=None,
            roles=[],
            request_id=request_id,
        )

    return Identity(
        user_id=data.get("user_id") or data.get("id"),
        email=data.get("email"),
        name=data.get("name"),
        roles=data.get("roles") or [],
        request_id=request_id,
    )


async def require_auth(request: Request) -> Identity:
    """FastAPI dependency that requires authentication.

    Raises HTTPException 401 if the user is not authenticated.

    Example:
        @app.get("/api/protected")
        async def protected(identity: Identity = Depends(require_auth)):
            return {"user": identity.email}
    """
    identity = await get_identity(request)
    if not identity.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return identity


def require_role(role: str):
    """Create a FastAPI dependency that requires a specific role.

    Args:
        role: The role name to require

    Returns:
        A dependency function that validates the user has the required role

    Example:
        @app.get("/api/admin")
        async def admin_only(identity: Identity = Depends(require_role("admin"))):
            return {"admin": identity.email}
    """

    async def _require_role(identity: Identity = Depends(require_auth)) -> Identity:
        if role not in identity.roles:
            raise HTTPException(status_code=403, detail=f"Role '{role}' required")
        return identity

    return _require_role
