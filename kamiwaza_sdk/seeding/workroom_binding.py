"""Bind newly created workrooms after their authority becomes available."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

from ..exceptions import APIError

if TYPE_CHECKING:
    from ..client import KamiwazaClient

_BINDING_TIMEOUT_SECONDS = 30.0


def enter_projected_workroom(client: KamiwazaClient, workroom_id: str | UUID) -> None:
    """Retry only the explicit pre-binding authorization-unavailable response.

    The enter route checks workroom visibility before changing session state.
    New grants can still be projecting at that boundary. Denials, binding
    conflicts, transport failures and other 503 responses propagate unchanged.
    """
    deadline = time.monotonic() + _BINDING_TIMEOUT_SECONDS
    while True:
        try:
            client.workrooms.enter(workroom_id)
            return
        except APIError as error:
            if not _authority_pending(error):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(1.0, remaining))


def _authority_pending(error: APIError) -> bool:
    if error.status_code != 503:
        return False
    if not isinstance(error.response_data, dict):
        return False
    return error.response_data.get("detail") == "authorization_unavailable"
