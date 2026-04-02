"""Kamiwaza extension runtime library.

Provides auth middleware, identity extraction, model client helpers,
and session management for Kamiwaza extensions (FastAPI backends).

This is intentionally separate from kamiwaza-sdk. Extensions need a
lightweight async library — not the full SDK with its sync HTTP client
and 20+ service modules.
"""

__version__ = "0.1.0"

from .identity import Identity, get_identity
from .auth import require_auth, require_role, forward_auth_headers
from .session import create_session_router
from .config import AuthConfig
from .client import KamiwazaExtClient
from .models import AvailableModel, get_model_client, list_available_models

__all__ = [
    "Identity",
    "get_identity",
    "require_auth",
    "require_role",
    "forward_auth_headers",
    "create_session_router",
    "AuthConfig",
    "KamiwazaExtClient",
    "AvailableModel",
    "get_model_client",
    "list_available_models",
]
