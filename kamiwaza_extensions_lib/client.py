"""Lightweight async client for Kamiwaza platform APIs."""

from __future__ import annotations

import ssl
from collections.abc import Mapping
from typing import Optional

import httpx

from ._headers import header_bytes
from .auth import platform_auth_httpx_headers
from .config import AuthConfig
from .errors import UnexpectedContextError
from .url import CallbackTarget, callback_target, registered_api_url

_ACTIVE_DEPLOYMENT_STATUSES = {"deployed", "running", "ready", "active"}


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
        verify_ssl: bool | ssl.SSLContext = True,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.openai_base = openai_base.rstrip("/") if openai_base else ""
        self._default_headers = headers or {}
        self._verify_ssl = verify_ssl
        self._timeout = httpx.Timeout(timeout)
        self._api_target = CallbackTarget(self.api_base, "", "")
        self._model_target = CallbackTarget(self.openai_base, "", "")

    @classmethod
    def from_env(cls) -> KamiwazaExtClient:
        """Create a client from standard ``KAMIWAZA_*`` runtime variables."""
        return cls._from_config(AuthConfig.from_env())

    @classmethod
    def service_account(cls) -> KamiwazaExtClient:
        """Create a client using ``KAMIWAZA_API_KEY`` for auth.

        Use for background tasks that outlive the original request
        context (no user headers available).

        Raises:
            RuntimeError: If ``KAMIWAZA_API_KEY`` is not set.
            UnexpectedContextError: If ``KAMIWAZA_CA_BUNDLE`` is not a
                readable PEM trust bundle.
        """
        config = AuthConfig.from_env()
        if not config.api_key:
            raise RuntimeError(
                "KAMIWAZA_API_KEY is not set. "
                "Service account auth requires an API key injected by the platform."
            )
        return cls._from_config(
            config,
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    @classmethod
    def _from_config(
        cls,
        config: AuthConfig,
        headers: Optional[dict[str, str]] = None,
    ) -> KamiwazaExtClient:
        standard_transport = config.platform_gateway_url.strip()
        api_base = (
            registered_api_url(config)
            if standard_transport
            else config.api_url.strip() or registered_api_url(config)
        )
        try:
            api_target = (
                callback_target(api_base, standard_transport)
                if api_base
                else CallbackTarget("", "", "")
            )
            model_target = (
                callback_target(config.openai_base, standard_transport)
                if config.openai_base
                else CallbackTarget("", "", "")
            )
        except ValueError as exc:
            raise UnexpectedContextError(
                "KAMIWAZA_API_URL, KAMIWAZA_PUBLIC_API_URL, "
                "KAMIWAZA_ENDPOINT, or KAMIWAZA_PLATFORM_GATEWAY_URL "
                "is not valid callback configuration"
            ) from exc
        client = cls(
            api_base=api_target.url,
            openai_base=model_target.url,
            headers=headers,
            verify_ssl=config.httpx_verify(),
        )
        client._api_target = api_target
        client._model_target = model_target
        return client

    def _client(
        self,
        extra_headers: httpx.Headers | dict[str, str] | None = None,
        *,
        target: CallbackTarget | None = None,
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
        headers = httpx.Headers(
            [header_bytes(key, value) for key, value in self._default_headers.items()]
        )
        if extra_headers is not None:
            encoded_extra_headers = (
                httpx.Headers(extra_headers.raw)
                if isinstance(extra_headers, httpx.Headers)
                else httpx.Headers(
                    [header_bytes(key, value) for key, value in extra_headers.items()]
                )
            )
            headers.update(encoded_extra_headers)
            # Rebuild from raw pairs so httpx infers the merged wire encoding;
            # a string round-trip would restore its ASCII-only normalization.
            headers = httpx.Headers(headers.raw)
        if target is not None and target.host:
            headers["Host"] = target.host
        return httpx.AsyncClient(
            headers=headers,
            verify=self._verify_ssl,
            timeout=self._timeout,
            follow_redirects=follow_redirects,
            trust_env=False,
        )

    @staticmethod
    def _platform_auth_headers(
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Headers:
        """Keep the complete signed envelope for backend-to-platform calls."""
        return platform_auth_httpx_headers(headers or {})

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
        async with self._client(headers, target=self._model_target) as client:
            resp = await client.post(
                url,
                json=payload,
                extensions=self._model_target.extensions,
            )
            resp.raise_for_status()
            return resp

    async def get_models(self, headers: Mapping[str, str] | None = None) -> list[dict]:
        """List active model deployments from the platform API."""
        if not self.api_base:
            raise RuntimeError(
                "KAMIWAZA_API_URL or KAMIWAZA_PUBLIC_API_URL not configured. "
                "Are you running inside a Kamiwaza deployment?"
            )
        auth_headers = self._platform_auth_headers(headers)
        async with self._client(auth_headers, target=self._api_target) as client:
            try:
                data = await _get_json(
                    client,
                    f"{self.api_base}/serving/deployments",
                    self._api_target,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                data = await _get_json(
                    client,
                    f"{self.api_base}/serving/deployments/active",
                    self._api_target,
                )
        if not isinstance(data, list):
            return data
        return [
            item
            for item in data
            if not isinstance(item, dict) or _is_active_deployment(item)
        ]


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    target: CallbackTarget,
):
    response = await client.get(url, extensions=target.extensions)
    response.raise_for_status()
    return response.json()


def _is_active_deployment(item: dict) -> bool:
    status = str(item.get("status", item.get("phase", ""))).strip().lower()
    if not status:
        return True
    return status in _ACTIVE_DEPLOYMENT_STATUSES
