"""Lightweight async client for Kamiwaza platform APIs."""

from __future__ import annotations

import os
from typing import Optional

import httpx

from .config import AuthConfig

_ACTIVE_DEPLOYMENT_STATUSES = {"deployed", "running", "ready", "active"}
_PLATFORM_AUTH_HEADER_KEYS = {
    "authorization",
    "cookie",
    "x-auth-token",
    "x-workroom-id",
    "x-request-id",
}


class KamiwazaExtClient:
    """Async HTTP client for the two things extensions need:
    (1) call the model endpoint and (2) call the platform API.

    NOT the same as ``kamiwaza_sdk.KamiwazaClient`` — this is a thin
    ``httpx`` wrapper with no sync overhead, no token refresh, and no
    lazy service loading.
    """

    #: Default request timeout in seconds.
    DEFAULT_TIMEOUT = 30.0

    def __init__(
        self,
        api_base: str,
        openai_base: str = "",
        headers: Optional[dict[str, str]] = None,
        verify_ssl: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.openai_base = openai_base.rstrip("/") if openai_base else ""
        self._default_headers = headers or {}
        self._verify_ssl = verify_ssl
        self._timeout = httpx.Timeout(timeout)

    @classmethod
    def from_env(cls) -> KamiwazaExtClient:
        """Create a client from ``KAMIWAZA_*`` environment variables.

        Uses ``KAMIWAZA_API_URL`` for the platform API and
        ``KAMIWAZA_ENDPOINT`` (or ``KAMIWAZA_MODEL_URL``) for the
        model endpoint.
        """
        config = AuthConfig.from_env()
        return cls(
            api_base=config.api_url,
            openai_base=config.openai_base,
            verify_ssl=config.verify_ssl,
        )

    @classmethod
    def service_account(cls) -> KamiwazaExtClient:
        """Create a client using ``KAMIWAZA_API_KEY`` for auth.

        Use for background tasks that outlive the original request
        context (no user headers available).

        Raises:
            RuntimeError: If ``KAMIWAZA_API_KEY`` is not set.
        """
        config = AuthConfig.from_env()
        if not config.api_key:
            raise RuntimeError(
                "KAMIWAZA_API_KEY is not set. "
                "Service account auth requires an API key injected by the platform."
            )
        return cls(
            api_base=config.api_url,
            openai_base=config.openai_base,
            headers={"Authorization": f"Bearer {config.api_key}"},
            verify_ssl=config.verify_ssl,
        )

    def _client(
        self,
        extra_headers: Optional[dict[str, str]] = None,
        *,
        follow_redirects: bool = False,
    ) -> httpx.AsyncClient:
        """Return a short-lived ``httpx.AsyncClient``.

        .. note::
            A new client (and TCP connection) is created per call.
            This is acceptable for v0.1.0 where request volume is low.
            A future version should introduce a shared client with
            connection pooling to avoid port exhaustion under load.
            See: https://github.com/kamiwaza-ai/kamiwaza-sdk/issues/63
        """
        headers = {**self._default_headers, **(extra_headers or {})}
        return httpx.AsyncClient(
            headers=headers,
            verify=self._verify_ssl,
            timeout=self._timeout,
            follow_redirects=follow_redirects,
        )

    @staticmethod
    def _platform_auth_headers(
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, str]:
        """Keep only platform-safe auth headers for backend-to-platform calls.

        ``X-User-*`` identity headers are hop-bound and can trigger stricter
        ForwardAuth validation on platform APIs. For model discovery we forward
        only bearer/session auth plus safe request scoping headers.
        """
        filtered: dict[str, str] = {}
        for key, value in (headers or {}).items():
            if key.lower() in _PLATFORM_AUTH_HEADER_KEYS:
                filtered[key] = value

        has_authorization = any(
            key.lower() == "authorization" and value for key, value in filtered.items()
        )
        if not has_authorization:
            for key, value in filtered.items():
                if key.lower() == "x-auth-token" and value:
                    filtered["Authorization"] = f"Bearer {value}"
                    break
        return filtered

    async def chat_completions(
        self,
        payload: dict,
        headers: Optional[dict[str, str]] = None,
    ) -> httpx.Response:
        """Call the OpenAI-compatible chat completions endpoint.

        Raises:
            RuntimeError: If ``openai_base`` is not configured.
        """
        if not self.openai_base:
            raise RuntimeError(
                "KAMIWAZA_ENDPOINT not configured. "
                "Are you running inside a Kamiwaza deployment?"
            )
        url = f"{self.openai_base}/chat/completions"
        async with self._client(headers) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp

    async def get_models(self, headers: Optional[dict[str, str]] = None) -> list[dict]:
        """List active model deployments from the platform API.

        Prefers the newer ``/serving/deployments`` endpoint and falls back
        to the older ``/serving/deployments/active`` shape when needed.
        """
        if not self.api_base:
            raise RuntimeError(
                "KAMIWAZA_API_URL not configured. "
                "Are you running inside a Kamiwaza deployment?"
            )
        auth_headers = self._platform_auth_headers(headers)
        async with self._client(auth_headers) as client:
            urls = (
                f"{self.api_base}/serving/deployments",
                f"{self.api_base}/serving/deployments/active",
            )
            last_error: Exception | None = None
            for index, url in enumerate(urls):
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    if isinstance(data, list):
                        return [
                            item
                            for item in data
                            if not isinstance(item, dict) or _is_active_deployment(item)
                        ]
                    return data
                except httpx.HTTPStatusError as exc:
                    is_last = index == len(urls) - 1
                    if exc.response.status_code != 404 or is_last:
                        raise
                    last_error = exc

            if last_error is not None:
                raise last_error
            return []


def _is_active_deployment(item: dict) -> bool:
    status = str(item.get("status", item.get("phase", ""))).strip().lower()
    if not status:
        return True
    return status in _ACTIVE_DEPLOYMENT_STATUSES
