"""Shared HTTP server scaffold for connector workloads.

Every deployed connector exposes the same contract to core:

  GET  /healthz        liveness
  GET  /manifest       the connector's self-describing manifest (self-registration)
  POST /v1/execute     {op, subject_token, params} -> {body}
  POST /v1/verify      {subject_token} -> capability probe

Only three things vary per connector: its *provider* (for ``/manifest``), how it
builds its *dispatcher* from the workload env, and how it maps its own error types
to HTTP status codes. :func:`create_connector_app` owns everything else — so a
connector's ``server.py`` is just those bindings, and new connectors inherit the
whole contract (including self-registration) for free. This is the in-core form of
the deferred ``kamiwaza-connector-sdk``.

Import-light (fastapi + pydantic only) so connector images can import it without
pulling the core service stack.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Builds the connector's dispatcher from the workload env (fail-closed).
DispatcherFactory = Callable[[], Any]
# Maps a connector error -> (http_status, error_kind, upstream_status | None).
ErrorClassifier = Callable[[Exception], "tuple[int, str, int | None]"]


class ExecuteRequest(BaseModel):
    """A request to run one operation on behalf of an acting subject."""

    op: str = Field(min_length=1)
    subject_token: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # Reserved (not yet wired): an opaque resume position core would replay so a
    # state-aware connector continues where it left off (the request side of
    # OpResult.state). Accepted on the wire but not yet forwarded to the
    # dispatcher; wiring it through dispatcher.execute is a follow-up.
    state: str | None = None


class VerifyRequest(BaseModel):
    """A request to probe the connection's live capabilities."""

    subject_token: str | None = None


@dataclass
class OpResult:
    """An op result that carries framework-level continuation back to core.

    An op returns this (instead of a plain body) when it has continuation to hand
    back — so core can manage availability generically without parsing op bodies:

    - ``state``: an opaque, **serializable** resume position core persists and
      replays on the next call (the generalization of a page token). State lives in
      core, so **any replica** can serve the continuation — restart-anywhere.
    - ``session``: a reference to **live, non-serializable** state bound to this pod
      (an open stream / scroll / cursor). Core routes follow-ups back to the holding
      replica (sticky), and re-establishes if it dies.

    Ops with neither just return a plain body (``state``/``session`` come back null).
    """

    body: Any
    state: str | None = None
    session: str | None = None


def create_connector_app(
    *,
    title: str,
    provider: Any,
    build_dispatcher: DispatcherFactory,
    error_type: type[Exception],
    classify_error: ErrorClassifier,
    dispatcher: Any | None = None,
) -> FastAPI:
    """Build a connector's ASGI app with the standard core-facing contract.

    Args:
        title: FastAPI app title (e.g. ``"kamiwaza-connector-google"``).
        provider: the connector's provider; ``provider.to_manifest()`` (the full
            self-description, incl. the config JSON Schema) is served at
            ``GET /manifest`` for core self-registration — the same manifest the
            published catalog entry carries.
        build_dispatcher: builds the dispatcher from the workload env on startup
            (fail-closed). Not called when *dispatcher* is injected (tests).
        error_type: the connector's base error class to catch around dispatch.
        classify_error: maps a caught error to (http_status, kind, upstream_status).
        dispatcher: inject a dispatcher to bypass env-based construction (tests).
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        own = app.state.dispatcher is None
        if own:
            app.state.dispatcher = build_dispatcher()
        try:
            yield
        finally:
            if own:
                await app.state.dispatcher.aclose()
                # Clear the closed dispatcher so a later lifespan pass (restart /
                # repeated TestClient context) rebuilds instead of serving through
                # a closed one.
                app.state.dispatcher = None

    app = FastAPI(title=title, lifespan=_lifespan)
    app.state.dispatcher = dispatcher

    def _error_response(exc: Exception) -> JSONResponse:
        status, kind, upstream = classify_error(exc)
        return JSONResponse(
            status_code=status,
            content={
                "error": {"kind": kind, "message": str(exc), "upstream_status": upstream}
            },
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/manifest")
    async def manifest() -> Any:
        """The connector's self-describing manifest (core self-registration).

        Lets core learn this connector by asking it — ``core -> GET /manifest`` ->
        subscribe — instead of the manifest being hand-supplied. Returns the full
        manifest (identity/auth/egress/deployment + config_schema), identical to
        the published catalog entry, so self-registered and catalog connectors
        are the same.
        """
        return provider.to_manifest()

    @app.post("/v1/execute")
    async def execute(req: ExecuteRequest, request: Request) -> Any:
        try:
            result = await request.app.state.dispatcher.execute(
                req.op, subject_token=req.subject_token, params=req.params
            )
        except error_type as exc:
            return _error_response(exc)
        # An op may return an OpResult to hand continuation (state/session) back to
        # core; a plain return value is the body with no continuation. The
        # continuation keys are only added when present, so a stateless op's response
        # stays exactly {"body": ...}.
        if isinstance(result, OpResult):
            out: dict[str, Any] = {"body": result.body}
            if result.state is not None:
                out["state"] = result.state
            if result.session is not None:
                out["session"] = result.session
            return out
        return {"body": result}

    @app.post("/v1/verify")
    async def verify(req: VerifyRequest, request: Request) -> Any:
        try:
            return await request.app.state.dispatcher.verify(
                subject_token=req.subject_token
            )
        except error_type as exc:
            return _error_response(exc)

    return app
