"""Lightweight async client for Kamiwaza platform APIs."""

from __future__ import annotations

import os
from typing import Optional

import httpx

from .config import AuthConfig


class KamiwazaExtClient:
    """Async HTTP client for the two things extensions need:
    (1) call the model endpoint and (2) call the platform API.

    NOT the same as ``kamiwaza_sdk.KamiwazaClient`` — this is a thin
    ``httpx`` wrapper with no sync overhead, no token refresh, and no
    lazy service loading.
    """

    def __init__(
        self,
        api_base: str,
        openai_base: str = "",
        headers: Optional[dict[str, str]] = None,
        verify_ssl: bool = True,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.openai_base = openai_base.rstrip("/") if openai_base else ""
        self._default_headers = headers or {}
        self._verify_ssl = verify_ssl

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

    def _client(self, extra_headers: Optional[dict[str, str]] = None) -> httpx.AsyncClient:
        headers = {**self._default_headers, **(extra_headers or {})}
        return httpx.AsyncClient(headers=headers, verify=self._verify_ssl)

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

    async def get_models(
        self, headers: Optional[dict[str, str]] = None
    ) -> list[dict]:
        """List active model deployments from the platform API."""
        if not self.api_base:
            raise RuntimeError(
                "KAMIWAZA_API_URL not configured. "
                "Are you running inside a Kamiwaza deployment?"
            )
        url = f"{self.api_base}/serving/deployments/active"
        async with self._client(headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
