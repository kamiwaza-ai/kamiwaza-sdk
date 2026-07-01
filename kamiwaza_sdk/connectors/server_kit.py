"""Shared HTTP server scaffold for connector workloads.

Every deployed connector exposes the same contract to core:

  GET  /healthz        liveness
  GET  /manifest       the connector's self-describing manifest (self-registration)
  POST /v1/execute     {op, subject_token, params} -> {body}
  POST /v1/verify      {subject_token} -> capability probe
  POST /v1/whoami      {access_token} -> {email?, name?, account_id?}   (optional)

Only three things vary per connector: its *provider* (for ``/manifest``), how it
builds its *dispatcher* from the workload env, and how it maps its own error types
to HTTP status codes. :func:`create_connector_app` owns everything else — so a
connector's ``server.py`` is just those bindings, and new connectors inherit the
whole contract (including self-registration) for free. This is the in-core form of
the deferred ``kamiwaza-connector-sdk``.

``/v1/whoami`` is the OAuth-2.0 (non-OIDC) identity fallback core invokes from
its OAuth callback for connectors whose token response carries no ``id_token``.
A dispatcher that implements ``async def whoami(access_token)`` gets the route
mounted automatically; one that doesn't responds 404 and core falls back to the
documented "Unknown account" UI label. The hook is optional and additive —
OIDC connectors (Google, M365) don't need it because the id_token already
carries the email.

Import-light (fastapi + pydantic only) so connector images can import it without
pulling the core service stack.
"""

from __future__ import annotations

import inspect
import logging
import os
import time
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


def accepted_params(fn: Callable[..., Any], params: dict[str, Any]) -> dict[str, Any]:
    """Drop params the op ``fn`` does not declare (unless it accepts ``**kwargs``).

    Core forwards content-routing hints (e.g. ``mime_type``) to every content op
    uniformly, and a node's ``content.query`` can carry provider-specific keys; an
    op that does not consume a given key must ignore it rather than fail with a
    ``TypeError``. A genuinely missing *required* param still raises when the op is
    called, so real contract mismatches stay loud.

    Framework behaviour so every connector dispatcher tolerates the platform's
    content-param contract without copy-pasting the filter. Call it at the dispatch
    call site: ``await fn(subject_token=tok, **accepted_params(fn, params))``.
    """
    signature = inspect.signature(fn)
    if any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
    ):
        return params
    allowed = {
        name
        for name, param in signature.parameters.items()
        if param.kind
        in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    }
    return {key: value for key, value in params.items() if key in allowed}


# One namespace for every connector so operators filter/format connector logs in
# one place. Op code should use ``get_connector_logger`` rather than the stdlib
# ``logging.getLogger`` so it lands under this namespace and inherits the handler.
_LOG = logging.getLogger("kamiwaza_sdk.connectors")


def get_connector_logger(name: str) -> logging.Logger:
    """A namespaced logger for connector op code (child of the framework logger).

    Logging discipline for connectors:

    - **Never** log a ``subject_token`` / ``access_token`` / provider bearer, or a
      raw param *value* -- params can carry user data (a search query, a file
      path). Log op names and param *keys* only.
    - Use ``exception``/``error`` for failures the operator must act on,
      ``warning`` for handled/expected faults, ``info`` for op outcomes, ``debug``
      for detail. The framework already logs every op's outcome + latency, so op
      code should add only what the framework can't see.
    """
    return _LOG.getChild(name)


def _resolve_log_level() -> int:
    """Numeric level from ``KAMIWAZA_CONNECTOR_LOG_LEVEL``; ``INFO`` on an unknown
    value so a typo'd env var can never crash connector app construction."""
    name = os.environ.get("KAMIWAZA_CONNECTOR_LOG_LEVEL", "INFO").upper()
    level = logging.getLevelName(name)
    return level if isinstance(level, int) else logging.INFO


def _ensure_connector_logging() -> None:
    """Configure the framework logger for a standalone connector app.

    A connector process is a standalone app, but uvicorn only configures its own
    loggers -- so without this the app's INFO logs are invisible and failures are
    swallowed behind the bare access line. Level and ``propagate`` are set
    **unconditionally** (a pre-existing handler must never leave the logger at the
    default WARNING and silently drop the INFO op-outcome lines); the stderr
    handler is attached once. ``propagate=False`` keeps lines from duplicating
    through uvicorn/root. Level from ``KAMIWAZA_CONNECTOR_LOG_LEVEL`` (default
    ``INFO``).
    """
    _LOG.setLevel(_resolve_log_level())
    _LOG.propagate = False
    if _LOG.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    _LOG.addHandler(handler)


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


class WhoamiRequest(BaseModel):
    """A request to resolve the connected account's identity from a fresh
    provider access token.

    Used by core's OAuth callback path for OAuth-2.0 (non-OIDC) providers that
    don't return an ``id_token``. The connector receives the just-minted
    access token and calls the provider's identity endpoint with it directly
    (the provider host is on the connector's egress allowlist), returning a
    standardized ``{email?, name?, account_id?}`` payload. The connector sees
    the raw token only for this one identity call — the OAuth callback has no
    user JWT, so core's proxy mint isn't available yet; after whoami the
    token is sealed into core's credential store and every subsequent
    provider call goes through the proxy.
    """

    # Live bearer token — keep it out of repr()/logs/tracebacks (matches the
    # connector_token bearer-field convention).
    access_token: str = Field(min_length=1, repr=False)


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
                # Null the ref BEFORE closing so a raising aclose() can't leave a
                # closed dispatcher on app.state for a later lifespan pass (restart
                # / repeated TestClient context) to serve through.
                disp = app.state.dispatcher
                app.state.dispatcher = None
                await disp.aclose()

    _ensure_connector_logging()
    app = FastAPI(title=title, lifespan=_lifespan)
    app.state.dispatcher = dispatcher

    def _error_response(exc: Exception) -> JSONResponse:
        status, kind, upstream = classify_error(exc)
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "kind": kind,
                    "message": str(exc),
                    "upstream_status": upstream,
                }
            },
        )

    def _log_outcome(
        op: str,
        started: float,
        *,
        params: dict[str, Any] | None = None,
        exc: Exception | None = None,
    ) -> None:
        """Log one op's outcome + latency. Secret-safe: param *keys* only, never
        values or tokens."""
        duration_ms = int((time.monotonic() - started) * 1000)
        keys = sorted(params) if params else []
        if exc is None:
            _LOG.info(
                "connector=%s op=%s params=%s outcome=ok dur_ms=%d",
                title,
                op,
                keys,
                duration_ms,
            )
            return
        status, kind, _ = classify_error(exc)
        _LOG.warning(
            "connector=%s op=%s params=%s outcome=error kind=%s status=%s "
            "dur_ms=%d error_type=%s",
            title,
            op,
            keys,
            kind,
            status,
            duration_ms,
            type(exc).__name__,
        )
        # Full exception text only at DEBUG: a provider error message can embed
        # upstream response fragments / user data, so it stays out of the default
        # WARNING line (operators opt in via KAMIWAZA_CONNECTOR_LOG_LEVEL=DEBUG).
        _LOG.debug("connector=%s op=%s error detail: %s", title, op, exc)

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
        started = time.monotonic()
        try:
            result = await request.app.state.dispatcher.execute(
                req.op, subject_token=req.subject_token, params=req.params
            )
        except error_type as exc:
            _log_outcome(req.op, started, params=req.params, exc=exc)
            return _error_response(exc)
        _log_outcome(req.op, started, params=req.params)
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
        started = time.monotonic()
        try:
            result = await request.app.state.dispatcher.verify(
                subject_token=req.subject_token
            )
        except error_type as exc:
            _log_outcome("verify", started, exc=exc)
            return _error_response(exc)
        _log_outcome("verify", started)
        return result

    @app.post("/v1/whoami")
    async def whoami(req: WhoamiRequest, request: Request) -> Any:
        """Resolve the connected account's identity (optional per dispatcher).

        Dispatchers without a ``whoami`` method respond 404 — core's connect
        fallback treats that the same as a connector that returned an empty
        payload (the UI keeps the documented "Unknown account" label until
        the connector ships the probe). Adding a connector for an OIDC
        provider does not require implementing this.
        """
        dispatcher = request.app.state.dispatcher
        handler = getattr(dispatcher, "whoami", None)
        # Absent OR non-callable (a stray truthy attribute) both mean "no probe"
        # -> the clean 404 contract, never a 500.
        if not callable(handler):
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "kind": "whoami_not_implemented",
                        "message": "connector does not expose a whoami probe",
                        "upstream_status": None,
                    }
                },
            )
        started = time.monotonic()
        try:
            result = await handler(access_token=req.access_token)
        except error_type as exc:
            _log_outcome("whoami", started, exc=exc)
            return _error_response(exc)
        _log_outcome("whoami", started)
        return result

    return app
