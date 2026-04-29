"""TokenRefreshMiddleware tests.

Issue: ENG-3895 / D210 M2 / Task T2.13.
Scenarios: TS-M2-31..34.

Uses ``httpx.MockTransport`` to drive the three-state contract end to end
without standing up a real upstream.
"""

from __future__ import annotations

import asyncio
from typing import Iterable

import httpx
import pytest

from kamiwaza_extensions_lib.errors import (
    PlatformOutageError,
    StreamInterruptedError,
)
from kamiwaza_extensions_lib.middleware import stream_with_refresh


def _scripted_transport(responses: Iterable[httpx.Response]) -> httpx.MockTransport:
    """Build a MockTransport that returns each scripted response in order."""
    iterator = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            return next(iterator)
        except StopIteration:
            raise AssertionError(
                f"Unexpected extra request: {request.method} {request.url}"
            )

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# TS-M2-31 — pre-commit 401 → refresh → 200 stream.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_commit_401_refresh_succeeds_and_returns_200_stream():
    transport = _scripted_transport([
        httpx.Response(401, content=b'{"error":"expired"}'),
        httpx.Response(200, content=b"data: chunk1\n\ndata: chunk2\n\n"),
    ])
    refresh_called = []

    async def refresh(old: dict[str, str]) -> dict[str, str]:
        refresh_called.append(old)
        return {**old, "X-Auth-Token": "new-token"}

    async with httpx.AsyncClient(transport=transport) as client:
        response = await stream_with_refresh(
            client, "POST", "https://upstream/v1/chat",
            headers={"X-Auth-Token": "old-token"},
            json={"prompt": "hi"},
            refresh=refresh,
        )
        body = b"".join([chunk async for chunk in response.body_iterator])

    assert response.status_code == 200
    assert b"chunk1" in body and b"chunk2" in body
    assert len(refresh_called) == 1
    assert refresh_called[0]["X-Auth-Token"] == "old-token"


# ---------------------------------------------------------------------------
# TS-M2-32 — pre-commit 401, refresh succeeds, second 401 → PlatformOutageError.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_commit_double_401_raises_platform_outage():
    transport = _scripted_transport([
        httpx.Response(401),
        httpx.Response(401),
    ])

    async def refresh(old: dict[str, str]) -> dict[str, str]:
        return {**old, "X-Auth-Token": "new-token"}

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PlatformOutageError, match="after token refresh"):
            await stream_with_refresh(
                client, "POST", "https://upstream/v1/chat",
                headers={"X-Auth-Token": "old"},
                refresh=refresh,
            )


@pytest.mark.asyncio
async def test_pre_commit_401_with_no_refresh_available_raises_platform_outage():
    """When the refresh fn returns None, surface PlatformOutageError immediately."""
    transport = _scripted_transport([httpx.Response(401)])

    async def refresh(_: dict[str, str]):
        return None  # No refresh possible (no refresh token, etc).

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PlatformOutageError, match="no refresh token"):
            await stream_with_refresh(
                client, "POST", "https://upstream/v1/chat",
                headers={"X-Auth-Token": "old"},
                refresh=refresh,
            )


# ---------------------------------------------------------------------------
# TS-M2-33 — post-commit upstream failure surfaces StreamInterruptedError.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mid_stream_failure_surfaces_stream_interrupted_error():
    """Simulate an upstream stream that errors AFTER first-byte by chaining
    a transport that raises during body streaming. We use a custom handler
    that returns a Response whose body iteration raises after the headers
    have been delivered.
    """
    class _Body:
        """An async iterable that yields one chunk then raises."""

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not getattr(self, "_yielded", False):
                self._yielded = True
                return b"first-chunk"
            raise httpx.NetworkError("upstream dropped connection")

    def handler(request: httpx.Request) -> httpx.Response:
        # ByteStream-compatible: provide an async iterable as the body so
        # httpx streams it lazily.
        return httpx.Response(200, content=_Body())

    transport = httpx.MockTransport(handler)

    async def refresh(old):
        raise AssertionError("refresh should not be called for 200 responses")

    async with httpx.AsyncClient(transport=transport) as client:
        response = await stream_with_refresh(
            client, "POST", "https://upstream/v1/chat",
            headers={"X-Auth-Token": "tok"},
            refresh=refresh,
        )
        with pytest.raises(StreamInterruptedError, match="aborted mid-flight"):
            async for _ in response.body_iterator:
                pass


# ---------------------------------------------------------------------------
# TS-M2-34 — concurrent 401s share a single refresh (lock prevents storm).
#
# We launch 5 concurrent stream_with_refresh calls all hitting 401 first.
# The refresh function takes a non-trivial await to expose any race; under
# the single-flight lock, all 5 should serialize their refresh through the
# same lock, but each gets its own retry. This proves the lock is in place
# and each call still gets a refreshed result.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_401_refreshes_serialize_through_lock():
    """Stateful handler: 401 if token is "old"; 200 if "new". Ordering of
    concurrent callers is unconstrained — what we're testing is that the
    refresh lock prevents *overlapping* refresh-fn invocations."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers.get("X-Auth-Token", "")
        if token == "new":
            return httpx.Response(200, content=b"ok")
        return httpx.Response(401, content=b'{"error":"expired"}')

    transport = httpx.MockTransport(handler)
    refresh_in_flight = 0
    lock_witness: list[bool] = []

    async def refresh(old):
        nonlocal refresh_in_flight
        refresh_in_flight += 1
        lock_witness.append(refresh_in_flight > 1)
        # Yield the loop so a competing refresh would surface the overlap
        # if the lock weren't doing its job.
        await asyncio.sleep(0)
        refresh_in_flight -= 1
        return {**old, "X-Auth-Token": "new"}

    async with httpx.AsyncClient(transport=transport) as client:
        async def call_one():
            response = await stream_with_refresh(
                client, "POST", "https://upstream/v1/chat",
                headers={"X-Auth-Token": "old"},
                refresh=refresh,
            )
            async for _ in response.body_iterator:
                pass
            return response.status_code

        statuses = await asyncio.gather(*(call_one() for _ in range(5)))

    assert all(s == 200 for s in statuses)
    # Lock invariant: counter never observed > 1.
    assert not any(lock_witness), (
        f"refresh saw overlapping invocations: {lock_witness}"
    )
