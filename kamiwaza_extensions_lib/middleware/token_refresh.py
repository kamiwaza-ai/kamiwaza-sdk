"""Mid-stream-safe token-refresh middleware for upstream model calls (ENG-3895).

Three-state contract (see system design §4.2.7 + research D-R5):

1. ``PRE_COMMIT_OK`` — upstream returned 2xx, no bytes committed downstream.
   Stream through.
2. ``PRE_COMMIT_401`` — upstream returned 401 and no bytes were sent to the
   extension client. Refresh the user's token, retry once. On a second 401,
   raise :class:`PlatformOutageError`.
3. ``MID_STREAM_FAIL`` — upstream connection dropped or sent an error frame
   *after* bytes started flowing. The HTTP status is already committed; we
   cannot retry. Close the stream cleanly and let the SDK on the calling
   side surface a :class:`StreamInterruptedError`.

The trick that makes (1)/(2) safe is that ``httpx.AsyncClient.stream()``
materializes the response (status + headers) on context-manager entry but
defers body iteration. Inspecting ``response.status_code`` before iterating
gives us a pre-commit window in which a 401 retry is feasible.

Each caller invokes ``refresh`` with its own headers (one refresh per
caller, not shared across requests). A previous version held an
``asyncio.Lock`` to serialize refresh calls; that turned out to fan a
single user's refreshed headers out to other concurrent requests in the
same process when paired with shared state, and even without shared
state it created an unnecessary process-wide bottleneck (PR-86 C2).
The TypeScript sibling (``@kamiwaza-ai/extensions-lib``) takes the same
no-coordination approach.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Optional

import httpx

from kamiwaza_extensions_lib.errors import (
    PlatformOutageError,
    StreamInterruptedError,
)

logger = logging.getLogger(__name__)


# A refresh function takes the failing request's headers and returns a new
# headers dict (with refreshed X-Auth-Token / Authorization), or None if no
# refresh is possible (e.g. no refresh token, refresh endpoint down).
RefreshFn = Callable[[dict[str, str]], Awaitable[Optional[dict[str, str]]]]

# Hop-by-hop headers we never proxy back to the extension's caller.
# content-length is stripped because the ASGI server recomputes it from the
# streamed body (or sends Transfer-Encoding: chunked instead). content-
# encoding is stripped because we forward decoded bytes via aiter_bytes —
# claiming gzip on already-decoded content would mislead the client. ASGI
# itself does not compress; we simply mustn't claim a compression we no
# longer carry (PR-86 M5).
_HOP_BY_HOP = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
})

def _passthrough(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


class _UpstreamSession:
    """Owns an open ``httpx.AsyncClient.stream`` context. Single-use.

    We hold the context manager open across the status-code inspection and
    optional retry decision. Caller is responsible for ``aclose()`` on every
    code path — done in :func:`_drain` for the streaming path, and inline
    on the retry path.
    """

    __slots__ = ("_ctx", "resp")

    def __init__(self, ctx, resp: httpx.Response) -> None:
        self._ctx = ctx
        self.resp = resp

    async def aclose(self) -> None:
        await self._ctx.__aexit__(None, None, None)


async def _open(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json: Optional[dict] = None,
    content: Optional[bytes] = None,
) -> _UpstreamSession:
    ctx = client.stream(method, url, headers=headers, json=json, content=content)
    resp = await ctx.__aenter__()
    return _UpstreamSession(ctx, resp)


async def _read_and_close(session: _UpstreamSession) -> None:
    """Best-effort: drain a small error body, release the connection, exit ctx."""
    try:
        await session.resp.aread()
    except Exception:  # noqa: BLE001 — body read on an error response is best-effort.
        pass
    await session.aclose()


async def stream_with_refresh(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json: Optional[dict] = None,
    content: Optional[bytes] = None,
    refresh: RefreshFn,
):
    """Open an upstream stream with one transparent token-refresh retry.

    Returns a ``fastapi.responses.StreamingResponse``. The downstream status
    code is decided BEFORE any bytes are committed, so a 401 retry is safe.

    Imports ``StreamingResponse`` lazily so this module can be imported in
    test contexts that don't pull FastAPI in.
    """
    from fastapi.responses import StreamingResponse

    session = await _open(client, method, url, headers=headers, json=json, content=content)

    if session.resp.status_code == 401:
        # PRE_COMMIT_401 — refresh + retry once. Each caller invokes
        # refresh() with its own headers; no inter-caller coordination
        # (mirrors the TS sibling, PR-86 C2).
        await _read_and_close(session)
        new_headers = await refresh(headers)
        if new_headers is None:
            raise PlatformOutageError(
                "upstream 401 and no refresh token available"
            )
        session = await _open(
            client, method, url, headers=new_headers, json=json, content=content
        )
        if session.resp.status_code == 401:
            await _read_and_close(session)
            raise PlatformOutageError("upstream 401 after token refresh")

    # Status code is now known and is NOT a raw 401. Build the downstream
    # StreamingResponse. From this point bytes start flowing — any error
    # below is post-commit.
    return StreamingResponse(
        _drain(session),
        status_code=session.resp.status_code,
        headers=_passthrough(session.resp.headers),
        media_type=session.resp.headers.get("content-type"),
    )


async def _drain(session: _UpstreamSession) -> AsyncIterator[bytes]:
    """Forward upstream bytes, then close the session.

    Wrapped in try/finally so the connection is released even if the
    consumer (the ASGI server, ultimately) cancels the iteration.
    """
    try:
        # ``aiter_bytes`` decodes content-encoding (gzip/deflate) before
        # forwarding. The platform-side compression contract is not
        # extension-controlled, so re-emitting the decoded body is the
        # safest default. If a future use case wants byte-for-byte
        # passthrough, plumb a flag.
        #
        # Round-3 review M6: cross-language asymmetry. The Python middleware
        # decodes via ``aiter_bytes`` and strips ``content-encoding`` from
        # the response headers before downstream sees them; the TS sibling
        # raw-streams ``upstream.body`` (no decoding) but also strips
        # ``content-encoding``. Net behavior: downstream clients see
        # decoded bodies on the Py side and (potentially) compressed
        # bodies on the TS side, but neither side claims gzip in the
        # response. Operators reading both sides should know this; a
        # future revision could either decode in TS or document a
        # streaming-passthrough mode here.
        async for chunk in session.resp.aiter_bytes():
            yield chunk
    except httpx.HTTPError as exc:
        # MID_STREAM_FAIL — bytes were already committed downstream. The
        # status code is sealed; we can't retry. Surfacing as a typed
        # exception lets the calling code distinguish "connection dropped
        # mid-stream" from "platform 502."
        logger.error("upstream stream aborted mid-flight: %s", exc, exc_info=True)
        raise StreamInterruptedError(
            f"upstream stream aborted mid-flight: {exc}"
        ) from exc
    finally:
        await session.aclose()
