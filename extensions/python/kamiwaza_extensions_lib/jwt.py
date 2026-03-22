"""JWT utilities for Kamiwaza auth.

These utilities extract claims from JWTs without verification,
since Kamiwaza has already validated tokens through forward auth.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import jwt

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

# Default maximum session duration: 8 hours
MAX_SESSION_SECONDS = 28800


def decode_jwt_claims(token: str) -> dict:
    """Decode JWT to extract claims without signature verification.

    Kamiwaza has already validated the token through forward auth,
    so we just need to extract the claims for session management.

    Args:
        token: JWT token string

    Returns:
        Dictionary of JWT claims, or empty dict if decoding fails

    Example:
        token = request.cookies.get("access_token")
        if token:
            claims = decode_jwt_claims(token)
            user_id = claims.get("sub")
            email = claims.get("email")
    """
    try:
        # Decode without verification - Kamiwaza already validated
        claims = jwt.decode(token, options={"verify_signature": False})
        return claims
    except jwt.DecodeError as e:
        logger.warning(f"Failed to decode JWT: {e}")
        return {}
    except Exception as e:
        logger.warning(f"Unexpected error decoding JWT: {e}")
        return {}


def extract_token_from_request(request: Request) -> str | None:
    """Extract JWT token from request cookies or headers.

    Checks the access_token cookie first, then falls back to
    the Authorization header with Bearer scheme.

    Args:
        request: FastAPI request object

    Returns:
        JWT token string, or None if not found

    Example:
        token = extract_token_from_request(request)
        if token:
            claims = decode_jwt_claims(token)
    """
    # Try cookie first
    token = request.cookies.get("access_token")
    if token:
        return token

    # Try Authorization header
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None


def calculate_session_expires_at(
    request: Request,
    max_session_seconds: int = MAX_SESSION_SECONDS,
) -> int | None:
    """Calculate session expiry timestamp from JWT iat claim.

    Uses the JWT's iat (issued at) claim plus a maximum session
    duration to determine when the session expires.

    Args:
        request: FastAPI request object
        max_session_seconds: Maximum session duration (default: 28800 = 8 hours)

    Returns:
        Unix timestamp of session expiry, or None if no valid token

    Example:
        expires_at = calculate_session_expires_at(request)
        if expires_at:
            response["session_expires_at"] = expires_at
    """
    token = extract_token_from_request(request)
    if not token:
        return None

    claims = decode_jwt_claims(token)
    iat = claims.get("iat")
    if iat:
        return int(iat) + max_session_seconds

    # Fallback: use current time + max session if no iat
    # This is less accurate but provides a reasonable default
    return int(time.time()) + max_session_seconds
