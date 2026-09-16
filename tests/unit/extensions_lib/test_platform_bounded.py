"""Bounded transport tests use chunked streams, not already-buffered bodies."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from starlette.datastructures import Headers

from kamiwaza_extensions_lib import platform as platform_module
from kamiwaza_extensions_lib.errors import (
    PlatformRedirectError,
    PlatformResponseTooLargeError,
    UnexpectedContextError,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, cancel=False):
        self.chunks, self.cancel, self.read, self.closed = chunks, cancel, 0, False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += 1
            if self.cancel:
                raise asyncio.CancelledError()
            yield chunk

    async def aclose(self):
        self.closed = True


def setup(monkeypatch, stream, status=200, headers=None):
    monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, headers=headers, stream=stream)

    transport = httpx.MockTransport(handler)
    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        platform_module.httpx,
        "AsyncClient",
        lambda **kw: client_class(transport=transport, **kw),
    )
    return seen


async def call(limit=4):
    return await platform_module.platform_request(
        SimpleNamespace(
            headers=Headers({"X-User-Id": "reader", "X-Workroom-Id": "room"})
        ),
        "GET",
        "/api/source/content",
        max_response_bytes=limit,
    )


async def test_exact_limit_preserves_response_and_envelope(monkeypatch):
    stream = Chunks([b"ab", b"cd"])
    seen = setup(monkeypatch, stream, headers={"content-type": "text/plain"})
    response = await call()
    assert response.content == b"abcd" and response.status_code == 200
    assert response.headers["content-type"] == "text/plain"
    assert seen[0].headers["accept-encoding"] == "identity"
    assert seen[0].headers["x-user-id"] == "reader"
    assert seen[0].headers["x-workroom-id"] == "room"
    assert stream.closed


@pytest.mark.parametrize("status", [200, 401, 500])
@pytest.mark.parametrize("headers", [{}, {"content-length": "1"}])
async def test_overflow_closes_before_reading_rest(monkeypatch, status, headers):
    stream = Chunks([b"ab", b"cde", b"never-read"])
    setup(monkeypatch, stream, status, headers)
    with pytest.raises(PlatformResponseTooLargeError):
        await call()
    assert stream.closed and stream.read == 2


async def test_redirect_body_is_never_read(monkeypatch):
    stream = Chunks([b"sensitive"])
    seen = setup(monkeypatch, stream, 302, {"location": "https://elsewhere.invalid/"})
    with pytest.raises(PlatformRedirectError):
        await call()
    assert stream.closed and stream.read == 0 and len(seen) == 1


async def test_compression_rejected_before_body(monkeypatch):
    stream = Chunks([b"compressed"])
    setup(monkeypatch, stream, headers={"content-encoding": "gzip"})
    with pytest.raises(UnexpectedContextError):
        await call()
    assert stream.closed and stream.read == 0


async def test_cancel_closes_stream(monkeypatch):
    stream = Chunks([b"a"], cancel=True)
    setup(monkeypatch, stream)
    with pytest.raises(asyncio.CancelledError):
        await call()
    assert stream.closed


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
async def test_invalid_limit_never_requests(monkeypatch, limit):
    stream = Chunks([])
    seen = setup(monkeypatch, stream)
    with pytest.raises(ValueError):
        await call(limit)
    assert not seen
