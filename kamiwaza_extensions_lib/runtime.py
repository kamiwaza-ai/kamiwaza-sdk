"""Runtime-path routing for App Garden extensions (Python side).

Mirrors the TypeScript contract in ``@kamiwaza-ai/extensions-lib``
(src/runtime/shared.ts + server.ts). The canonical behavior is defined by
``docs/extensions/runtime-path/routing-vectors.json``; both implementations
consume it in tests, so keep them in lockstep.

Environment is authoritative for deployment identity — request headers
(``x-forwarded-prefix``) may corroborate but never select the path.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from ipaddress import IPv6Address
from typing import Literal, Mapping
from urllib.parse import SplitResult, unquote_to_bytes, urlsplit

import idna

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_HOST_CONTROL_RE = re.compile(r"[\x00-\x20\x7f]")
_SENTINEL_FAMILY_RE = re.compile(r"__KZ_RUNTIME_BASE_[0-9A-F]+__")
_INVALID_HOST_CHARACTERS = frozenset("/\\?#@")
_MAX_PATH_LENGTH = 512
_MAX_SEGMENT_LENGTH = 128
_ASCII_C0_AND_SPACE = "".join(chr(value) for value in range(0x21))

__all__ = ["RuntimeRouting", "normalize_app_path", "with_app_path"]


def _is_invalid_segment(segment: str) -> bool:
    if segment in ("", ".", ".."):
        return True
    if "%" in segment:
        return True
    if len(segment) > _MAX_SEGMENT_LENGTH:
        return True
    return _SEGMENT_RE.fullmatch(segment) is None


def _trim_and_validate_raw_path(value: str) -> str:
    if _CONTROL_RE.search(value):
        raise ValueError(f"invalid app path (control characters): {value!r}")
    # Only U+0020 is ignorable. Other Unicode whitespace is outside the
    # conservative ASCII grammar and must fail identically in Python and JS.
    trimmed = value.strip(" ")
    if len(trimmed) > _MAX_PATH_LENGTH:
        raise ValueError(
            f"invalid app path (exceeds maximum length {_MAX_PATH_LENGTH})"
        )
    return trimmed


def _normalize_nonempty_path(value: str, trimmed: str) -> str:
    with_leading = trimmed if trimmed.startswith("/") else f"/{trimmed}"
    without_trailing = with_leading.rstrip("/")
    segments = without_trailing.split("/")[1:]
    if any(_is_invalid_segment(segment) for segment in segments):
        raise ValueError(f"invalid app path: {value!r}")
    return without_trailing


def normalize_app_path(value: str | None) -> str:
    """Normalize an app path: one leading slash, no trailing slashes,
    conservative segment grammar, bounded length. Empty-ish input
    ("", "/", None) normalizes to "". Raises ``ValueError`` on invalid
    input — including raw control characters BEFORE trimming (a trailing
    newline in an env value is a misconfiguration, not whitespace).
    """
    if value is None:
        return ""
    trimmed = _trim_and_validate_raw_path(value)
    if trimmed in ("", "/"):
        return ""
    return _normalize_nonempty_path(value, trimmed)


def _is_root_relative(path: str) -> bool:
    if not path.startswith("/"):
        return False
    return not path.startswith("//")


def _has_app_prefix(path: str, prefix: str) -> bool:
    if path == prefix:
        return True
    if not path.startswith(prefix):
        return False
    return path[len(prefix)] in "/?#"


def with_app_path(path: str, app_path: str | None = None) -> str:
    """Prefix a root-relative same-app path with the app path.

    Idempotent and segment-boundary aware; absolute URLs, protocol-relative
    URLs, and non-root-relative paths are returned untouched.
    """
    prefix = normalize_app_path(app_path)
    if prefix == "":
        return path
    if path == "":
        return path
    if not _is_root_relative(path):
        return path
    # Boundary-aware already-prefixed check: '/', '?', '#', or end-of-string
    # after the exact prefix all count.
    if _has_app_prefix(path, prefix):
        return path
    if path == "/":
        return prefix
    return f"{prefix}{path}"


def _trim_trailing_slash(value: str) -> str:
    return value.rstrip("/")


def _origin_with_app_path(value: str, app_path: str) -> str:
    return f"{_normalized_http_origin(value)}{app_path}"


def _invalid_public_url(value: str) -> ValueError:
    return ValueError(f"invalid public app URL for path routing: {value!r}")


def _parse_http_origin(value: str) -> tuple[SplitResult, str, str, int | None]:
    candidate = value.strip(_ASCII_C0_AND_SPACE)
    # WHATWG treats backslashes as path separators for special schemes,
    # while ``urlsplit`` can interpret one before ``@`` as userinfo and select
    # a different host. Reject the ambiguous spelling in both runtimes.
    if "\\" in candidate:
        raise _invalid_public_url(value)
    parsed = urlsplit(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise _invalid_public_url(value)
    hostname = parsed.hostname
    if not hostname:
        raise _invalid_public_url(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise _invalid_public_url(value) from exc
    return parsed, scheme, hostname, port


def _assert_valid_decoded_host(decoded_host: str) -> None:
    if _HOST_CONTROL_RE.search(decoded_host):
        raise ValueError
    if any(char in _INVALID_HOST_CHARACTERS for char in decoded_host):
        raise ValueError


def _format_decoded_host(decoded_host: str) -> str:
    if ":" in decoded_host:
        return f"[{IPv6Address(decoded_host).compressed}]"
    return idna.encode(
        decoded_host,
        uts46=True,
        transitional=False,
    ).decode("ascii")


def _normalized_hostname(hostname: str, value: str) -> str:
    try:
        decoded_host = unquote_to_bytes(hostname).decode("utf-8")
        _assert_valid_decoded_host(decoded_host)
        return _format_decoded_host(decoded_host)
    except (UnicodeError, ValueError, idna.IDNAError) as exc:
        raise _invalid_public_url(value) from exc


def _port_suffix(scheme: str, port: int | None) -> str:
    if port is None:
        return ""
    default_port = 443 if scheme == "https" else 80
    if port == default_port:
        return ""
    return f":{port}"


def _normalized_http_origin(value: str) -> str:
    """Normalize a browser-visible HTTP origin conservatively.

    Match the JavaScript ``URL`` behavior relied on by the TypeScript runtime
    for outer C0/space trimming, userinfo removal, IDN/percent-host conversion,
    default ports, and casing. Inputs outside that supported absolute-URL
    grammar fail closed instead of emitting a malformed auth redirect.
    """
    _, scheme, hostname, port = _parse_http_origin(value)
    normalized_host = _normalized_hostname(hostname, value)
    return f"{scheme}://{normalized_host}{_port_suffix(scheme, port)}"


def _has_unexpected_url_components(parsed: SplitResult) -> bool:
    if parsed.username is not None:
        return True
    if parsed.password is not None:
        return True
    if parsed.query:
        return True
    return bool(parsed.fragment)


def _normalized_app_path_url(value: str, app_path: str) -> str:
    """Validate and canonicalize the platform's full public path URL."""
    candidate = value.strip(_ASCII_C0_AND_SPACE)
    origin = _normalized_http_origin(candidate)
    parsed = urlsplit(candidate)
    if _has_unexpected_url_components(parsed):
        raise ValueError(
            "KAMIWAZA_APP_PATH_URL must be the public origin plus "
            f"KAMIWAZA_APP_PATH: {value!r}"
        )
    if _trim_trailing_slash(parsed.path) != app_path:
        raise ValueError(
            "KAMIWAZA_APP_PATH_URL must be the public origin plus "
            f"KAMIWAZA_APP_PATH: {value!r}"
        )
    return f"{origin}{app_path}"


def _assert_supported_mode(mode: str) -> None:
    if mode in ("", "path", "port"):
        return
    raise ValueError(f"unknown KAMIWAZA_ROUTING_MODE: {mode!r}")


def _assert_not_relocation_sentinel(app_path: str) -> None:
    if _SENTINEL_FAMILY_RE.search(app_path):
        raise ValueError("KAMIWAZA_APP_PATH must not contain the relocation sentinel")


def _resolve_mode_and_path(
    env: Mapping[str, str],
) -> tuple[Literal["path", "port"], str]:
    mode = env.get("KAMIWAZA_ROUTING_MODE", "")
    _assert_supported_mode(mode)
    if mode == "port":
        return "port", ""

    app_path = normalize_app_path(env.get("KAMIWAZA_APP_PATH", ""))
    _assert_not_relocation_sentinel(app_path)
    if app_path:
        return "path", app_path
    if mode == "path":
        raise ValueError("path routing mode requires a nonempty KAMIWAZA_APP_PATH")
    return "port", ""


def _resolve_app_urls(env: Mapping[str, str], app_path: str) -> tuple[str, str]:
    if not app_path:
        return "", _trim_trailing_slash(env.get("KAMIWAZA_APP_URL", ""))
    configured_app_path_url = _trim_trailing_slash(env.get("KAMIWAZA_APP_PATH_URL", ""))
    app_path_url = (
        _normalized_app_path_url(configured_app_path_url, app_path)
        if configured_app_path_url
        else ""
    )
    configured_app_url = _trim_trailing_slash(env.get("KAMIWAZA_APP_URL", ""))
    origin = _trim_trailing_slash(env.get("KAMIWAZA_ORIGIN", ""))
    public_origin = configured_app_url or origin
    app_url = app_path_url
    if not app_url and public_origin:
        app_url = _origin_with_app_path(public_origin, app_path)
    return app_path_url, app_url


@dataclass(frozen=True)
class RuntimeRouting:
    """Deployment routing identity, resolved once from the environment."""

    routing_mode: Literal["path", "port"]
    app_path: str
    app_path_url: str
    app_url: str
    deployment_id: str
    app_port: str

    @property
    def root_path(self) -> str:
        """ASGI ``root_path`` for Uvicorn (empty in port mode)."""
        return self.app_path

    @property
    def cookie_path(self) -> str:
        """Cookie ``Path`` scope: the app prefix in path mode, ``/`` in
        port mode. This avoids accidental cross-deployment name collisions;
        cookie paths are not a security boundary between same-origin apps."""
        return self.app_path or "/"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimeRouting":
        if env is None:
            env = os.environ

        routing_mode, app_path = _resolve_mode_and_path(env)
        app_path_url, app_url = _resolve_app_urls(env, app_path)

        return cls(
            routing_mode=routing_mode,
            app_path=app_path,
            app_path_url=app_path_url,
            app_url=app_url,
            deployment_id=env.get("KAMIWAZA_DEPLOYMENT_ID", ""),
            app_port=env.get("KAMIWAZA_APP_PORT", ""),
        )
