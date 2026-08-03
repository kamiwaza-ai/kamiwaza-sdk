"""Configuration for the reusable S3-compatible object-storage client.

Environment variables only — no config file required.
Precedence: Constructor args > Environment variables > Defaults

Kamiwaza registry aliases are supported through KAMIWAZA_REGISTRY_* vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "kamiwaza" / "client"
DEFAULT_TOKEN_PATH = DEFAULT_CONFIG_DIR / "token.json"

# Environment variable names
ENV_ACCESS_KEY_ID = "R2_ACCESS_KEY_ID"
ENV_SECRET_ACCESS_KEY = "R2_SECRET_ACCESS_KEY"
ENV_ENDPOINT_URL = "R2_ENDPOINT_URL"
ENV_BROKER_URL = "R2_BROKER_URL"
ENV_ACCOUNT_ID = "R2_ACCOUNT_ID"
ENV_AUTH_NON_INTERACTIVE = "R2_AUTH_NON_INTERACTIVE"
ENV_AUTH_CALLBACK_TIMEOUT = "R2_AUTH_CALLBACK_TIMEOUT_SECONDS"

# Kamiwaza registry aliases
ENV_KAMIWAZA_REGISTRY_ENDPOINT = "KAMIWAZA_REGISTRY_ENDPOINT"
ENV_KAMIWAZA_REGISTRY_ACCOUNT_ID = "KAMIWAZA_REGISTRY_ACCOUNT_ID"


@dataclass
class R2Config:
    """R2 connection configuration."""

    account_id: str | None = None
    endpoint_url: str | None = None
    broker_url: str | None = None


@dataclass
class AuthConfig:
    """Authentication and local callback configuration."""

    token_cache_path: str | Path = field(
        default_factory=lambda: str(DEFAULT_TOKEN_PATH)
    )
    non_interactive: bool = False
    callback_timeout_seconds: float = 180.0


@dataclass
class DefaultsConfig:
    """Default values for client creation."""

    region: str = "auto"
    credential_ttl_seconds: int = 900


@dataclass
class Config:
    """Full object-storage client configuration.

    Loaded from environment variables only. No config file required.
    """

    r2: R2Config = field(default_factory=R2Config)
    auth: AuthConfig = field(default_factory=AuthConfig)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)

    @classmethod
    def load(cls) -> Config:
        """Load configuration from environment variables."""
        config = cls()
        config._apply_env()
        return config

    def _apply_env(self) -> None:
        """Apply environment variable configuration."""
        if account_id := os.environ.get(ENV_ACCOUNT_ID):
            self.r2.account_id = account_id

        endpoint = os.environ.get(ENV_ENDPOINT_URL) or os.environ.get(
            ENV_KAMIWAZA_REGISTRY_ENDPOINT
        )
        if endpoint:
            self.r2.endpoint_url = endpoint
        elif account_id := os.environ.get(ENV_KAMIWAZA_REGISTRY_ACCOUNT_ID):
            self.r2.endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
            self.r2.account_id = account_id

        if broker_url := os.environ.get(ENV_BROKER_URL):
            self.r2.broker_url = broker_url.rstrip("/")

        if non_interactive := os.environ.get(ENV_AUTH_NON_INTERACTIVE):
            self.auth.non_interactive = non_interactive.lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        if callback_timeout := os.environ.get(ENV_AUTH_CALLBACK_TIMEOUT):
            try:
                parsed_timeout = float(callback_timeout)
            except ValueError as exc:
                raise ValueError(
                    f"{ENV_AUTH_CALLBACK_TIMEOUT} must be a number"
                ) from exc
            if parsed_timeout <= 0:
                raise ValueError(
                    f"{ENV_AUTH_CALLBACK_TIMEOUT} must be greater than zero"
                )
            self.auth.callback_timeout_seconds = parsed_timeout

    def get_endpoint_url(self, override: str | None = None) -> str | None:
        """Get R2 endpoint URL with optional override."""
        if override:
            return override
        if self.r2.endpoint_url:
            return self.r2.endpoint_url
        if self.r2.account_id:
            return f"https://{self.r2.account_id}.r2.cloudflarestorage.com"
        return None
