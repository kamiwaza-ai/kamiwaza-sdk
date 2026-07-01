from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from kamiwaza_sdk.exceptions import APIError

CONNECTOR_LIST_RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0, 3.0, 3.5)


class ConnectorListClient(Protocol):
    def get(self, endpoint: str, **kwargs: Any) -> dict[str, Any]: ...


def retryable_connector_list_error(exc: APIError) -> bool:
    return exc.status_code == 403 and not (exc.response_text or "").strip()


def connector_list_retry_message(
    *,
    workroom_id: str,
    attempts: int,
    elapsed_seconds: float,
    exc: APIError,
) -> str:
    return (
        "GET /dde/connectors/ remained forbidden for explicit "
        f"X-Workroom-Id={workroom_id!r} after {attempts} attempts "
        f"over {elapsed_seconds:.1f}s. "
        f"Last status={exc.status_code}, response_text={exc.response_text!r}. "
        "Set KAMIWAZA_HTTP_TRACE=1 or KAMIWAZA_HTTP_TRACE_FILE to capture "
        "request and response headers."
    )


def list_connectors(
    sdk: ConnectorListClient,
    workroom_id: str,
    *,
    retry_delays: Sequence[float] = CONNECTOR_LIST_RETRY_DELAYS_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> list:
    """List DDE connectors scoped to a workroom via header.

    Retries only the blank-body 403 seen on immediate authorized self-access.
    Non-empty 403s and non-403 errors fail fast; persistent blank 403s use a
    bounded ~10s default retry budget before failing with diagnostics.
    """
    started_at = monotonic()

    for attempt_index in range(len(retry_delays) + 1):
        try:
            resp = sdk.get("/dde/connectors/", headers={"X-Workroom-Id": workroom_id})
        except APIError as exc:
            if not retryable_connector_list_error(exc):
                raise
            if attempt_index >= len(retry_delays):
                raise AssertionError(
                    connector_list_retry_message(
                        workroom_id=workroom_id,
                        attempts=attempt_index + 1,
                        elapsed_seconds=monotonic() - started_at,
                        exc=exc,
                    )
                ) from exc
            sleep(retry_delays[attempt_index])
            continue
        return resp.get("items", [])

    # Defensive guard: the loop always returns, retries, or raises.
    raise AssertionError("unreachable connector-list retry state")
