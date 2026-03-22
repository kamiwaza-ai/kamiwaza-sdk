"""Configuration for Kamiwaza authentication."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _is_falsey(value: str | None) -> bool:
    """Check if a string value represents a falsey boolean."""
    return (value or "").lower() in {"0", "false", "no", "off", ""}


@dataclass
class AuthConfig:
    """Configuration for Kamiwaza authentication.

    Attributes:
        api_url: Base URL for Kamiwaza API (e.g., "https://localhost/api")
        validate_url: URL for auth validation endpoint
        use_auth: Whether authentication is enabled
        gateway_state_secret: Secret for signing auth gateway state
        signature_secret: Secret for forward auth signatures

    Example:
        config = AuthConfig.from_env()
        if config.use_auth:
            # Require authentication
            ...
    """

    api_url: str = field(
        default_factory=lambda: os.getenv(
            "KAMIWAZA_API_URL", "http://host.docker.internal:7777/api"
        ).rstrip("/")
    )

    validate_url: str = field(
        default_factory=lambda: os.getenv("AUTH_VALIDATE_URL", "")
    )

    use_auth: bool = field(
        default_factory=lambda: not _is_falsey(os.getenv("KAMIWAZA_USE_AUTH", "true"))
    )

    gateway_state_secret: str = field(
        default_factory=lambda: os.getenv("AUTH_GATEWAY_STATE_SECRET", "")
    )

    signature_secret: str = field(
        default_factory=lambda: os.getenv("FORWARDAUTH_SIGNATURE_SECRET", "")
    )

    @classmethod
    def from_env(cls) -> AuthConfig:
        """Create configuration from environment variables."""
        return cls()

    @property
    def effective_validate_url(self) -> str:
        """Get the effective validation URL, with fallback to api_url."""
        return self.validate_url or f"{self.api_url}/auth/validate"
