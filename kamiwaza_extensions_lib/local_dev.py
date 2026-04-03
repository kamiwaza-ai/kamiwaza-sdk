"""Helpers for connection-backed local auth in localhost dev mode."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

_LOCAL_DEV_AUTH_HEADERS_ENV = "KAMIWAZA_LOCAL_DEV_AUTH_HEADERS_JSON"
_LOCAL_DEV_AUTH_ENABLED_ENV = "KAMIWAZA_LOCAL_DEV_AUTH_BRIDGE"
_ALLOWED_AUTH_HEADERS = frozenset(
    {
        "authorization",
        "x-auth-token",
        "x-user-id",
        "x-user-email",
        "x-user-name",
        "x-user-roles",
        "x-workroom-id",
        "x-request-id",
    }
)


def _bridge_enabled() -> bool:
    raw = os.environ.get(_LOCAL_DEV_AUTH_ENABLED_ENV, "false").strip().lower()
    return raw not in ("", "0", "false", "no")


def _auth_enabled() -> bool:
    raw = os.environ.get("KAMIWAZA_USE_AUTH", "true").strip().lower()
    return raw not in ("", "0", "false", "no")


def get_local_dev_auth_headers() -> dict[str, str]:
    """Return bridge headers injected by ``kz-ext dev local --auth``."""
    if not _bridge_enabled() or not _auth_enabled():
        return {}

    raw = os.environ.get(_LOCAL_DEV_AUTH_HEADERS_ENV, "")
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    if not isinstance(data, dict):
        return {}

    headers: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or key.lower() not in _ALLOWED_AUTH_HEADERS:
            continue
        if value in (None, ""):
            continue
        if isinstance(value, (str, int, float, bool)):
            headers[key.lower()] = str(value)
    return headers


def forward_or_bridge_auth_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    """Return request auth headers, or bridge headers when the request has none."""
    forwarded = {
        key: str(value)
        for key, value in headers.items()
        if key.lower() in _ALLOWED_AUTH_HEADERS and value not in (None, "")
    }
    if forwarded:
        return forwarded
    return get_local_dev_auth_headers()
