"""Unit tests for the Arrow Flight streaming client."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Tests that don't need pyarrow at all
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit


def test_flight_batches_raises_when_no_grpc_handshake():
    """RetrievalService.flight_batches must raise TransportNotSupportedError when job.grpc is None."""
    from kamiwaza_sdk.exceptions import TransportNotSupportedError
    from kamiwaza_sdk.schemas.retrieval import (
        DatasetDescriptor,
        RetrievalJob,
        TransportType,
    )
    from kamiwaza_sdk.services.retrieval import RetrievalService

    mock_client = MagicMock()
    svc = RetrievalService(mock_client)
    job = RetrievalJob(
        job_id="00000000-0000-0000-0000-000000000099",
        transport=TransportType.GRPC,
        status="ready",
        dataset=DatasetDescriptor(urn="urn:li:dataset:test", platform="test"),
        grpc=None,
    )
    with pytest.raises(TransportNotSupportedError):
        svc.flight_batches(job)


# ---------------------------------------------------------------------------
# Schema tests (no pyarrow needed)
# ---------------------------------------------------------------------------


def test_grpchandshake_legacy_endpoint_lifted():
    """A legacy dict with a bare ``endpoint`` string is normalised to ``endpoints`` list."""
    from kamiwaza_sdk.schemas.retrieval import GrpcHandshake

    hs = GrpcHandshake.model_validate(
        {
            "endpoint": "0.0.0.0:6130",
            "token": "tok",
            "expires_at": "2099-01-01T00:00:00Z",
        }
    )
    assert len(hs.endpoints) == 1
    assert hs.endpoints[0].location == "0.0.0.0:6130"


def test_grpchandshake_missing_both_yields_empty():
    """When both ``endpoint`` and ``endpoints`` are absent, endpoints defaults to []."""
    from kamiwaza_sdk.schemas.retrieval import GrpcHandshake

    hs = GrpcHandshake.model_validate(
        {"token": "tok", "expires_at": "2099-01-01T00:00:00Z"}
    )
    assert hs.endpoints == []


# ---------------------------------------------------------------------------
# Tests that require pyarrow.flight
# ---------------------------------------------------------------------------

pytest.importorskip("pyarrow.flight")

from kamiwaza_sdk.schemas.retrieval import FlightEndpoint, GrpcHandshake  # noqa: E402
from kamiwaza_sdk.services.retrieval_flight import (  # noqa: E402
    FlightUnavailableError,
    open_flight_stream,
)


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


def test_flight_unavailable_error_importable_from_exceptions():
    """FlightUnavailableError must live in kamiwaza_sdk.exceptions."""
    from kamiwaza_sdk.exceptions import FlightUnavailableError as FUE  # noqa: F401
    from kamiwaza_sdk.services.retrieval_flight import FlightUnavailableError as FUE2

    # Both names must resolve to the same class
    assert FUE is FUE2
