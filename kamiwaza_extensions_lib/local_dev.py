"""Local-dev auth bridge helpers (ENG-4318).

Used by `kz-ext dev local --auth` to forward the developer's real identity
from their active `kz-ext login` connection into the local Docker container.

Design rationale lives in the D210 PRD-lite: identity must be the developer's
real bearer (not a synthetic dev user), gated explicitly, and fail-loud when
no usable connection exists.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, urlunparse

from kamiwaza_extensions.connections import ConnectionManager

_HOST_DOCKER_INTERNAL = "host.docker.internal"
# TLDs that are reserved for local / loopback use per RFC 6761 / 6762.
_LOOPBACK_TLDS = (".test", ".local")
_BARE_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class LocalDevAuthError(Exception):
    """Raised when the `--auth` bridge cannot be set up.

    The message is developer-facing and tells the user which `kz-ext login`
    step to run next. Callers should surface it directly and exit non-zero.
    """


@dataclass
class BridgeContext:
    """Material the dev_local runner injects into the compose env."""

    bearer_token: str
    api_url: str
    public_api_url: str
    verify_ssl: bool
    expires_at: Optional[int]  # JWT exp (unix seconds), if present
    user_id: Optional[str]  # JWT sub, for diagnostics only


def _strip_brackets(host: str) -> str:
    """Strip IPv6 brackets if present (`[::1]` -> `::1`)."""
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def _hostname(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    host = parsed.hostname  # urlparse strips brackets and lowercases
    return host or None


_DEFAULT_RESOLVER = socket.gethostbyname


def is_loopback_url(
    url: str,
    *,
    resolver=_DEFAULT_RESOLVER,
) -> bool:
    """Return True if `url`'s hostname is loopback or unresolvable from a
    container.

    Triggers:
      - bare loopbacks: ``localhost``, ``127.0.0.1``, ``::1``
      - reserved TLDs: ``*.test``, ``*.local``
      - any hostname that fails to resolve via host DNS

    A non-loopback IPv4 (e.g. ``1.2.3.4``) is never a loopback URL even if
    DNS lookup hits a NXDOMAIN — IPs don't need DNS.

    ``resolver`` is injectable for tests; defaults to ``socket.gethostbyname``.
    """
    host = _hostname(url)
    if host is None:
        return False
    if host in _BARE_LOOPBACK_HOSTS:
        return True
    if host.endswith(_LOOPBACK_TLDS):
        return True
    # If it's an IP literal we don't need DNS — only loopback IPs matter.
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    # Hostname — try to resolve. Unresolvable means the developer has it
    # mapped via /etc/hosts on the host but containers can't see that.
    try:
        resolver(host)
        return False
    except OSError:
        return True


def rewrite_bare_loopback_url(url: str) -> str:
    """Rewrite `localhost` / `127.0.0.1` / `::1` → `host.docker.internal`.

    Named hostnames (`kamiwaza.test`, custom CNAMEs) are preserved unchanged
    so that TLS SNI matches the developer's host certificate.
    """
    host = _hostname(url)
    if host is None or host not in _BARE_LOOPBACK_HOSTS:
        return url
    parsed = urlparse(url)
    # Reconstruct netloc preserving port + auth.
    new_netloc = _HOST_DOCKER_INTERNAL
    if parsed.port is not None:
        new_netloc = f"{_HOST_DOCKER_INTERNAL}:{parsed.port}"
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        new_netloc = f"{userinfo}@{new_netloc}"
    return urlunparse(parsed._replace(netloc=new_netloc))


def extract_extra_hosts(
    url: str,
    *,
    resolver=_DEFAULT_RESOLVER,
) -> list[str]:
    """Return compose ``extra_hosts`` entries needed to reach `url` from
    inside a container.

    - Named loopbacks (`kamiwaza.test`, `dev.local`) → `["name:host-gateway"]`
      so DNS resolves to the host gateway IP.
    - Bare loopbacks (`localhost`, `127.0.0.1`) → `[]` because the URL
      rewrite to `host.docker.internal` already handles them.
    - Non-loopback URLs → `[]`.
    """
    host = _hostname(url)
    if host is None:
        return []
    if host in _BARE_LOOPBACK_HOSTS:
        return []
    if not is_loopback_url(url, resolver=resolver):
        return []
    # Named loopback hostname — strip brackets if it's a (rare) bracketed v6.
    return [f"{_strip_brackets(host)}:host-gateway"]


def _decode_jwt_claims(token: str) -> dict:
    """Decode JWT payload (no signature verification) into a dict.

    Returns an empty dict on any decode failure. Mirrors
    ``kamiwaza_extensions_lib.session._decode_jwt_exp`` — we trust the
    platform to verify the signature when the bearer is actually used.
    """
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{payload}{padding}")
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def prepare_bridge_context(
    connection_manager: Optional[ConnectionManager] = None,
) -> BridgeContext:
    """Validate the active `kz-ext login` connection and return bridge material.

    Raises ``LocalDevAuthError`` when:
      - no active connection (``run `kz-ext login` first``)
      - active connection has no stored bearer
      - JWT ``exp`` is in the past

    Tokens with no ``exp`` claim are accepted — they may be PATs or other
    non-expiring credentials. The platform validates the bearer at runtime
    (AC-9 negative-path covers that surface).
    """
    mgr = connection_manager or ConnectionManager()
    connection = mgr.get_active_connection()
    if connection is None:
        raise LocalDevAuthError(
            "no active Kamiwaza connection — run `kz-ext login` first"
        )

    stored = mgr.get_token(connection.name)
    bearer = stored.access_token if stored is not None else ""
    if not bearer:
        raise LocalDevAuthError(
            f"active connection '{connection.name}' has no stored bearer "
            "token — run `kz-ext login` again"
        )

    claims = _decode_jwt_claims(bearer)
    exp = _coerce_int(claims.get("exp"))
    if exp is not None and exp <= int(time.time()):
        when = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
        raise LocalDevAuthError(
            f"bearer token expired at {when} — run `kz-ext login` again"
        )

    sub = claims.get("sub")
    user_id = sub if isinstance(sub, str) and sub else None

    api_url = connection.url
    public_api_url = api_url.removesuffix("/api").rstrip("/")
    if not public_api_url:
        public_api_url = api_url

    return BridgeContext(
        bearer_token=bearer,
        api_url=api_url,
        public_api_url=public_api_url,
        verify_ssl=connection.verify_ssl,
        expires_at=exp,
        user_id=user_id,
    )
