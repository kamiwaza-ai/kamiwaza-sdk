from __future__ import annotations

import os
import re
from typing import Any

from fastapi import Request

TRUSTED_ROUTED_ROOT_PATH_PREFIX = "/runtime/apps/"
_FALSEY_ENV_VALUES = frozenset({"", "0", "false", "no", "off", "n", "f"})
_MAX_LOG_FIELD_LENGTH = 256
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def runtime_value(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def auth_enabled() -> bool:
    raw = os.getenv("KAMIWAZA_USE_AUTH")
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSEY_ENV_VALUES


def runtime_prefix() -> str:
    value = runtime_value("KAMIWAZA_APP_PATH")
    if not value:
        return ""
    normalized = value if value.startswith("/") else f"/{value}"
    return normalized.rstrip("/") or "/"


def safe_log_field(value: str | None) -> str:
    if not value:
        return ""
    sanitized = "".join(
        char for char in value if char not in "\r\n=" and ord(char) >= 32
    )
    return sanitized[:_MAX_LOG_FIELD_LENGTH]


def normalized_workroom_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized or not _UUID_RE.fullmatch(normalized):
        return None
    return normalized


def normalized_workroom_role(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    return normalized or None


def routed_root_path(request: Request) -> str:
    root_path = str(request.scope.get("root_path") or "").strip()
    if not root_path:
        return ""
    normalized = root_path if root_path.startswith("/") else f"/{root_path}"
    normalized = normalized.rstrip("/") or "/"
    return normalized if normalized.startswith(TRUSTED_ROUTED_ROOT_PATH_PREFIX) else ""


def has_forwarded_identity_headers(request: Request) -> bool:
    return any(
        request.headers.get(header)
        for header in (
            "x-user-id",
            "x-user-email",
            "x-user-workroom-id",
            "x-user-workroom-role",
        )
    )


def trusted_routed_workroom_context(request: Request, identity: Any) -> bool:
    if not getattr(identity, "is_authenticated", False):
        return False
    if not auth_enabled():
        return False
    expected_runtime_prefix = runtime_prefix()
    if not expected_runtime_prefix:
        return False
    return routed_root_path(request) == expected_runtime_prefix


def trust_identity_workroom_fields(request: Request, identity: Any) -> bool:
    if not getattr(identity, "is_authenticated", False):
        return False
    if trusted_routed_workroom_context(request, identity):
        return True
    return not has_forwarded_identity_headers(request)


def current_workroom_id(request: Request, identity: Any) -> str | None:
    workroom_id = getattr(identity, "workroom_id", None)
    normalized = normalized_workroom_id(workroom_id if isinstance(workroom_id, str) else None)
    if normalized and trust_identity_workroom_fields(request, identity):
        return normalized
    if not trusted_routed_workroom_context(request, identity):
        return None
    return normalized_workroom_id(request.headers.get("x-user-workroom-id"))


def workroom_role(request: Request, identity: Any) -> str | None:
    role = getattr(identity, "workroom_role", None)
    if isinstance(role, str) and role and trust_identity_workroom_fields(request, identity):
        return normalized_workroom_role(role)
    if not trusted_routed_workroom_context(request, identity):
        return None
    return normalized_workroom_role(request.headers.get("x-user-workroom-role"))
