"""Model discovery and client helpers for extensions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from fastapi import Request

from .auth import forward_auth_headers
from .config import AuthConfig
from .client import KamiwazaExtClient


@dataclass
class AvailableModel:
    """A model available for use by the extension."""

    id: str = ""
    name: str = ""
    repo_id: Optional[str] = None
    type: Optional[str] = None  # "chat", "embedding", etc.
    capabilities: list[str] = field(default_factory=list)
    status: str = "unknown"

    # Forward compatibility — unknown keys are silently kept.
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AvailableModel:
        known = {"id", "name", "repo_id", "type", "capabilities", "status"}
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(
            id=str(data.get("id", data.get("deployment_id", ""))),
            name=data.get("name", data.get("model_name", "")),
            repo_id=data.get("repo_id"),
            type=data.get("type"),
            capabilities=data.get("capabilities", []),
            status=data.get("status", data.get("phase", "unknown")),
            _extra=extra,
        )


async def get_model_client(request: Request):
    """Return an ``openai.AsyncOpenAI`` client for the platform model endpoint.

    The client's ``base_url`` points to ``KAMIWAZA_ENDPOINT`` and the
    user's auth headers are forwarded automatically.

    Requires the ``openai`` package (``pip install openai>=1.0``).

    Raises:
        RuntimeError: If ``KAMIWAZA_ENDPOINT`` is not configured or
            the ``openai`` package is not installed.
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise RuntimeError(
            "The 'openai' package is required for get_model_client(). "
            "Install it with: pip install openai>=1.0"
        )

    config = AuthConfig.from_env()
    if not config.openai_base:
        raise RuntimeError(
            "KAMIWAZA_ENDPOINT not configured. "
            "Are you running inside a Kamiwaza deployment?"
        )

    fwd = forward_auth_headers(request.headers)
    auth_header = None
    passthrough_headers: dict[str, str] = {}
    for key, value in fwd.items():
        if key.lower() == "authorization":
            auth_header = value
        else:
            passthrough_headers[key] = value

    # AsyncOpenAI always synthesizes its own Authorization header from api_key.
    # Reuse the forwarded bearer token there so the on-the-wire header matches
    # the incoming request instead of being replaced by a placeholder value.
    api_key = "not-needed-kamiwaza"
    if auth_header:
        prefix = "bearer "
        if auth_header.lower().startswith(prefix):
            api_key = auth_header[len(prefix):]
        else:
            api_key = auth_header

    return AsyncOpenAI(
        base_url=config.openai_base,
        api_key=api_key,
        default_headers=passthrough_headers,
    )


async def list_available_models(request: Request) -> list[AvailableModel]:
    """List models available to the current user.

    Calls ``GET /serving/deployments/active`` with the user's auth
    context and returns typed ``AvailableModel`` objects.

    Returns an empty list when no deployments are active or the
    platform API is not configured.
    """
    config = AuthConfig.from_env()
    if not config.api_url:
        return []

    fwd = forward_auth_headers(request.headers)
    client = KamiwazaExtClient.from_env()
    try:
        deployments = await client.get_models(headers=fwd)
    except (httpx.HTTPError, OSError):
        return []

    if isinstance(deployments, list):
        return [AvailableModel.from_dict(d) for d in deployments]
    return []
