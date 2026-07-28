"""Model discovery and client helpers for extensions."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import Request

from .auth import forward_auth_httpx_headers
from .client import KamiwazaExtClient
from .config import AuthConfig
from .local_dev import _is_loopback_ip

# Round-9 review: ``_url`` was renamed to ``url`` (public) in 0.4.0 so
# scaffolded extensions can import the helpers without coupling to a
# private path. The underscored aliases below preserve in-tree
# backward-compat for existing test imports.
from .url import (
    backend_runtime_base as _backend_runtime_base,  # noqa: F401
    public_base_url as _public_base_url,  # noqa: F401
)

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


async def get_model_client(request: Request):
    """Return an ``openai.AsyncOpenAI`` client for the platform model endpoint.

    The client's ``base_url`` points to ``KAMIWAZA_ENDPOINT`` and the
    user's auth headers are forwarded automatically. The caller owns the
    returned client and must close it with ``await client.close()``.

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
    wire_headers = forward_auth_httpx_headers(request.headers)
    openai_base = await _resolve_openai_base(config, wire_headers)
    if not openai_base:
        raise RuntimeError(
            "KAMIWAZA_ENDPOINT not configured. "
            "Are you running inside a Kamiwaza deployment?"
        )

    auth_header = wire_headers.get("authorization")

    # AsyncOpenAI always synthesizes its own Authorization header from api_key.
    # Reuse the forwarded bearer token there so the on-the-wire header matches
    # the incoming request instead of being replaced by a placeholder value.
    api_key = "not-needed-kamiwaza"
    if auth_header:
        prefix = "bearer "
        if auth_header.lower().startswith(prefix):
            api_key = auth_header[len(prefix) :]
        else:
            api_key = auth_header

    return AsyncOpenAI(
        base_url=openai_base,
        api_key=api_key,
        http_client=httpx.AsyncClient(
            headers=wire_headers,
            verify=config.httpx_verify(),
            trust_env=False,
        ),
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
    forwarded_headers: Mapping[str, str],
) -> str:
    # _resolve_openai_base is consumed by get_model_client() which
    # builds an AsyncOpenAI instance that runs INSIDE the backend
    # container. Model routes are built on _model_route_base: the
    # public/gateway base when it is container-reachable (in-cluster,
    # sub-path ingress, docker-mode with a real origin), or the
    # container-routable base under `kz-ext dev local --auth` where the
    # public host is browser-only localhost (PR #87 round-7 review,
    # codex P1; ENG-8766 review Critical — raw deployment payloads
    # carry only a relative ``access_path``, and building it onto the
    # k8s KAMIWAZA_API_URL targeted the Ray Serve proxy, which cannot
    # serve /runtime/models/*).
    route_base = _model_route_base(config)
    if config.api_url:
        client = KamiwazaExtClient.from_env()
        try:
            deployments = await client.get_models(headers=forwarded_headers)
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("Platform model endpoint discovery failed: %s", exc)
            deployments = []

        if isinstance(deployments, list):
            for deployment in deployments:
                if not _is_openai_compatible(deployment):
                    continue
                endpoint = _deployment_openai_base(
                    deployment,
                    route_base,
                    rehost_endpoint=True,
                )
                if endpoint:
                    return endpoint

    return config.openai_base


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


# Hosts that only make sense from the developer's browser/machine. Only
# these get re-hosted onto the container base — any other fully-qualified
# host is the platform's canonical URL for the model (e.g. the ingress
# gateway on k8s) and must be kept verbatim (ENG-8766).
# ``host.docker.internal`` is deliberately NOT here: it is the
# container-routable alias (the re-host *target* under kz-ext dev
# local), never a browser-only host.
_BROWSER_ONLY_HOSTS = frozenset({"localhost", "0.0.0.0"})


def _is_browser_only_host(host: str) -> bool:
    """True when ``host`` only means something on the developer machine.

    Combines the name set with :func:`local_dev._is_loopback_ip` so the
    full ``127.0.0.0/8`` range and IPv4-mapped IPv6 loopbacks match —
    not just the ``127.0.0.1`` literal (ENG-8766 re-review High #2; the
    supported dev-local loopback variants include e.g. ``127.0.0.2``).
    """
    if host in _BROWSER_ONLY_HOSTS:
        return True
    return bool(_is_loopback_ip(host))


def _model_route_base(config: AuthConfig) -> str:
    """Base URL on which relative model routes (``access_path``) live.

    Model routes (``/runtime/models/{id}``) are served by the platform's
    front proxy/gateway — NOT necessarily by the host in
    ``KAMIWAZA_API_URL``: on k8s that points at the Ray Serve proxy,
    which has no ``/runtime/models/*`` routes (the rewrite lives in
    per-deployment ingress VirtualServices; ENG-8766 review Critical).
    Prefer the public (gateway) base whenever its host means something
    outside the developer's browser; fall back to the container-routable
    base for ``kz-ext dev local`` where the public host is ``localhost``
    (there the container base fronts the same host-install gateway).
    """
    public = _public_base_url(config)
    if public:
        host = (urlparse(public).hostname or "").lower()
        if not _is_browser_only_host(host):
            return public
    return _backend_runtime_base(config)


def _should_rehost(parsed, target_parsed) -> bool:
    """True when ``parsed`` may be re-hosted onto ``target_parsed``.

    Browser-only hosts are unreachable from the container and need the
    swap; a same-netloc endpoint is already container-routable and only
    gets the sub-path prefix merge. Anything else is the platform's
    canonical model URL and must be kept verbatim (ENG-8766).
    """
    host = (parsed.hostname or "").lower()
    if _is_browser_only_host(host):
        return True
    return parsed.netloc.lower() == target_parsed.netloc.lower()


def _rehost_to_container(endpoint: str, container_base: str) -> str:
    """Re-host a browser-only endpoint onto the container-routable base.

    Round-12 review (codex P2): the platform may emit deployment
    ``endpoint`` fields with a browser-only host (``localhost`` under
    ``kz-ext dev local --auth``). When we're configuring the backend
    container's AsyncOpenAI client, those URLs are unreachable; swap
    scheme+netloc onto ``container_base`` while preserving any ingress
    sub-path (``/foo/runtime/...``) and avoiding the double-prepend the
    round-9/round-11 template fix already covers.

    ENG-8766: the swap applies ONLY when the endpoint's host is
    browser-only (loopback) or already equals the container base's
    netloc (the sub-path-ingress prefix-merge case). A fully-qualified
    endpoint on a different real host is the platform's canonical model
    URL — on k8s that's the ingress gateway, which owns the
    ``/runtime/models/{id}`` rewrite; re-hosting it onto
    ``KAMIWAZA_API_URL`` (the Ray Serve proxy) produced an unroutable
    URL and broke every in-cluster chat call.

    Returns the endpoint unchanged if either side lacks a
    scheme/netloc (e.g. relative URLs, malformed input).
    """
    if not endpoint:
        return ""
    parsed = urlparse(endpoint.rstrip("/"))
    if not container_base or not parsed.scheme or not parsed.netloc:
        return urlunparse(parsed).rstrip("/")
    target_parsed = urlparse(container_base)
    if not target_parsed.scheme or not target_parsed.netloc:
        return urlunparse(parsed).rstrip("/")
    if not _should_rehost(parsed, target_parsed):
        return urlunparse(parsed).rstrip("/")
    base_prefix = target_parsed.path.rstrip("/")
    already_prefixed = base_prefix and (
        parsed.path == base_prefix or parsed.path.startswith(base_prefix + "/")
    )
    merged_path = (
        parsed.path
        if (already_prefixed or not base_prefix)
        else f"{base_prefix}{parsed.path}"
    )
    parsed = parsed._replace(
        scheme=target_parsed.scheme,
        netloc=target_parsed.netloc,
        path=merged_path,
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
    name = str(
        data.get("m_name") or data.get("model_name") or data.get("name") or ""
    ).lower()

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


def _deployment_openai_base(
    data: dict[str, Any],
    target_base: str,
    *,
    rehost_endpoint: bool = False,
) -> str:
    """Build the OpenAI-compatible base URL for ``data``, routable from
    the audience implied by ``target_base``.

    ``rehost_endpoint=True`` swaps a platform-emitted ``endpoint``
    field's scheme+netloc onto ``target_base`` (round-12 review,
    codex P2) — used by the backend AsyncOpenAI path so a browser-only
    endpoint (``http://localhost:8000/...``) doesn't leak into the
    container's HTTP client. The frontend display path
    (``list_available_models``) keeps the platform's endpoint
    verbatim so URLs the user sees match what the platform reports.
    """
    endpoint = str(data.get("endpoint") or "").rstrip("/")
    if endpoint:
        if rehost_endpoint:
            return _rehost_to_container(
                _normalize_openai_endpoint(endpoint),
                target_base,
            )
        return _normalize_openai_endpoint(endpoint)

    access_path = str(data.get("access_path") or "").strip()
    if access_path and target_base:
        path = access_path if access_path.startswith("/") else f"/{access_path}"
        path = path.rstrip("/")
        if path.endswith("/v1"):
            return f"{target_base}{path}"
        return f"{target_base}{path}/v1"

    lb_port = data.get("lb_port")
    if target_base and lb_port:
        parsed = urlparse(target_base)
        scheme = parsed.scheme or "https"
        host = parsed.hostname
        if host:
            if lb_port == 443:
                return f"{scheme}://{host}/v1"
            return f"{scheme}://{host}:{lb_port}/v1"

    return ""
