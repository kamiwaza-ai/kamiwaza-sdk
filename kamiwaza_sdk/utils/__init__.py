"""Utility helpers shared across the SDK."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr


def reveal_secrets(payload: Any) -> Any:
    """Recursively unwrap ``SecretStr`` instances so JSON encoding succeeds."""

    if isinstance(payload, SecretStr):
        return payload.get_secret_value()
    if isinstance(payload, dict):
        return {key: reveal_secrets(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [reveal_secrets(item) for item in payload]
    return payload
