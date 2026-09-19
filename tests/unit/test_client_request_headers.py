"""Per-call headers: what one block sends, and what it must not reach.

`request_headers` exists for a value that names one request rather than one
connection — `Idempotency-Key` is the case it was written for. That shape is
why it is a context manager over a context variable rather than a copied
client: a copy gets its own session, and therefore its own connection pool,
so one copy per call means one TCP connection per call. The last test here
measures that, because it is the reason for the design and a later
refactor back to copying would pass every other test in this file.
"""

from __future__ import annotations

import gc
import threading
import weakref
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from kamiwaza_sdk.client import _SCOPED_HEADERS, KamiwazaClient

BASE_URL = "http://platform.invalid/api"
KEY = "8e03978e40d543e8bc936894a57f9324"


class _Recorder:
    """A stand-in for the session, keeping the headers each call was sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.verify = True
        self.headers: dict[str, str] = {}
        self.cookies: dict[str, str] = {}

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Record the headers and answer with an empty successful body."""
        self.sent.append(dict(kwargs.get("headers") or {}))
        return _Response()


class _Response:
    """The smallest response the client will accept."""

    status_code = 200
    headers = {"content-type": "application/json"}
    text = "{}"
    content = b"{}"

    def json(self) -> dict[str, Any]:
        """The parsed body."""
        return {}

    def raise_for_status(self) -> None:
        """Nothing to raise."""


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> KamiwazaClient:
    """A client whose transport records instead of connecting."""
    built = KamiwazaClient(base_url=BASE_URL)
    recorder = _Recorder()
    monkeypatch.setattr(built, "session", recorder)
    monkeypatch.setattr(built, "authenticator", None)
    return built


def _sent(client: KamiwazaClient) -> list[dict[str, str]]:
    """The headers of every call made through the recording transport."""
    return client.session.sent  # type: ignore[attr-defined]


def _keys(headers: dict[str, str]) -> list[str]:
    """The header names, lowercased, for a case-insensitive comparison."""
    return [name.lower() for name in headers]


def test_a_block_sends_its_header(client: KamiwazaClient) -> None:
    """The call inside the block carries it."""
    with client.request_headers({"Idempotency-Key": KEY}):
        client.get("widgets")

    assert _sent(client)[0]["Idempotency-Key"] == KEY


def test_the_header_is_gone_after_the_block(client: KamiwazaClient) -> None:
    """A key that outlived its block would be sent with a later, different act.

    This is the failure a client-level header has by construction, and the
    reason this is scoped rather than stored.
    """
    with client.request_headers({"Idempotency-Key": KEY}):
        client.get("widgets")
    client.get("widgets")

    assert "idempotency-key" not in _keys(_sent(client)[1])


def test_another_client_in_the_same_block_is_untouched(
    client: KamiwazaClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scope is one client, not the program."""
    other = KamiwazaClient(base_url=BASE_URL)
    monkeypatch.setattr(other, "session", _Recorder())
    monkeypatch.setattr(other, "authenticator", None)

    with client.request_headers({"Idempotency-Key": KEY}):
        other.get("widgets")

    assert "idempotency-key" not in _keys(_sent(other)[0])


def test_another_thread_is_untouched(client: KamiwazaClient) -> None:
    """One caller's key must not travel with a concurrent caller's request.

    A header held on the client would do exactly that, which is what makes
    the context variable load-bearing rather than stylistic.
    """
    seen: list[dict[str, str]] = []

    def call_outside() -> None:
        client.get("widgets")
        seen.append(_sent(client)[-1])

    with client.request_headers({"Idempotency-Key": KEY}):
        thread = threading.Thread(target=call_outside)
        thread.start()
        thread.join()

    assert "idempotency-key" not in _keys(seen[0])


def test_blocks_nest_and_the_inner_value_wins(client: KamiwazaClient) -> None:
    """An inner block refines the outer one rather than replacing it."""
    with client.request_headers({"X-Outer": "outer", "X-Both": "outer"}):
        with client.request_headers({"X-Both": "inner"}):
            client.get("widgets")

    headers = _sent(client)[0]
    assert headers["X-Outer"] == "outer"
    assert headers["X-Both"] == "inner"


def test_a_header_passed_to_the_call_wins(client: KamiwazaClient) -> None:
    """The caller's own header for one call beats the block's."""
    with client.request_headers({"Idempotency-Key": KEY}):
        client.get("widgets", headers={"Idempotency-Key": "mine"})

    assert _sent(client)[0]["Idempotency-Key"] == "mine"


def test_the_workroom_header_still_travels(client: KamiwazaClient) -> None:
    """A client default and a block header are sent together."""
    scoped = client.workroom_scope("wr-7")
    scoped.session = client.session  # type: ignore[assignment]
    scoped.authenticator = None

    with scoped.request_headers({"Idempotency-Key": KEY}):
        scoped.get("widgets")

    headers = _sent(client)[0]
    assert headers["X-Workroom-Id"] == "wr-7"
    assert headers["Idempotency-Key"] == KEY


def test_the_scope_does_not_grow_with_calls(client: KamiwazaClient) -> None:
    """Sequential blocks leave nothing behind.

    The scope is copied on entry and restored on exit, so its size is the
    number of blocks open at once rather than the number of calls made. A
    change that set the variable without restoring it would pass every other
    test here and leak a key onto every later call.
    """
    for index in range(1000):
        with client.request_headers({"Idempotency-Key": f"key-{index}"}):
            pass

    assert _SCOPED_HEADERS.get() == {}


def test_the_scope_is_restored_when_the_call_raises(client: KamiwazaClient) -> None:
    """An endpoint that blows up must not leave its key behind."""
    with pytest.raises(RuntimeError):
        with client.request_headers({"Idempotency-Key": KEY}):
            raise RuntimeError("the call failed")

    assert _SCOPED_HEADERS.get() == {}


def test_the_scope_holds_no_client_after_the_block() -> None:
    """The mapping keys on the client, so it must release it on exit.

    Keyed by the client object rather than its address, which a later object
    could reuse. That is only safe if the entry goes when the block does.
    """
    held = KamiwazaClient(base_url=BASE_URL)
    reference = weakref.ref(held)

    with held.request_headers({"Idempotency-Key": KEY}):
        assert len(_SCOPED_HEADERS.get()) == 1

    del held
    gc.collect()

    assert reference() is None, "the scope kept the client alive"


class _KeepAlive(BaseHTTPRequestHandler):
    """A server that holds the connection open, so reuse is observable."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        """Answer with an empty JSON body."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: Any) -> None:
        """Keep the test output clean."""


@pytest.mark.withoutresponses
def test_calls_in_a_block_reuse_the_connection() -> None:
    """The reason this is not a copied client.

    A copied client builds its own session and adapter, so one copy per call
    is one TCP connection per call — and over TLS, one handshake per call.
    Measured against a real socket rather than asserted.
    """
    connections: list[Any] = []

    class Counting(ThreadingHTTPServer):
        daemon_threads = True

        def get_request(self) -> Any:
            """Count each accepted connection."""
            sock, addr = super().get_request()
            connections.append(addr)
            return sock, addr

    server = Counting(("127.0.0.1", 0), _KeepAlive)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        live = KamiwazaClient(base_url=f"http://127.0.0.1:{port}/api")
        live.authenticator = None
        for index in range(5):
            with live.request_headers({"Idempotency-Key": f"key-{index}"}):
                live.get("widgets")
    finally:
        server.shutdown()

    assert len(connections) == 1, (
        f"five calls opened {len(connections)} connections; a header scope "
        f"that copies the client opens one per call"
    )
