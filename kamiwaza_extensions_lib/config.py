"""Environment-based configuration for Kamiwaza extensions."""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import UnexpectedContextError


def _path_mode_public_url(
    path_app_url: str, legacy_app_url: str, origin: str, app_path: str
) -> str:
    if path_app_url:
        return path_app_url
    public_origin = legacy_app_url or origin
    normalized_path = "/" + app_path.strip(" ").strip("/")
    if not public_origin or normalized_path == "/":
        return ""
    parsed = urlsplit(public_origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}{normalized_path}"


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
    ca_bundle: str = ""

    @classmethod
    def from_env(cls) -> AuthConfig:
        """Read configuration from environment variables."""
        app_path = os.environ.get("KAMIWAZA_APP_PATH", "")
        routing_mode = os.environ.get("KAMIWAZA_ROUTING_MODE", "")
        legacy_app_url = os.environ.get("KAMIWAZA_APP_URL", "").rstrip("/")
        path_app_url = os.environ.get("KAMIWAZA_APP_PATH_URL", "").rstrip("/")
        origin = os.environ.get("KAMIWAZA_ORIGIN", "").rstrip("/")
        # AuthConfig remains non-throwing and cheap on request paths. The ASGI
        # launcher validates the full routing contract once at startup; here
        # we only apply the path-mode public-URL precedence needed by auth
        # redirects while preserving legacy behavior for port mode.
        path_mode = routing_mode == "path" or (routing_mode == "" and bool(app_path))
        app_url = (
            _path_mode_public_url(path_app_url, legacy_app_url, origin, app_path)
            if path_mode
            else legacy_app_url
        )
        return cls(
            api_url=os.environ.get("KAMIWAZA_API_URL", ""),
            public_api_url=os.environ.get("KAMIWAZA_PUBLIC_API_URL", ""),
            openai_base=os.environ.get("KAMIWAZA_ENDPOINT", "")
            or os.environ.get("KAMIWAZA_MODEL_URL", ""),
            app_url=app_url,
            app_path=app_path,
            app_name=os.environ.get("KAMIWAZA_APP_NAME", ""),
            use_auth=os.environ.get("KAMIWAZA_USE_AUTH", "true").lower()
            not in ("false", "0", "no"),
            origin=os.environ.get("KAMIWAZA_ORIGIN", ""),
            api_key=os.environ.get("KAMIWAZA_API_KEY", ""),
            verify_ssl=_read_verify_ssl(),
            ca_bundle=os.environ.get("KAMIWAZA_CA_BUNDLE", "").strip(),
        )

    def httpx_verify(self) -> bool | ssl.SSLContext:
        """Return explicit TLS verification state for proxy-isolated clients."""
        if not self.verify_ssl:
            return False
        if self.ca_bundle:
            try:
                return ssl.create_default_context(cafile=self.ca_bundle)
            except OSError as exc:
                raise UnexpectedContextError(
                    "KAMIWAZA_CA_BUNDLE is not a readable PEM trust bundle"
                ) from exc
        return True
