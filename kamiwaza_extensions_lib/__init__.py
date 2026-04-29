"""Kamiwaza extension runtime library.

Provides auth middleware, identity extraction, model client helpers,
and session management for Kamiwaza extensions (FastAPI backends).

This is intentionally separate from kamiwaza-sdk. Extensions need a
lightweight async library — not the full SDK with its sync HTTP client
and 20+ service modules.
"""

__version__ = "0.3.0"

from .auth import forward_auth_headers, require_auth, require_role
from .client import KamiwazaExtClient
from .config import AuthConfig
from .errors import (
    KamiwazaRuntimeError,
    MisboundAuthError,
    OutOfEnvelopeAccessError,
    PlatformOutageError,
    UnexpectedContextError,
)
from .identity import (
    Identity,
    anonymous_identity,
    extract_identity,
    get_identity,
    identity_from_headers,
)
from .models import AvailableModel, get_model_client, list_available_models
from .session import create_session_router

__all__ = [
    "AuthConfig",
    "AvailableModel",
    "require_auth",
    "require_role",
    "forward_auth_headers",
    "create_session_router",
    "KamiwazaExtClient",
    "Identity",
    "identity_from_headers",
    "get_identity",
    "extract_identity",
    "anonymous_identity",
    "KamiwazaRuntimeError",
    "MisboundAuthError",
    "UnexpectedContextError",
    "OutOfEnvelopeAccessError",
    "PlatformOutageError",
    "get_model_client",
    "list_available_models",
]
