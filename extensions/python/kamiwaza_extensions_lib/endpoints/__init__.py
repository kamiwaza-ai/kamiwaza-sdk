"""Session management endpoints for Kamiwaza auth.

Provides a router factory for creating session management endpoints
that can be included in any FastAPI application.
"""

from .session import create_session_router

__all__ = ["create_session_router"]
