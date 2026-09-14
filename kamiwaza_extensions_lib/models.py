"""Model discovery and client helpers for extensions."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import ParseResult, urlparse, urlunparse

import httpx
from fastapi import Request

from .auth import forward_auth_httpx_headers
from .client import KamiwazaExtClient
from .config import AuthConfig
from .errors import UnexpectedContextError
from .local_dev import _is_loopback_ip
from .url import (
    CallbackTarget,
    backend_runtime_base,
    callback_target,
    public_base_url,
    registered_api_url,
)

# Transitional aliases retained for existing extension tests and imports.
_backend_runtime_base = backend_runtime_base
_public_base_url = public_base_url

_ACTIVE_DEPLOYMENT_STATUSES = {"deployed", "running", "ready", "active"}
logger = logging.getLogger(__name__)


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


async def get_model_client(request: Request, endpoint: str | None = None):
    """Return an ``openai.AsyncOpenAI`` client for a registered model route.

    ``KAMIWAZA_PLATFORM_GATEWAY_URL`` changes only the connection origin.
    Registered Host, path, TLS authority, and forwarded authorization remain
    unchanged. Pass ``endpoint`` to select a registered route explicitly.

    Raises:
        RuntimeError: If no model endpoint is configured or ``openai`` is absent.
        UnexpectedContextError: If callback transport configuration is invalid.
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise RuntimeError(
            "The 'openai' package is required for get_model_client(). "
            "Install it with: pip install openai>=1.0"
        )

    config = AuthConfig.from_env()
    wire_headers = forward_auth_httpx_headers(request.headers)
    openai_base = await _model_base(config, wire_headers, endpoint)
    if not openai_base:
        raise RuntimeError(
            "KAMIWAZA_ENDPOINT not configured. "
            "Are you running inside a Kamiwaza deployment?"
        )
    try:
        target = callback_target(openai_base, config.platform_gateway_url)
    except ValueError as exc:
        raise UnexpectedContextError(
            "KAMIWAZA_ENDPOINT or KAMIWAZA_PLATFORM_GATEWAY_URL "
            "is not valid callback configuration"
        ) from exc
    api_key = _openai_api_key(wire_headers.get("authorization"))

    return AsyncOpenAI(
        base_url=target.url,
        api_key=api_key,
        http_client=_model_http_client(config, wire_headers, target),
    )


async def _model_base(
    config: AuthConfig,
    wire_headers: httpx.Headers,
    endpoint: str | None,
) -> str:
    if not endpoint:
        return await _resolve_openai_base(config, wire_headers)
    registered = _normalize_openai_endpoint(endpoint)
    if config.platform_gateway_url.strip():
        return registered
    return _rehost_to_container(registered, backend_runtime_base(config))


def _openai_api_key(authorization: str | None) -> str:
    """Return bearer payload expected by ``AsyncOpenAI``."""
    if not authorization:
        return "not-needed-kamiwaza"
    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        return authorization[len(prefix) :]
    return authorization


def _model_http_client(
    config: AuthConfig,
    wire_headers: httpx.Headers,
    target: CallbackTarget,
) -> httpx.AsyncClient:
    headers = httpx.Headers(wire_headers.raw)
    headers["Host"] = target.host

    async def preserve_tls_authority(outbound: httpx.Request) -> None:
        outbound.extensions["sni_hostname"] = target.server_name

    return httpx.AsyncClient(
        headers=headers,
        verify=config.httpx_verify(),
        trust_env=False,
        event_hooks={"request": [preserve_tls_authority]},
    )


async def list_available_models(request: Request) -> list[AvailableModel]:
    """List models available to the current user.

    Calls ``GET /serving/deployments/active`` with the user's auth
    context and returns typed ``AvailableModel`` objects.

    Returns an empty list when no deployments are active or the
    platform API is not configured.
    """
    config = AuthConfig.from_env()
    if not (config.api_url or registered_api_url(config)):
        return []

    wire_headers = forward_auth_httpx_headers(request.headers)
    client = KamiwazaExtClient.from_env()
    try:
        deployments = await client.get_models(headers=wire_headers)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("Platform model discovery failed: %s", exc)
        return []

    if isinstance(deployments, list):
        # list_available_models returns endpoints intended for the
        # frontend / end-user display. Use the public (browser-facing)
        # base URL so the values surfaced to the UI are the URLs a user
        # would copy-paste or click — not the container-internal URL.
        public_base = public_base_url(config)
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
    forwarded_headers: Mapping[str, str],
) -> str:
    if not (config.api_url or registered_api_url(config)):
        return config.openai_base
    standard_transport = bool(config.platform_gateway_url.strip())
    route_base = (
        public_base_url(config) if standard_transport else _model_route_base(config)
    )
    deployments = await _load_model_deployments(forwarded_headers)
    endpoint = _first_openai_endpoint(
        deployments,
        route_base,
        rehost_endpoint=not standard_transport,
    )
    return endpoint or config.openai_base


async def _load_model_deployments(
    forwarded_headers: Mapping[str, str],
) -> list[dict[str, Any]]:
    client = KamiwazaExtClient.from_env()
    try:
        return await client.get_models(headers=forwarded_headers)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("Platform model endpoint discovery failed: %s", exc)
        return []


def _first_openai_endpoint(
    deployments: list[dict[str, Any]],
    route_base: str,
    *,
    rehost_endpoint: bool,
) -> str:
    for deployment in deployments:
        if not _is_openai_compatible(deployment):
            continue
        endpoint = _deployment_openai_base(
            deployment,
            route_base,
            rehost_endpoint=rehost_endpoint,
        )
        if endpoint:
            return endpoint
    return ""


def _normalize_openai_endpoint(endpoint: str) -> str:
    """Strip a leading ``/api`` from ``/api/runtime/models/...`` paths."""
    if not endpoint:
        return ""

    parsed = urlparse(endpoint.rstrip("/"))
    if parsed.path.startswith("/api/runtime/models/"):
        parsed = parsed._replace(
            path=parsed.path.replace("/api/runtime/models/", "/runtime/models/", 1)
        )
    return urlunparse(parsed).rstrip("/")


_BROWSER_ONLY_HOSTS = frozenset({"localhost", "0.0.0.0"})


def _is_browser_only_host(host: str) -> bool:
    if host in _BROWSER_ONLY_HOSTS:
        return True
    return bool(_is_loopback_ip(host))


def _model_route_base(config: AuthConfig) -> str:
    public = public_base_url(config)
    if public:
        host = (urlparse(public).hostname or "").lower()
        if not _is_browser_only_host(host):
            return public
    return backend_runtime_base(config)


def _should_rehost(parsed, target_parsed) -> bool:
    host = (parsed.hostname or "").lower()
    if _is_browser_only_host(host):
        return True
    return parsed.netloc.lower() == target_parsed.netloc.lower()


def _has_url_authority(parsed: ParseResult) -> bool:
    return bool(parsed.scheme and parsed.netloc)


def _merged_endpoint_path(path: str, base_path: str) -> str:
    prefix = base_path.rstrip("/")
    if not prefix:
        return path
    if path == prefix:
        return path
    if path.startswith(prefix + "/"):
        return path
    return f"{prefix}{path}"


def _rehost_to_container(endpoint: str, container_base: str) -> str:
    """Apply legacy container rehosting when standard transport is absent."""
    if not endpoint:
        return ""
    parsed = urlparse(endpoint.rstrip("/"))
    if not container_base:
        return urlunparse(parsed).rstrip("/")
    if not _has_url_authority(parsed):
        return urlunparse(parsed).rstrip("/")
    target_parsed = urlparse(container_base)
    if not _has_url_authority(target_parsed):
        return urlunparse(parsed).rstrip("/")
    if not _should_rehost(parsed, target_parsed):
        return urlunparse(parsed).rstrip("/")
    parsed = parsed._replace(
        scheme=target_parsed.scheme,
        netloc=target_parsed.netloc,
        path=_merged_endpoint_path(parsed.path, target_parsed.path),
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


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value:
            return str(value).lower()
    return ""


def _infer_model_type(data: dict[str, Any]) -> Optional[str]:
    explicit = data.get("type")
    if explicit:
        return str(explicit)

    access_path = _first_text(data, "access_path")
    engine = _first_text(data, "engine_name", "engine")
    container = _first_text(data, "container")
    name = _first_text(data, "m_name", "model_name", "name")

    if any("transcribe" in value for value in (engine, name)):
        return "audio"
    if any("embedding" in value for value in (access_path, container, name)):
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


def _deployment_openai_base(
    data: dict[str, Any],
    target_base: str,
    *,
    rehost_endpoint: bool = False,
) -> str:
    """Build an OpenAI-compatible URL for current and legacy runtimes."""
    endpoint = str(data.get("endpoint") or "").rstrip("/")
    if endpoint:
        normalized = _normalize_openai_endpoint(endpoint)
        if rehost_endpoint:
            return _rehost_to_container(normalized, target_base)
        return normalized
    access_endpoint = _access_path_openai_base(data, target_base)
    if access_endpoint:
        return access_endpoint
    return _load_balancer_openai_base(data, target_base)


def _access_path_openai_base(data: dict[str, Any], target_base: str) -> str:
    access_path = str(data.get("access_path") or "").strip()
    if not access_path or not target_base:
        return ""
    path = access_path if access_path.startswith("/") else f"/{access_path}"
    path = path.rstrip("/")
    if path.endswith("/v1"):
        return f"{target_base}{path}"
    return f"{target_base}{path}/v1"


def _load_balancer_openai_base(data: dict[str, Any], target_base: str) -> str:
    lb_port = data.get("lb_port")
    if not target_base or not lb_port:
        return ""
    parsed = urlparse(target_base)
    host = parsed.hostname
    if not host:
        return ""
    scheme = parsed.scheme or "https"
    if lb_port == 443:
        return f"{scheme}://{host}/v1"
    return f"{scheme}://{host}:{lb_port}/v1"
