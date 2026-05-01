"""Local-dev auth bridge helpers (ENG-4318).

Used by `kz-ext dev local --auth` to forward the developer's real identity
from their active `kz-ext login` connection into the local Docker container.

Design rationale lives in the D210 PRD-lite: identity must be the developer's
real bearer (not a synthetic dev user), gated explicitly, and fail-loud when
no usable connection exists.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, urlunparse

from kamiwaza_extensions.connections import ConnectionManager

from ._jwt import decode_jwt_exp, decode_jwt_payload

_HOST_DOCKER_INTERNAL = "host.docker.internal"
# TLDs that are reserved for local / loopback use per RFC 6761 / 6762.
_LOOPBACK_TLDS = (".test", ".local")

# Default DNS-resolution timeout for the loopback heuristic (seconds).
# Without this cap, kz-ext dev local --auth could block 5–30s on slow or
# captive networks before the user gets feedback.
_DNS_TIMEOUT_S = 2.0

# Env var names the bridge writes into the container. Listed here once so
# the runner can scrub them from os.environ when --auth is NOT set (defense
# in depth: a stale shell export must not accidentally activate the bridge
# or expose a stale token).
GATE_ENV = "KZ_EXT_DEV_LOCAL_AUTH"
TOKEN_ENV = "KAMIWAZA_BEARER_TOKEN"
WORKROOM_ENV = "KAMIWAZA_DEV_WORKROOM_ID"
BRIDGE_ENV_VARS = (GATE_ENV, TOKEN_ENV, WORKROOM_ENV)


class LocalDevAuthError(Exception):
    """Raised when the `--auth` bridge cannot be set up.

    The message is developer-facing and tells the user which `kz-ext login`
    step to run next. Callers should surface it directly and exit non-zero.
    """


@dataclass
class BridgeContext:
    """Material the dev_local runner injects into the compose env.

    Trimmed to what callers actually consume — earlier drafts carried
    redundant URL fields that drifted from the runner-side rewrite logic
    (parallel-truth-source risk).
    """

    bearer_token: str
    user_id: str  # JWT sub — required; bridge fails-loud upstream when missing
    expires_at: Optional[int]  # JWT exp (unix seconds), if present


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


def _default_resolver(host: str) -> str:
    """Resolve ``host`` with a real wall-clock timeout so a captive or
    slow network can't block ``kz-ext dev local --auth`` startup for tens
    of seconds.

    PR #87 round-3 review fix: an earlier draft used
    ``socket.setdefaulttimeout(_DNS_TIMEOUT_S)`` around
    ``socket.gethostbyname`` — that is a no-op for the OS resolver path,
    which goes through libc's ``getaddrinfo`` and ignores the Python-side
    default timeout. We now run the lookup in a daemon thread and cap
    wait time with ``Thread.join(timeout=…)``. The thread is daemonized
    so it dies with the process if the resolver hangs forever — no
    cleanup overhead, since this codepath only runs during one-shot
    ``kz-ext dev local --auth`` startup.

    Round-11 review (codex GH High) fix: switched from
    ``socket.gethostbyname`` (IPv4-only) to ``socket.getaddrinfo`` so
    AAAA-only hosts (real Kamiwaza deployments that publish only IPv6
    records) resolve correctly. Without this, an IPv6-only platform
    hostname raised ``gaierror`` here → ``is_loopback_url`` treated it
    as "unresolvable from host" → ``build_compose_extra_hosts`` mapped
    the remote name to ``host-gateway``, silently routing platform
    traffic to the developer's machine instead of the actual server.
    """
    result: list[str] = []
    error: list[BaseException] = []

    def _run() -> None:
        try:
            # ``AF_UNSPEC`` admits both A and AAAA records. We only need
            # one address back to populate the loopback heuristic; the
            # IP itself is what callers compare against
            # ``ipaddress.ip_address.is_loopback``. Take the first
            # answer regardless of family — both v4 loopback (127/8)
            # and v6 loopback (::1) parse correctly.
            infos = socket.getaddrinfo(
                host, None, socket.AF_UNSPEC, socket.SOCK_STREAM,
            )
            if not infos:
                raise OSError(f"no addresses returned for {host!r}")
            sockaddr = infos[0][4]
            result.append(sockaddr[0])
        except BaseException as exc:  # noqa: BLE001 — propagate to caller
            error.append(exc)

    thread = threading.Thread(
        target=_run, name=f"kz-dns-{host}", daemon=True,
    )
    thread.start()
    thread.join(timeout=_DNS_TIMEOUT_S)
    if thread.is_alive():
        # Thread is still running — translate to OSError so callers
        # (`is_loopback_url`) treat the timeout as "unresolvable from
        # host", which is the same outcome as a real NXDOMAIN and feeds
        # the loopback heuristic correctly. The thread is daemonized so
        # it will die with the process.
        raise OSError(
            f"DNS lookup for {host!r} exceeded {_DNS_TIMEOUT_S}s"
        )
    if error:
        # Re-raise the original resolver error so OSError-based callers
        # see exactly what they would have seen without the timeout.
        raise error[0]
    return result[0]


def _is_loopback_ip(host: str) -> Optional[bool]:
    """True/False if ``host`` parses as an IP literal, else None.

    ``ip_address.is_loopback`` covers the full ``127.0.0.0/8`` range and
    IPv6 ``::1`` — broader than the previous string-set check that only
    matched ``127.0.0.1`` exactly.
    """
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return None


def is_loopback_url(
    url: str,
    *,
    resolver=None,
) -> bool:
    """Return True if `url`'s hostname is loopback or unresolvable from a
    container.

    Triggers:
      - bare loopbacks: ``localhost`` (string), any IP in ``127.0.0.0/8`` /
        IPv6 ``::1`` (via ``ipaddress.is_loopback``)
      - reserved TLDs: ``*.test``, ``*.local``
      - any hostname that fails to resolve via host DNS

    A non-loopback IP literal (e.g. ``1.2.3.4``) is never a loopback URL
    even if DNS lookup hits a NXDOMAIN — IPs don't need DNS.

    ``resolver`` is injectable for tests; defaults to a timeout-capped
    wrapper around ``socket.gethostbyname``.
    """
    host = _hostname(url)
    if host is None:
        return False
    if host == "localhost":
        return True
    if host.endswith(_LOOPBACK_TLDS):
        return True
    # IP literal? Use the full loopback range (127.0.0.0/8 + ::1).
    ip_loopback = _is_loopback_ip(host)
    if ip_loopback is True:
        return True
    if ip_loopback is False:
        return False
    # Hostname — try to resolve. Unresolvable means the developer has it
    # mapped via /etc/hosts on the host but containers can't see that.
    try:
        (resolver or _default_resolver)(host)
        return False
    except OSError:
        return True


def is_bare_loopback(host: Optional[str]) -> bool:
    """True if ``host`` is a bare-loopback address (no TLS-cert binding).

    Bare loopbacks (``localhost``, anything in ``127.0.0.0/8``, ``::1``)
    are safe to rewrite to ``host.docker.internal`` because they have no
    cert binding. Named hostnames (``kamiwaza.test``) must be preserved
    so TLS SNI keeps matching the developer's host certificate.
    """
    if host is None:
        return False
    if host == "localhost":
        return True
    return _is_loopback_ip(host) is True


def rewrite_bare_loopback_url(url: str) -> str:
    """Rewrite ``localhost`` / any ``127.0.0.0/8`` IP / ``::1`` →
    ``host.docker.internal``.

    Named hostnames (``kamiwaza.test``, custom CNAMEs) are preserved
    unchanged so that TLS SNI matches the developer's host certificate.
    """
    host = _hostname(url)
    if not is_bare_loopback(host):
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
    resolver=None,
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
    if is_bare_loopback(host):
        # Bare loopbacks are handled by URL rewrite — no extra_hosts entry.
        return []
    if not is_loopback_url(url, resolver=resolver):
        return []
    # Named loopback hostname — strip brackets if it's a (rare) bracketed v6.
    return [f"{_strip_brackets(host)}:host-gateway"]


def prepare_bridge_context(
    connection_manager: Optional[ConnectionManager] = None,
) -> BridgeContext:
    """Validate the active `kz-ext login` connection and return bridge material.

    Raises ``LocalDevAuthError`` when:
      - no active connection (``run `kz-ext login` first``)
      - active connection has no stored bearer
      - JWT ``exp`` is in the past
      - bearer is not a JWT (no ``sub`` claim) — opaque PATs / API keys
        cannot drive the bridge because the TS middleware needs ``sub`` to
        synthesize ``x-user-id``. Earlier drafts accepted such tokens and
        produced a silent no-op auth path; we now fail-loud upstream so
        the developer sees a clear hint instead of hunting through 401s.
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

    # Round-11 review (Comprehensive M, Claude M) — both the JSON+base64
    # decode AND the NumericDate int-coercion now live in ``_jwt``; this
    # path no longer carries its own copy. Decode the payload once for
    # the ``sub`` lookup below; ``decode_jwt_exp`` re-decodes (cheap —
    # ~µs) but keeps the public-helper boundary clean.
    claims = decode_jwt_payload(bearer)
    exp = decode_jwt_exp(bearer)
    if exp is not None and exp <= int(time.time()):
        when = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
        raise LocalDevAuthError(
            f"bearer token expired at {when} — run `kz-ext login` again"
        )

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise LocalDevAuthError(
            f"active connection '{connection.name}' bearer is not a JWT "
            "with a usable `sub` claim — `kz-ext dev local --auth` requires "
            "an interactive login (try `kz-ext login` without `--api-key`)"
        )

    return BridgeContext(
        bearer_token=bearer,
        user_id=sub,
        expires_at=exp,
    )
