"""Kamiwaza client — httpx wiring + base _request + retry middleware.

T5.2 of the Federation API + SDK MVP. Builds on the T5.1 skeleton with:

    - httpx.Client construction with base URL + Authorization header
    - Base ``_request`` helper that returns parsed JSON (mirror of the
      kamiwaza_sdk.services.base_service pattern, but new namespace)
    - Retry middleware honoring design §4.2.1's SDK retry contract:
      503 with ``detail.reason == "psk_propagation_timeout"`` is retried
      with exponential backoff (1, 2, 4, 8, 16s) capped at a 90s
      wall-clock total. Other 503s and all 4xx responses surface
      immediately as KamiwazaError(status_code=...).
    - close() / context-manager support for releasing transport
      resources outside ``with`` blocks.

Federation, jobs, retrieval, etc. modules attach as attributes in
subsequent tickets (T5.3, T5.9, T5.36, …).
"""

from __future__ import annotations

import json
import os
import time
from types import TracebackType
from typing import Any, Optional, Type

import httpx


# Design §4.2.1 retry contract — exponential schedule capped at 90s
# wall-clock total. The schedule is deterministic so behavior is testable
# without a clock injection layer.
_RETRY_BACKOFF_SCHEDULE_SECONDS = (1, 2, 4, 8, 16, 32, 64)
_RETRY_WALL_CLOCK_BUDGET_SECONDS = 90.0
_PSK_PROPAGATION_TIMEOUT_REASON = "psk_propagation_timeout"


class Kamiwaza:
    """Client handle for the Kamiwaza platform.

    Args:
        base_url: Cluster URL, e.g. ``https://kamiwaza.test``.
        token: Personal Access Token (PAT) for authentication.
        timeout: Per-request timeout in seconds (default 30).

    Example:
        >>> client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-...")
        >>> # T5.3+ adds client.federations, client.jobs, etc.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    @classmethod
    def from_env(
        cls,
        base_url_env: str = "KAMIWAZA_BASE_URL",
        token_env: str = "KAMIWAZA_TOKEN",
    ) -> "Kamiwaza":
        """Construct a client from env vars (canonical entry point).

        Args:
            base_url_env: Env var name holding the cluster base URL.
            token_env: Env var name holding the PAT.

        Returns:
            Configured Kamiwaza client instance.

        Raises:
            KamiwazaError: When either env var is unset; the message names
                the missing variable so the operator can fix it directly.
        """
        from kamiwaza.exceptions import KamiwazaError

        base_url: Optional[str] = os.environ.get(base_url_env)
        token: Optional[str] = os.environ.get(token_env)
        if not base_url:
            raise KamiwazaError(
                f"{base_url_env} env var is not set; cannot construct Kamiwaza "
                "client via from_env(). Set it to your cluster URL "
                "(e.g. https://kamiwaza.example.com) or use the explicit "
                "Kamiwaza(base_url=..., token=...) constructor."
            )
        if not token:
            raise KamiwazaError(
                f"{token_env} env var is not set; cannot construct Kamiwaza "
                "client via from_env(). Set it to your Personal Access "
                "Token or use the explicit Kamiwaza(base_url=..., token=...) "
                "constructor."
            )
        return cls(base_url=base_url, token=token)

    def close(self) -> None:
        """Release the underlying httpx transport resources."""
        self._http.close()

    def __enter__(self) -> "Kamiwaza":
        return self

    def __exit__(
        self,
        _exc_type: Optional[Type[BaseException]],
        _exc: Optional[BaseException],
        _tb: Optional[TracebackType],
    ) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Issue an HTTP request and return the parsed JSON body.

        Wraps ``httpx.Client.request`` with the design's SDK retry contract
        (§4.2.1): 503 with ``detail.reason == "psk_propagation_timeout"``
        retries with exponential backoff capped at a 90s wall-clock budget.
        Other non-2xx responses are mapped to ``KamiwazaError`` with the
        status code attached.

        Args:
            method: HTTP method ("GET", "POST", etc.).
            path: Request path; resolved against the client's base_url.
            **kwargs: Forwarded to ``httpx.Client.request`` (json, params, …).

        Returns:
            The decoded JSON body from the first 2xx response.
        """

        deadline = time.monotonic() + _RETRY_WALL_CLOCK_BUDGET_SECONDS
        last_response: Optional[httpx.Response] = None

        for delay in (0,) + _RETRY_BACKOFF_SCHEDULE_SECONDS:
            if delay:
                if time.monotonic() + delay > deadline:
                    break
                time.sleep(delay)

            response = self._http.request(method, path, **kwargs)
            last_response = response
            if response.status_code < 400:
                return _parse_json(response)

            if not _is_psk_propagation_timeout(response):
                raise _to_kamiwaza_error(response)

            # 503 psk_propagation_timeout — retry per backoff schedule.

        # Loop exhausted while still racing DataHub. Surface as KamiwazaError.
        assert last_response is not None  # loop ran at least once
        raise _to_kamiwaza_error(last_response)


def _parse_json(response: httpx.Response) -> Any:
    """Return the response body parsed as JSON, or {} for empty 2xx."""
    if not response.content:
        return {}
    return response.json()


def _is_psk_propagation_timeout(response: httpx.Response) -> bool:
    """Match the design §4.2.1 retry-eligible 503 shape exactly.

    Server returns ``HTTPException(status_code=503, detail={"reason":
    "psk_propagation_timeout", "elapsed_seconds": ..., "remediation": ...})``.
    """
    if response.status_code != 503:
        return False
    body = _try_parse_json(response)
    if not isinstance(body, dict):
        return False
    detail = body.get("detail")
    if not isinstance(detail, dict):
        return False
    return detail.get("reason") == _PSK_PROPAGATION_TIMEOUT_REASON


def _try_parse_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return None


def _to_kamiwaza_error(response: httpx.Response) -> Exception:
    """Map a non-2xx httpx.Response to the most-specific KamiwazaError.

    Delegates to ``kamiwaza.exceptions.error_for_response`` for the
    dispatch table mapping ``(status_code, detail.reason)`` to typed
    subclasses (T5.10). Unrecognized shapes fall back to the base
    ``KamiwazaError``.
    """
    from kamiwaza.exceptions import error_for_response

    body = _try_parse_json(response)
    snippet = response.text[:200] if response.text else ""
    message = f"Kamiwaza API request failed: {response.status_code} {snippet}".strip()
    return error_for_response(response.status_code, body, message)
