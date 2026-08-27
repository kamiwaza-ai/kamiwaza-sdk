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
from typing import Literal, Mapping

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_PATH_LENGTH = 512
_MAX_SEGMENT_LENGTH = 128

__all__ = ["RuntimeRouting", "normalize_app_path", "with_app_path"]


def normalize_app_path(value: str | None) -> str:
    """Normalize an app path: one leading slash, no trailing slashes,
    conservative segment grammar, bounded length. Empty-ish input
    ("", "/", None) normalizes to "". Raises ``ValueError`` on invalid
    input — including raw control characters BEFORE trimming (a trailing
    newline in an env value is a misconfiguration, not whitespace).
    """
    if value is None:
        return ""
    if _CONTROL_RE.search(value):
        raise ValueError(f"invalid app path (control characters): {value!r}")
    trimmed = value.strip()
    if trimmed in ("", "/"):
        return ""
    if len(trimmed) > _MAX_PATH_LENGTH:
        raise ValueError(
            f"invalid app path (exceeds maximum length {_MAX_PATH_LENGTH})"
        )

    with_leading = trimmed if trimmed.startswith("/") else f"/{trimmed}"
    without_trailing = with_leading.rstrip("/")

    for segment in without_trailing.split("/")[1:]:
        if (
            segment in ("", ".", "..")
            or "%" in segment
            or len(segment) > _MAX_SEGMENT_LENGTH
            or not _SEGMENT_RE.match(segment)
        ):
            raise ValueError(f"invalid app path: {value!r}")

    return without_trailing


def with_app_path(path: str, app_path: str | None = None) -> str:
    """Prefix a root-relative same-app path with the app path.

    Idempotent and segment-boundary aware; absolute URLs, protocol-relative
    URLs, and non-root-relative paths are returned untouched.
    """
    prefix = normalize_app_path(app_path)
    if prefix == "" or path == "":
        return path
    if not path.startswith("/") or path.startswith("//"):
        return path
    # Boundary-aware already-prefixed check: '/', '?', '#', or end-of-string
    # after the exact prefix all count.
    if path == prefix:
        return path
    if path.startswith(prefix) and path[len(prefix)] in "/?#":
        return path
    if path == "/":
        return prefix
    return f"{prefix}{path}"


def _trim_trailing_slash(value: str) -> str:
    return value.rstrip("/")


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
        port mode — prevents one App Garden deployment's cookies from
        shadowing another's on the same host."""
        return self.app_path or "/"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimeRouting":
        if env is None:
            env = os.environ

        mode = env.get("KAMIWAZA_ROUTING_MODE", "")
        raw_path = env.get("KAMIWAZA_APP_PATH", "").strip()

        if mode == "port":
            routing_mode: Literal["path", "port"] = "port"
        elif mode == "path":
            routing_mode = "path"
        elif mode:
            raise ValueError(f"unknown KAMIWAZA_ROUTING_MODE: {mode!r}")
        else:
            routing_mode = "path" if raw_path else "port"

        app_path = ""
        app_path_url = ""
        app_url = ""

        if routing_mode == "path":
            app_path = normalize_app_path(raw_path)
            if not app_path:
                raise ValueError(
                    "path routing mode requires a nonempty KAMIWAZA_APP_PATH"
                )
            app_path_url = _trim_trailing_slash(env.get("KAMIWAZA_APP_PATH_URL", ""))
            configured_app_url = _trim_trailing_slash(env.get("KAMIWAZA_APP_URL", ""))
            origin = _trim_trailing_slash(env.get("KAMIWAZA_ORIGIN", ""))
            app_url = (
                app_path_url
                or configured_app_url
                or (f"{origin}{app_path}" if origin else "")
            )
        else:
            app_url = _trim_trailing_slash(env.get("KAMIWAZA_APP_URL", ""))

        return cls(
            routing_mode=routing_mode,
            app_path=app_path,
            app_path_url=app_path_url,
            app_url=app_url,
            deployment_id=env.get("KAMIWAZA_DEPLOYMENT_ID", ""),
            app_port=env.get("KAMIWAZA_APP_PORT", ""),
        )
