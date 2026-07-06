"""Unit tests for the Arrow Flight streaming client."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("pyarrow.flight")

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

    def fake_connect(location, **kw):
        calls.append(location)
        return FakeClient(ok=(location == "grpc://good:6130"))

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)
    hs = GrpcHandshake(
        endpoints=[
            FlightEndpoint(location="grpc://bad:6130"),
            FlightEndpoint(location="grpc://good:6130"),
        ],
        token="t",
        expires_at=datetime.now(timezone.utc),
    )
    list(open_flight_stream(hs, job_id="00000000-0000-0000-0000-000000000001"))
    assert calls == ["grpc://bad:6130", "grpc://good:6130"]


def test_all_endpoints_fail_raises(monkeypatch):
    def fake_connect(location, **kw):
        raise Exception(f"conn refused at {location}")

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)
    hs = GrpcHandshake(
        endpoints=[FlightEndpoint(location="grpc://dead:6130")],
        token="t",
        expires_at=datetime.now(timezone.utc),
    )
    with pytest.raises(FlightUnavailableError):
        list(open_flight_stream(hs, job_id="00000000-0000-0000-0000-000000000002"))
