"""HTTP client for Kamiwaza API with forward auth support."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from .config import AuthConfig

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def forward_auth_headers(
    request_headers: Mapping[str, str],
    include_forwarded: bool = True,
    include_user_headers: bool = True,
) -> dict[str, str]:
    """Extract authentication headers to forward to upstream APIs.

    Extracts auth-related headers from the incoming request to forward
    to Kamiwaza APIs, preserving the user's authentication context.

    By default, includes:
    - Core auth: authorization, cookie, x-auth-token
    - Forwarded headers: x-forwarded-for, x-forwarded-proto, x-forwarded-host,
      x-forwarded-uri, x-forwarded-prefix, x-real-ip, x-original-url, x-request-id
    - User identity: all x-user-* headers (set by Kamiwaza forward auth)

    Args:
        request_headers: Headers from the incoming request (e.g., request.headers)
        include_forwarded: Include x-forwarded-* and related headers (default: True)
        include_user_headers: Include x-user-* headers (default: True)

    Returns:
        Dictionary containing the headers to forward

    Example:
        @app.get("/api/proxy")
        async def proxy(request: Request):
            headers = forward_auth_headers(request.headers)
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.example.com/data",
                    headers=headers
                )
            return resp.json()

        # Minimal headers only
        headers = forward_auth_headers(
            request.headers,
            include_forwarded=False,
            include_user_headers=False
        )
    """
    headers: dict[str, str] = {}

    header_items = (
        request_headers.items()
        if hasattr(request_headers, "items")
        else [(k, request_headers.get(k)) for k in request_headers]
    )
    normalized_headers = {key.lower(): value for key, value in header_items if value}

    # Core auth headers
    for key in ("authorization", "cookie", "x-auth-token"):
        value = normalized_headers.get(key)
        if value:
            headers[key] = value

    # Forwarded headers (for proper routing and logging)
    if include_forwarded:
        forwarded_keys = (
            "x-forwarded-for",
            "x-forwarded-proto",
            "x-forwarded-host",
            "x-forwarded-uri",
            "x-forwarded-prefix",
            "x-real-ip",
            "x-original-url",
            "x-request-id",
        )
        for key in forwarded_keys:
            value = normalized_headers.get(key)
            if value:
                headers[key] = value

    # User identity headers (set by Kamiwaza forward auth)
    if include_user_headers:
        for key, value in normalized_headers.items():
            if key.startswith("x-user-") and value:
                headers[key] = value

    # Convert access_token cookie to Authorization header if needed
    if "authorization" not in headers:
        # Check if there's an access_token in the cookie header
        cookie_value = normalized_headers.get("cookie", "")
        if "access_token=" in cookie_value:
            # Extract token from cookie string
            for part in cookie_value.split(";"):
                part = part.strip()
                if part.startswith("access_token="):
                    token = part[len("access_token=") :]
                    headers["authorization"] = f"Bearer {token}"
                    break

    return headers


@dataclass
class KamiwazaClient:
    """HTTP client for Kamiwaza platform APIs with authentication forwarding.

    This client handles authentication header forwarding and provides methods
    for common Kamiwaza API operations.

    Attributes:
        api_base: Base URL for Kamiwaza API
        openai_base: Base URL for OpenAI-compatible endpoints
        timeout: HTTP request timeout configuration

    Example:
        client = KamiwazaClient.from_env()
        models = await client.get_models(request.headers)
    """

    api_base: str
    openai_base: str
    timeout: httpx.Timeout

    @classmethod
    def from_env(cls) -> KamiwazaClient:
        """Create client from environment variables.

        Uses:
        - KAMIWAZA_API_URL: Base API URL (default: http://host.docker.internal:7777/api)
        - KAMIWAZA_ENDPOINT: OpenAI-compatible endpoint (default: http://host.docker.internal:8080)
        """
        api_url = os.getenv(
            "KAMIWAZA_API_URL", "http://host.docker.internal:7777/api"
        ).rstrip("/")
        openai_url = (os.getenv("KAMIWAZA_ENDPOINT") or "").rstrip(
            "/"
        ) or "http://host.docker.internal:8080"
        return cls(api_base=api_url, openai_base=openai_url, timeout=DEFAULT_TIMEOUT)

    @classmethod
    def from_config(cls, config: AuthConfig) -> KamiwazaClient:
        """Create client from AuthConfig."""
        openai_url = (os.getenv("KAMIWAZA_ENDPOINT") or "").rstrip(
            "/"
        ) or "http://host.docker.internal:8080"
        return cls(
            api_base=config.api_url,
            openai_base=openai_url,
            timeout=DEFAULT_TIMEOUT,
        )

    async def get_models(self, headers: Mapping[str, str]) -> dict[str, Any]:
        """Fetch available models from Kamiwaza.

        Args:
            headers: Request headers to forward for authentication

        Returns:
            List of available models

        Note:
            Uses trailing slash on /models/ to avoid redirect issues.
        """
        async with httpx.AsyncClient(
            timeout=self.timeout, verify=False, follow_redirects=True
        ) as client:
            resp = await client.get(
                f"{self.api_base}/models/",
                headers=forward_auth_headers(headers),
            )
            resp.raise_for_status()
            return resp.json()

    async def validate(self, headers: Mapping[str, str]) -> dict[str, Any] | None:
        """Validate authentication with Kamiwaza API.

        Args:
            headers: Request headers to forward for authentication

        Returns:
            User information dict if authenticated, None otherwise
        """
        validate_url = (
            os.getenv("AUTH_VALIDATE_URL") or f"{self.api_base}/auth/validate"
        )
        async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
            resp = await client.get(
                validate_url,
                headers=forward_auth_headers(headers),
            )
            if resp.status_code != 200:
                return None
            return resp.json()

    async def chat_completions(
        self, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> httpx.Response:
        """Send chat completion request to OpenAI-compatible endpoint.

        Args:
            payload: The chat completion request body
            headers: Request headers to forward for authentication

        Returns:
            The raw httpx.Response for streaming support
        """
        async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
            return await client.post(
                f"{self.openai_base}/chat/completions",
                headers=forward_auth_headers(headers)
                | {"Content-Type": "application/json"},
                json=payload,
            )

    async def embeddings(
        self, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> httpx.Response:
        """Send embeddings request to OpenAI-compatible endpoint.

        Args:
            payload: The embeddings request body
            headers: Request headers to forward for authentication

        Returns:
            The raw httpx.Response
        """
        async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
            return await client.post(
                f"{self.openai_base}/embeddings",
                headers=forward_auth_headers(headers)
                | {"Content-Type": "application/json"},
                json=payload,
            )
