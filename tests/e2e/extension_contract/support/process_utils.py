from __future__ import annotations

import base64
import binascii
import logging
import re
import subprocess

logger = logging.getLogger(__name__)
_RAW_PASSWORD_RE = re.compile(r"^[^\s:]{8,}$")
_PASSWORD_STATUS_WORDS = frozenset({"done", "done.", "exiting", "exiting.", "success", "success."})


def parse_password_output(output: str) -> str | None:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    for line in lines:
        if "password" not in line.lower():
            continue
        _, _, value = line.partition(":")
        if value.strip():
            return value.strip()
    if len(lines) != 1:
        return None
    raw_password = lines[0]
    if raw_password.lower() in _PASSWORD_STATUS_WORDS:
        return None
    return raw_password if _RAW_PASSWORD_RE.fullmatch(raw_password) else None


def decode_secret_value(encoded: str) -> str | None:
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        logger.warning("Could not decode secret value: %s", exc)
        return None


def run_local_command(
    command: list[str],
    *,
    description: str,
    timeout_seconds: int = 15,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("%s failed: %s", description, exc)
        return None


def describe_auth_validation_error(exc: Exception) -> str:
    details = [type(exc).__name__]
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        details.append(f"status={status_code}")
    message = str(exc).strip()
    if message:
        details.append(message)
    return " ".join(details)
