from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast

from kamiwaza_sdk.exceptions import KamiwazaError

CONNECTOR_LIST_RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0, 3.0, 3.5)
LOGGER = logging.getLogger(__name__)


class ConnectorListClient(Protocol):
    def get(
        self, endpoint: str, *, headers: dict[str, str]
    ) -> Mapping[str, Any] | list[dict[str, Any]] | None: ...


def response_items(response: object, *, endpoint: str) -> list[dict[str, Any]]:
    if response is None:
        return []
    if isinstance(response, list):
        return _validated_mapping_items(response, endpoint=endpoint)
    if isinstance(response, Mapping):
        items = response.get("items", [])
        if items is None:
            return []
        if isinstance(items, list):
            return _validated_mapping_items(items, endpoint=endpoint)
        raise AssertionError(
            f"Unexpected list response from {endpoint}: items={type(items).__name__}"
        )
    raise AssertionError(
        f"Unexpected list response from {endpoint}: {type(response).__name__}"
    )


def _validated_mapping_items(
    items: list[object], *, endpoint: str
) -> list[dict[str, Any]]:
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise AssertionError(
                f"Unexpected list response from {endpoint}: item[{index}]="
                f"{type(item).__name__}"
            )
    return cast(list[dict[str, Any]], items)


def _response_text(exc: KamiwazaError) -> str | None:
    return cast(str | None, getattr(exc, "response_text", None))


def _response_body(exc: KamiwazaError) -> object | None:
    response_data = getattr(exc, "response_data", None)
    if response_data is not None:
        return response_data
    return exc.body


def retryable_connector_list_error(exc: KamiwazaError) -> bool:
    return (
        exc.status_code == 403
        and not (_response_text(exc) or "").strip()
        and _response_body(exc) is None
    )


def connector_list_retry_message(
    *,
    workroom_id: str,
    attempts: int,
    elapsed_seconds: float,
    exc: KamiwazaError,
) -> str:
    return (
        "GET /dde/connectors/ remained forbidden for explicit "
        f"X-Workroom-Id={workroom_id!r} after {attempts} attempts "
        f"over {elapsed_seconds:.1f}s. "
        f"Last status={exc.status_code}, response_text={_response_text(exc)!r}. "
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
) -> list[dict[str, Any]]:
    """List DDE connectors scoped to a workroom via header.

    Retries only the blank-body 403 seen on immediate authorized self-access.
    Non-empty 403s and non-403 errors fail fast; persistent blank 403s use a
    bounded ~10s default retry budget before failing with diagnostics.
    """
    started_at = monotonic()

    for attempt_index in range(len(retry_delays) + 1):
        try:
            resp = sdk.get("/dde/connectors/", headers={"X-Workroom-Id": workroom_id})
        except KamiwazaError as exc:
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
            delay = retry_delays[attempt_index]
            LOGGER.warning(
                "Retrying GET /dde/connectors/ after blank 403 for explicit "
                "X-Workroom-Id=%r: attempt %s/%s sleeping %.1fs",
                workroom_id,
                attempt_index + 1,
                len(retry_delays) + 1,
                delay,
            )
            sleep(delay)
            continue
        return response_items(resp, endpoint="/dde/connectors/")

    # Defensive guard: the loop always returns, retries, or raises.
    raise AssertionError("unreachable connector-list retry state")
