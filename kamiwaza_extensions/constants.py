"""Shared constants and utilities for kamiwaza-extensions."""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from kamiwaza_extensions.connections import ConnectionInfo

COMPOSE_FILENAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

# All compose-file names a version bump / drift check should inspect. Includes
# the appgarden deployment overlay alongside the canonical compose names.
ALL_COMPOSE_FILENAMES = COMPOSE_FILENAMES + (
    "docker-compose.appgarden.yml",
    "docker-compose.appgarden.yaml",
)

EXTENSIONS_NAMESPACE = "kamiwaza-extensions"


@contextlib.contextmanager
def ssl_env_override(connection: "ConnectionInfo") -> Generator[None, None, None]:
    """Temporarily set KAMIWAZA_VERIFY_SSL=false if the connection's
    effective setting disables SSL.

    Reads ``connection.effective_verify_ssl()`` (centralizes env-var
    override + dev-TLD auto-disable + persisted setting) so the SDK
    HTTP client picks up the same intent the rest of the deploy
    pipeline uses. Avoids divergence between "what the developer
    expected" and "what the inner client did".
    """
    old = os.environ.get("KAMIWAZA_VERIFY_SSL")
    if not connection.effective_verify_ssl():
        os.environ["KAMIWAZA_VERIFY_SSL"] = "false"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("KAMIWAZA_VERIFY_SSL", None)
        else:
            os.environ["KAMIWAZA_VERIFY_SSL"] = old


def extract_user_id(access_token: str) -> str:
    """Extract a stable user identifier (``sub`` claim) from a JWT.

    Falls back to hashing the token if decoding fails.
    """
    import base64
    import json as _json

    try:
        payload_b64 = access_token.split(".")[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        sub = payload.get("sub")
        if sub:
            return sub
    except Exception:
        pass
    import hashlib
    return hashlib.sha256(access_token.encode()).hexdigest()
