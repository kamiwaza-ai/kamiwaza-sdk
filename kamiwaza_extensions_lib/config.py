"""Environment-based configuration for Kamiwaza extensions."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _read_verify_ssl() -> bool:
    """Read SSL verification setting from environment.

    Checks ``KAMIWAZA_VERIFY_SSL`` first (Python convention: "false"/"0" = off).
    Falls back to ``KAMIWAZA_TLS_REJECT_UNAUTHORIZED`` (Node.js convention:
    "0" = don't reject = don't verify, "1" = verify).
    """
    explicit = os.environ.get("KAMIWAZA_VERIFY_SSL")
    if explicit is not None:
        return explicit.lower() not in ("false", "0", "no")
    tls_reject = os.environ.get("KAMIWAZA_TLS_REJECT_UNAUTHORIZED")
    if tls_reject is not None:
        # "0" means don't reject unauthorized certs = don't verify
        return tls_reject.strip() != "0"
    return True


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
            verify_ssl=_read_verify_ssl(),
        )
