"""Kamiwaza Forward Auth utilities for FastAPI backends.

This module provides utilities for handling Kamiwaza platform authentication
in App Garden applications using Traefik forward auth.

Example usage:
    from kamiwaza_extensions_lib import Identity, get_identity, require_auth, KamiwazaClient

    @app.get("/api/protected")
    async def protected_endpoint(identity: Identity = Depends(require_auth)):
        return {"user": identity.email}

Example with session router:
    from kamiwaza_extensions_lib import create_session_router

    app = FastAPI()
    app.include_router(create_session_router())  # Adds /session, /auth/login-url, /auth/logout
"""

from .client import KamiwazaClient, forward_auth_headers
from .config import AuthConfig
from .endpoints import create_session_router
from .errors import SessionExpiredError, UpstreamAuthError
from .identity import Identity, get_identity, require_auth
from .jwt import (
    MAX_SESSION_SECONDS,
    calculate_session_expires_at,
    decode_jwt_claims,
    extract_token_from_request,
)

__all__ = [
    "MAX_SESSION_SECONDS",
    # Config
    "AuthConfig",
    # Identity
    "Identity",
    # Client
    "KamiwazaClient",
    # Errors
    "SessionExpiredError",
    "UpstreamAuthError",
    "calculate_session_expires_at",
    # Session endpoints
    "create_session_router",
    # JWT utilities
    "decode_jwt_claims",
    "extract_token_from_request",
    "forward_auth_headers",
    "get_identity",
    "require_auth",
]
