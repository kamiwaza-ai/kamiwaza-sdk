"""FastAPI backend for chatbot-app."""

import logging
import os
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from kamiwaza_extensions_lib import (
    AuthConfig,
    Identity,
    create_session_router,
    forward_auth_headers,
    get_model_client,
    list_available_models,
    require_auth,
)
from openai import APIStatusError, AsyncOpenAI

app = FastAPI(title="chatbot-app")
logger = logging.getLogger(__name__)

cors_origin = os.environ.get("KAMIWAZA_ORIGIN", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session management endpoints (/session, /auth/login-url, /auth/logout)
app.include_router(create_session_router())


@app.get("/health")
async def health():
    return {"status": "ok", "app": "chatbot-app"}


@app.get("/api/info")
async def info():
    # Unauthenticated endpoint — keep the response narrow. `api_url` would
    # leak cluster-internal hostnames like `http://api:7777/api` to anyone
    # who can hit the extension's public surface (ENG-3920).
    config = AuthConfig.from_env()
    return {
        "app_name": config.app_name,
        "use_auth": config.use_auth,
    }


@app.get("/api/models")
async def models(request: Request, identity: Identity = Depends(require_auth)):
    """List available models from the Kamiwaza platform."""
    return await list_available_models(request)


def _pick_string(value):
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _public_base_url():
    config = AuthConfig.from_env()
    origin = _pick_string(config.origin)
    if origin:
        return origin.rstrip("/")

    app_url = _pick_string(config.app_url)
    app_path = _pick_string(config.app_path)
    if app_url and app_path and app_url.endswith(app_path):
        return app_url[: -len(app_path)].rstrip("/")

    public_api_url = _pick_string(config.public_api_url)
    if public_api_url:
        return public_api_url.removesuffix("/api").rstrip("/")

    api_url = _pick_string(config.api_url)
    if api_url:
        return api_url.removesuffix("/api").rstrip("/")

    return ""


def _normalize_model_endpoint(endpoint: str, access_path: str):
    public_base = _public_base_url()

    if access_path and public_base:
        normalized_path = access_path if access_path.startswith("/") else f"/{access_path}"
        normalized_path = normalized_path.rstrip("/")
        if normalized_path.endswith("/v1"):
            return f"{public_base}{normalized_path}"
        return f"{public_base}{normalized_path}/v1"

    if endpoint:
        parsed = urlparse(endpoint)
        if parsed.path.startswith("/api/runtime/models/"):
            return urlunparse(
                parsed._replace(path=parsed.path.replace("/api/runtime/models/", "/runtime/models/", 1))
            ).rstrip("/")

    return endpoint


async def _resolve_chat_target(request: Request, selected_model: str):
    if not selected_model:
        return None, ""

    available_models = await list_available_models(request)
    selected = selected_model.strip().lower()

    for model in available_models:
        candidates = {
            _pick_string(getattr(model, "id", "")).lower(),
            _pick_string(getattr(model, "name", "")).lower(),
            _pick_string(getattr(model, "repo_id", "")).lower(),
        }

        if selected not in candidates:
            continue

        extra = getattr(model, "_extra", {}) or {}
        endpoint = _normalize_model_endpoint(
            _pick_string(extra.get("endpoint")),
            _pick_string(extra.get("access_path")),
        )
        canonical_model = _pick_string(getattr(model, "name", "")) or selected_model.strip()
        return endpoint or None, canonical_model

    return None, selected_model.strip()


def _candidate_models(requested_model: str, resolved_model: str, endpoint: str | None):
    candidates = ["kamiwaza" if endpoint else requested_model, resolved_model, requested_model, "model", "auto"]
    seen: set[str] = set()
    unique: list[str] = []

    for candidate in candidates:
        value = _pick_string(candidate)
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)

    return unique


async def _build_chat_client(request: Request, endpoint: str | None):
    if not endpoint:
        return await get_model_client(request)

    config = AuthConfig.from_env()
    forwarded_headers = forward_auth_headers(request.headers)

    auth_header = None
    passthrough_headers: dict[str, str] = {}
    for key, value in forwarded_headers.items():
        if key.lower() == "authorization":
            auth_header = value
        else:
            passthrough_headers[key] = value

    api_key = "not-needed-kamiwaza"
    if auth_header:
        prefix = "bearer "
        if auth_header.lower().startswith(prefix):
            api_key = auth_header[len(prefix):]
        else:
            api_key = auth_header

    return AsyncOpenAI(
        base_url=endpoint,
        api_key=api_key,
        default_headers=passthrough_headers,
        http_client=httpx.AsyncClient(verify=config.verify_ssl),
    )


@app.post("/api/chat")
async def chat(request: Request, identity: Identity = Depends(require_auth)):
    """Call the selected model via the OpenAI-compatible client."""
    body = await request.json()
    requested_model = _pick_string(body.get("model")) or "auto"
    endpoint, resolved_model = await _resolve_chat_target(request, requested_model)
    client = await _build_chat_client(request, endpoint)
    attempted_models = _candidate_models(requested_model, resolved_model, endpoint)
    last_error: APIStatusError | None = None

    for model_name in attempted_models:
        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=body.get("messages", []),
            )
            return response.model_dump()
        except APIStatusError as exc:
            last_error = exc
            logger.warning(
                "Chat completion failed for requested_model=%s resolved_model=%s attempt_model=%s endpoint=%s status=%s",
                requested_model,
                resolved_model,
                model_name,
                endpoint or "<default>",
                exc.status_code,
            )
            if exc.status_code != 404:
                detail = None
                if isinstance(exc.body, dict):
                    detail = exc.body.get("detail") or exc.body.get("message")
                raise HTTPException(
                    status_code=exc.status_code or 502,
                    detail=detail or f"Upstream model request failed with status {exc.status_code}.",
                ) from exc

    detail = None
    if last_error and isinstance(last_error.body, dict):
        detail = last_error.body.get("detail") or last_error.body.get("message")
    raise HTTPException(
        status_code=last_error.status_code if last_error else 502,
        detail=detail
        or f"Unable to reach the selected model after trying: {', '.join(attempted_models)}.",
    )
