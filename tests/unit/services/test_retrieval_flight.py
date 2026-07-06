"""Unit tests for the Arrow Flight streaming client (pyarrow required).

Pyarrow-free tests live in test_retrieval_flight_schema.py so they run even
when pyarrow is absent.
"""
from __future__ import annotations

import pytest

# Must be at the top of the file so the entire module is skipped (not just
# individual tests) when pyarrow.flight is unavailable.
pytest.importorskip("pyarrow.flight")

from datetime import datetime, timezone  # noqa: E402

from kamiwaza_sdk.schemas.retrieval import FlightEndpoint, GrpcHandshake  # noqa: E402
from kamiwaza_sdk.services.retrieval_flight import (  # noqa: E402
    FlightUnavailableError,
    open_flight_stream,
)

pytestmark = pytest.mark.unit


def test_handshake_schema_parses_endpoints():
    hs = GrpcHandshake(
        endpoints=[{"location": "grpc://host:6130"}],
        token="tok",
        expires_at=datetime.now(timezone.utc),
    )
    assert len(hs.endpoints) == 1
    assert hs.endpoints[0].location == "grpc://host:6130"
    assert hs.protocol == "arrow-flight"


def test_open_flight_stream_tries_endpoints_in_order(monkeypatch):
    calls = []

    class FakeReader:
        def __iter__(self):
            return iter([])

        def read_all(self):
            return None

    class FakeClient:
        def __init__(self, ok):
            self.ok = ok

        def do_get(self, ticket, options=None):
            if not self.ok:
                raise Exception("unreachable")
            return FakeReader()

        def close(self):
            pass

    def fake_connect(location, **kw):
        calls.append(location)
        return FakeClient(ok=(location == "grpc://good:6130"))

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)
    monkeypatch.setattr("kamiwaza_sdk.services.retrieval_flight.time.sleep", lambda _: None)
    hs = GrpcHandshake(
        endpoints=[
            FlightEndpoint(location="grpc://bad:6130"),
            FlightEndpoint(location="grpc://good:6130"),
        ],
        token="t",
        expires_at=datetime.now(timezone.utc),
    )
    list(open_flight_stream(hs, job_id="00000000-0000-0000-0000-000000000001"))
    # The bad endpoint is retried _ENDPOINT_RETRY_ATTEMPTS times, then good succeeds once.
    from kamiwaza_sdk.services.retrieval_flight import _ENDPOINT_RETRY_ATTEMPTS

    assert calls.count("grpc://bad:6130") == _ENDPOINT_RETRY_ATTEMPTS
    assert calls.count("grpc://good:6130") == 1


def test_all_endpoints_fail_raises(monkeypatch):
    def fake_connect(location, **kw):
        raise Exception(f"conn refused at {location}")

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)
    monkeypatch.setattr("kamiwaza_sdk.services.retrieval_flight.time.sleep", lambda _: None)
    hs = GrpcHandshake(
        endpoints=[FlightEndpoint(location="grpc://dead:6130")],
        token="t",
        expires_at=datetime.now(timezone.utc),
    )
    with pytest.raises(FlightUnavailableError):
        list(open_flight_stream(hs, job_id="00000000-0000-0000-0000-000000000002"))


def test_midstream_failure_propagates_not_fallback(monkeypatch):
    """After yielding ≥1 batch, an error must propagate — not fall through to the next endpoint."""
    second_connect_called = []

    class FakeChunk:
        def __init__(self, data):
            self.data = data

    class FailAfterOneReader:
        def __iter__(self):
            yield FakeChunk("batch-0")
            raise RuntimeError("mid-stream boom")

    class FakeClient:
        def do_get(self, ticket, options=None):
            return FailAfterOneReader()

        def close(self):
            pass

    def fake_connect(location, **kw):
        if location == "grpc://second:6130":
            second_connect_called.append(location)
        return FakeClient()

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)
    hs = GrpcHandshake(
        endpoints=[
            FlightEndpoint(location="grpc://first:6130"),
            FlightEndpoint(location="grpc://second:6130"),
        ],
        token="t",
        expires_at=datetime.now(timezone.utc),
    )
    with pytest.raises(RuntimeError, match="mid-stream boom"):
        list(open_flight_stream(hs, job_id="00000000-0000-0000-0000-000000000003"))

    # The second endpoint must NOT have been tried
    assert second_connect_called == [], "fallback to second endpoint must not happen after mid-stream failure"


def test_per_endpoint_retry_on_prestream_failure(monkeypatch):
    """Pre-stream failures are retried up to _ENDPOINT_RETRY_ATTEMPTS times per endpoint."""
    from kamiwaza_sdk.services.retrieval_flight import _ENDPOINT_RETRY_ATTEMPTS

    connect_calls: list[str] = []

    class FakeReader:
        def __iter__(self):
            return iter([])

    class FakeClient:
        def __init__(self, fail: bool) -> None:
            self._fail = fail

        def do_get(self, ticket, options=None):
            if self._fail:
                raise ConnectionRefusedError("pre-stream fail")
            return FakeReader()

        def close(self):
            pass

    # First endpoint always fails (pre-stream); second always succeeds.
    def fake_connect(location, **kw):
        connect_calls.append(location)
        return FakeClient(fail=(location == "grpc://flaky:6130"))

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)
    # Suppress sleeps to keep the test fast.
    monkeypatch.setattr("kamiwaza_sdk.services.retrieval_flight.time.sleep", lambda _: None)

    hs = GrpcHandshake(
        endpoints=[
            FlightEndpoint(location="grpc://flaky:6130"),
            FlightEndpoint(location="grpc://good:6130"),
        ],
        token="t",
        expires_at=datetime.now(timezone.utc),
    )
    list(open_flight_stream(hs, job_id="00000000-0000-0000-0000-000000000004"))

    # Flaky endpoint should be attempted exactly _ENDPOINT_RETRY_ATTEMPTS times.
    assert connect_calls.count("grpc://flaky:6130") == _ENDPOINT_RETRY_ATTEMPTS
    # Good endpoint should succeed on the first attempt.
    assert connect_calls.count("grpc://good:6130") == 1
