"""Model discovery and client helpers for extensions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import Request

from .auth import forward_auth_headers
from .client import KamiwazaExtClient
from .config import AuthConfig
# Round-9 review: ``_url`` was renamed to ``url`` (public) in 0.4.0 so
# scaffolded extensions can import the helpers without coupling to a
# private path. The underscored aliases below preserve in-tree
# backward-compat for existing test imports.
from .url import (
    backend_runtime_base as _backend_runtime_base,  # noqa: F401
    public_base_url as _public_base_url,  # noqa: F401
)

_ACTIVE_DEPLOYMENT_STATUSES = {"deployed", "running", "ready", "active"}


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
            name=data.get("name", data.get("model_name", data.get("m_name", ""))),
            repo_id=data.get("repo_id"),
            type=data.get("type") or _infer_model_type(data),
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
    fwd = forward_auth_headers(request.headers)
    openai_base = await _resolve_openai_base(config, fwd)
    if not openai_base:
        raise RuntimeError(
            "KAMIWAZA_ENDPOINT not configured. "
            "Are you running inside a Kamiwaza deployment?"
        )

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
        base_url=openai_base,
        api_key=api_key,
        default_headers=passthrough_headers,
        http_client=httpx.AsyncClient(verify=config.verify_ssl),
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
        # list_available_models returns endpoints intended for the
        # frontend / end-user display. Use the public (browser-facing)
        # base URL so the values surfaced to the UI are the URLs a user
        # would copy-paste or click — not the container-internal URL.
        public_base = _public_base_url(config)
        models: list[AvailableModel] = []
        for deployment in deployments:
            if not _is_active_deployment(deployment):
                continue
            model_data = dict(deployment)
            model_data.setdefault("type", _infer_model_type(model_data))
            model_data.setdefault("capabilities", _infer_capabilities(model_data))
            endpoint = _deployment_openai_base(model_data, public_base)
            if endpoint:
                model_data["endpoint"] = endpoint
            models.append(AvailableModel.from_dict(model_data))
        return models
    return []


async def _resolve_openai_base(
    config: AuthConfig,
    forwarded_headers: dict[str, str],
) -> str:
    # _resolve_openai_base is consumed by get_model_client() which
    # builds an AsyncOpenAI instance that runs INSIDE the backend
    # container. Use the container-routable base, not the
    # browser-facing public URL — under `kz-ext dev local --auth` the
    # two can diverge (api_url=host.docker.internal, public_api_url=
    # localhost) and the backend container cannot reach its own
    # localhost. In production both URLs point at the same gateway so
    # the priority is a no-op there. PR #87 round-7 review (codex P1).
    container_base = _backend_runtime_base(config)
    if config.api_url:
        client = KamiwazaExtClient.from_env()
        try:
            deployments = await client.get_models(headers=forwarded_headers)
        except (httpx.HTTPError, OSError):
            deployments = []

        if isinstance(deployments, list):
            for deployment in deployments:
                if not _is_openai_compatible(deployment):
                    continue
                endpoint = _deployment_openai_base(deployment, container_base)
                if endpoint:
                    return endpoint

    return config.openai_base


def _normalize_openai_endpoint(endpoint: str) -> str:
    if not endpoint:
        return ""

    parsed = urlparse(endpoint.rstrip("/"))
    if parsed.path.startswith("/api/runtime/models/"):
        parsed = parsed._replace(
            path=parsed.path.replace("/api/runtime/models/", "/runtime/models/", 1)
        )
    return urlunparse(parsed).rstrip("/")


def _is_active_deployment(data: dict[str, Any]) -> bool:
    status = str(data.get("status", data.get("phase", ""))).strip().lower()
    if not status:
        return True
    return status in _ACTIVE_DEPLOYMENT_STATUSES


def _is_openai_compatible(data: dict[str, Any]) -> bool:
    if not _is_active_deployment(data):
        return False
    model_type = _infer_model_type(data)
    if model_type != "chat":
        return False
    access_path = str(data.get("access_path") or "").strip()
    if not access_path:
        return True
    return access_path.startswith("/runtime/models")


def _infer_model_type(data: dict[str, Any]) -> Optional[str]:
    explicit = data.get("type")
    if explicit:
        return str(explicit)

    access_path = str(data.get("access_path") or "").lower()
    engine = str(data.get("engine_name") or data.get("engine") or "").lower()
    container = str(data.get("container") or "").lower()
    name = str(data.get("m_name") or data.get("model_name") or data.get("name") or "").lower()

    if "transcribe" in engine or "transcribe" in name:
        return "audio"
    if "embedding" in access_path or "embedding" in container or "embedding" in name:
        return "embedding"
    if access_path.startswith("/runtime/models"):
        return "chat"
    return None


def _infer_capabilities(data: dict[str, Any]) -> list[str]:
    model_type = _infer_model_type(data)
    if model_type == "chat":
        return ["chat.completions"]
    if model_type == "embedding":
        return ["embeddings"]
    if model_type == "audio":
        return ["audio.transcriptions"]
    return []


def _deployment_openai_base(data: dict[str, Any], public_base: str) -> str:
    endpoint = str(data.get("endpoint") or "").rstrip("/")
    if endpoint:
        return _normalize_openai_endpoint(endpoint)

    access_path = str(data.get("access_path") or "").strip()
    if access_path and public_base:
        path = access_path if access_path.startswith("/") else f"/{access_path}"
        path = path.rstrip("/")
        if path.endswith("/v1"):
            return f"{public_base}{path}"
        return f"{public_base}{path}/v1"

    lb_port = data.get("lb_port")
    if public_base and lb_port:
        parsed = urlparse(public_base)
        scheme = parsed.scheme or "https"
        host = parsed.hostname
        if host:
            if lb_port == 443:
                return f"{scheme}://{host}/v1"
            return f"{scheme}://{host}:{lb_port}/v1"

    return ""
