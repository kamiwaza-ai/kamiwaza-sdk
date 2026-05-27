from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware

from .workroom_trust import (
    current_workroom_id as _current_workroom_id,
)
from .workroom_trust import (
    normalized_workroom_id as _normalized_workroom_id,
)
from .workroom_trust import (
    runtime_prefix as _runtime_prefix,
)
from .workroom_trust import (
    runtime_value as _runtime_value,
)
from .workroom_trust import (
    safe_log_field as _safe_log_field,
)
from .workroom_trust import (
    workroom_role as _workroom_role,
)

if TYPE_CHECKING:

    def create_session_router(prefix: str = "/api") -> APIRouter: ...

    def require_auth() -> Any: ...
else:
    from kamiwaza_extensions_lib import create_session_router, require_auth

logger = logging.getLogger("echo_check")


class AuthIdentity(Protocol):
    is_authenticated: bool
    user_id: str | None
    email: str | None
    name: str | None
    roles: list[str]
    workroom_role: str | None
    workroom_id: str | None


def _origin_from_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _cors_allowed_origins() -> list[str]:
    configured = _runtime_value("KAMIWAZA_CORS_ALLOWED_ORIGINS")
    if configured:
        return [origin for origin in (entry.strip() for entry in configured.split(",")) if origin]

    candidates = {
        _origin_from_url(_runtime_value("KAMIWAZA_PUBLIC_API_URL")),
        _origin_from_url(_runtime_value("KAMIWAZA_API_URL")),
        "http://localhost",
        "https://localhost",
        "https://kamiwaza.test",
    }
    return sorted(origin for origin in candidates if origin)


def _runtime_payload(request: Request) -> dict[str, Any]:
    return {
        "kamiwaza_app_path": _runtime_value("KAMIWAZA_APP_PATH"),
        "kamiwaza_deployment_id": _runtime_value("KAMIWAZA_DEPLOYMENT_ID"),
        "kamiwaza_public_api_url": _runtime_value("KAMIWAZA_PUBLIC_API_URL"),
        "kamiwaza_workroom_id": _runtime_value("KAMIWAZA_WORKROOM_ID"),
        "request_id": request.headers.get("x-request-id"),
        "root_path": request.scope.get("root_path") or "",
        "forwarded_prefix": request.headers.get("x-forwarded-prefix"),
        "forwarded_uri": request.headers.get("x-forwarded-uri"),
        "request_path": request.url.path,
    }


def _service_status() -> dict[str, str]:
    return {"service": "echo-check", "status": "ok"}


def _health_payload() -> dict[str, bool | str]:
    return {"status": "ok", "ready": True}


def _build_root_router(*, include_in_schema: bool) -> APIRouter:
    router = APIRouter()

    @router.get("/", include_in_schema=include_in_schema)
    async def root() -> dict[str, str]:
        return _service_status()

    @router.get("/health", include_in_schema=include_in_schema)
    async def health() -> dict[str, bool | str]:
        return _health_payload()

    return router


def _build_api_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/ready")
    async def ready() -> dict[str, bool | str]:
        return {"status": "ready", "ready": True}

    @router.get("/api/runtime")
    async def runtime(request: Request) -> dict[str, Any]:
        payload = _runtime_payload(request)
        payload["status"] = "ok"
        return payload

    @router.get("/api/observability")
    async def observability(
        request: Request,
        marker: str = Query(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
        identity: Any = Depends(require_auth),
    ) -> dict[str, Any]:
        typed_identity = cast(AuthIdentity, identity)
        payload = _runtime_payload(request)
        logger.info(
            "echo-check-observability marker=%s request_id=%s deployment_id=%s user_id=%s path=%s",
            marker,
            _safe_log_field(payload["request_id"]),
            _safe_log_field(payload["kamiwaza_deployment_id"]),
            _safe_log_field(typed_identity.user_id),
            _safe_log_field(request.url.path),
        )
        payload.update(
            {
                "status": "logged",
                "marker": marker,
                "authenticated": typed_identity.is_authenticated,
                "user_id": typed_identity.user_id,
            }
        )
        return payload

    @router.get("/api/whoami")
    async def whoami(
        request: Request,
        identity: Any = Depends(require_auth),
    ) -> dict[str, Any]:
        typed_identity = cast(AuthIdentity, identity)
        payload = _runtime_payload(request)
        payload.update(
            {
                "authenticated": typed_identity.is_authenticated,
                "user_id": typed_identity.user_id,
                "email": typed_identity.email,
                "name": typed_identity.name,
                "roles": typed_identity.roles,
                "current_workroom_id": _current_workroom_id(request, typed_identity),
                "workroom_role": _workroom_role(request, typed_identity),
            }
        )
        return payload

    @router.get("/api/workroom-check/{workroom_id}")
    async def workroom_check(
        request: Request,
        identity: Any = Depends(require_auth),
        workroom_id: str = PathParam(
            ...,
            min_length=36,
            max_length=36,
            pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
        ),
    ) -> dict[str, Any]:
        typed_identity = cast(AuthIdentity, identity)
        current_workroom_id = _current_workroom_id(request, typed_identity)
        normalized_workroom_id = _normalized_workroom_id(workroom_id)
        if not current_workroom_id or current_workroom_id != normalized_workroom_id:
            logger.warning(
                "echo-check-workroom-denied request_id=%s user_id=%s requested_workroom_id=%s current_workroom_id=%s path=%s",
                _safe_log_field(request.headers.get("x-request-id")),
                _safe_log_field(typed_identity.user_id),
                _safe_log_field(normalized_workroom_id),
                _safe_log_field(current_workroom_id),
                _safe_log_field(request.url.path),
            )
            raise HTTPException(status_code=403, detail="Bound workroom mismatch")
        payload = _runtime_payload(request)
        payload.update(
            {
                "authenticated": typed_identity.is_authenticated,
                "user_id": typed_identity.user_id,
                "current_workroom_id": current_workroom_id,
                "workroom_role": _workroom_role(request, typed_identity),
            }
        )
        return payload

    router.include_router(create_session_router(prefix="/api"))
    return router


def _register_routes(app: FastAPI) -> None:
    runtime_prefix = _runtime_prefix()
    if runtime_prefix:
        app.include_router(_build_root_router(include_in_schema=False), prefix=runtime_prefix)
        app.include_router(_build_api_router(), prefix=runtime_prefix)

        @app.get("/health", include_in_schema=False)
        async def container_health() -> dict[str, bool | str]:
            return _health_payload()

        return

    app.include_router(_build_root_router(include_in_schema=False))
    app.include_router(_build_api_router())


app = FastAPI(
    title="Echo Check",
    description="Minimal Kamiwaza contract app for extension compatibility checks",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_register_routes(app)
