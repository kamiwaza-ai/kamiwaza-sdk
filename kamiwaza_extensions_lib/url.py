"""URL helpers for registered platform routes and callback transports.

Registered URLs define HTTP Host, path, TLS authority, and authorization
context. ``KAMIWAZA_PLATFORM_GATEWAY_URL`` may replace only the connection
origin used by extension backend callbacks.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ._headers import has_http_control_character
from .config import AuthConfig


def _strip_api_suffix(url: str) -> str:
    """Strip a trailing ``/api`` (and any extra slashes). Empty → empty.

    Trailing-slash variants normalize identically — ``"…/api"`` and
    ``"…/api/"`` both produce the same output. Round-10 collapsed the
    sibling ``local_dev.public_api_url_from`` helper into this single
    source of truth (the prior helper only existed because the env
    overlay wrongly stripped ``/api`` before exporting; that path now
    keeps the raw URL and the helper had no other callers).
    """
    if not url:
        return ""
    return url.rstrip("/").removesuffix("/api").rstrip("/")


@dataclass(frozen=True)
class CallbackTarget:
    """Connection URL plus authority retained from a registered route."""

    url: str
    host: str
    server_name: str

    @property
    def extensions(self) -> dict[str, str] | None:
        """Return HTTPX TLS authority metadata when available."""
        if not self.server_name:
            return None
        return {"sni_hostname": self.server_name}


def _http_url(raw: str, *, origin_only: bool) -> httpx.URL:
    value = raw.strip()
    try:
        url = httpx.URL(value)
        port = url.port
    except (httpx.InvalidURL, ValueError) as exc:
        raise ValueError("invalid HTTP URL") from exc
    invalid = any(
        (
            not value,
            url.scheme not in {"http", "https"},
            not url.host,
            port is not None and not 1 <= port <= 65535,
            bool(url.userinfo),
            bool(url.query),
            bool(url.fragment),
            has_http_control_character(value),
            origin_only and url.path not in {"", "/"},
        )
    )
    if invalid:
        raise ValueError("invalid HTTP URL")
    return url


def callback_target(
    registered_url: str,
    transport_origin: str = "",
) -> CallbackTarget:
    """Dial ``transport_origin`` while retaining registered route authority."""
    registered = _http_url(registered_url, origin_only=False)
    transport = (
        _http_url(transport_origin, origin_only=True)
        if transport_origin.strip()
        else registered
    )
    target = registered.copy_with(
        scheme=transport.scheme,
        host=transport.host,
        port=transport.port,
    )
    return CallbackTarget(
        url=str(target),
        host=registered.netloc.decode("ascii"),
        server_name=registered.host,
    )


def registered_api_url(config: AuthConfig) -> str:
    """Return public registered API URL without direct-Core fallback."""
    if config.public_api_url:
        return config.public_api_url.strip()
    if config.origin:
        return f"{config.origin.strip().rstrip('/')}/api"
    return ""


def public_base_url(config: AuthConfig) -> str:
    """Return browser-facing base URL with legacy API fallback."""
    return _strip_api_suffix(registered_api_url(config) or config.api_url)


def backend_runtime_base(config: AuthConfig) -> str:
    """Return legacy container-routable runtime base during transition."""
    return _strip_api_suffix(config.api_url or registered_api_url(config))
