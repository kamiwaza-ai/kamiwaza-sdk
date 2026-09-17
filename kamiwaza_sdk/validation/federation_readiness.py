"""Wait for first-ingress authorization projection before fixture operations."""

from __future__ import annotations

import logging
import time
from typing import Any

from kamiwaza_sdk.exceptions import APIError

logger = logging.getLogger(__name__)
_AUTHORIZATION_TIMEOUT_SECONDS = 30.0


def authorized_datasets(persona: Any, target: str) -> Any:
    """Read through a bounded projection delay without replaying mutations.

    An allowlisted brokered user's initial grants are applied on first ingress.
    SpiceDB fails closed until those grants project. Only the explicit 503
    authorization-unavailable response is transient here; denials and other
    failures retain their original behavior.
    """
    deadline = time.monotonic() + _AUTHORIZATION_TIMEOUT_SECONDS
    while True:
        try:
            return persona.catalog.datasets.list(target_cluster=target)
        except APIError as exc:
            if not _projection_unavailable(exc):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "federation authorization projection timed out"
                ) from None
            logger.info("federation_authorization_projection_pending")
            time.sleep(min(1.0, remaining))


def _projection_unavailable(error: APIError) -> bool:
    if error.status_code != 503:
        return False
    body = error.response_data
    if not isinstance(body, dict):
        return False
    return body.get("detail") == "authorization_unavailable"
