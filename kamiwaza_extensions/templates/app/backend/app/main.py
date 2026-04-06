"""FastAPI backend for {{name}}."""

import os

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from kamiwaza_extensions_lib import (
    AuthConfig,
    Identity,
    create_session_router,
    get_model_client,
    list_available_models,
    require_auth,
)

app = FastAPI(title="{{name}}")

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
    return {"status": "ok", "app": "{{name}}"}


@app.get("/api/info")
async def info():
    config = AuthConfig.from_env()
    return {
        "app_name": config.app_name,
        "api_url": config.api_url,
        "use_auth": config.use_auth,
    }


@app.get("/api/models")
async def models(request: Request, identity: Identity = Depends(require_auth)):
    """List available models from the Kamiwaza platform."""
    return await list_available_models(request)


@app.post("/api/chat")
async def chat(request: Request, identity: Identity = Depends(require_auth)):
    """Example: call a model via the OpenAI-compatible client."""
    body = await request.json()
    client = await get_model_client(request)
    response = await client.chat.completions.create(
        model=body.get("model", "auto"),
        messages=body.get("messages", []),
    )
    return response.model_dump()
