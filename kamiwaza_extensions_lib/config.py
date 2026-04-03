"""Environment-based configuration for Kamiwaza extensions."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class AuthConfig:
    """Configuration read from KAMIWAZA_* environment variables.

    All fields have safe defaults so the config object is always
    constructable — individual features check for required values
    at point-of-use.
    """

    api_url: str = ""
    public_api_url: str = ""
    openai_base: str = ""
    app_url: str = ""
    app_path: str = ""
    app_name: str = ""
    use_auth: bool = True
    origin: str = ""
    api_key: str = ""
    verify_ssl: bool = True

    @classmethod
    def from_env(cls) -> AuthConfig:
        """Read configuration from environment variables."""
        return cls(
            api_url=os.environ.get("KAMIWAZA_API_URL", ""),
            public_api_url=os.environ.get("KAMIWAZA_PUBLIC_API_URL", ""),
            openai_base=os.environ.get("KAMIWAZA_ENDPOINT", "")
            or os.environ.get("KAMIWAZA_MODEL_URL", ""),
            app_url=os.environ.get("KAMIWAZA_APP_URL", ""),
            app_path=os.environ.get("KAMIWAZA_APP_PATH", ""),
            app_name=os.environ.get("KAMIWAZA_APP_NAME", ""),
            use_auth=os.environ.get("KAMIWAZA_USE_AUTH", "true").lower()
            not in ("false", "0", "no"),
            origin=os.environ.get("KAMIWAZA_ORIGIN", ""),
            api_key=os.environ.get("KAMIWAZA_API_KEY", ""),
            verify_ssl=os.environ.get("KAMIWAZA_VERIFY_SSL", "true").lower()
            not in ("false", "0", "no"),
        )
