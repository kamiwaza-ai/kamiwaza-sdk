"""Pyarrow-free unit tests for retrieval flight client.

This module intentionally has NO ``pytest.importorskip("pyarrow.flight")`` so
that schema, exception, and guard tests run regardless of whether pyarrow is
installed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# TransportNotSupportedError guard (moved from test_retrieval_flight.py)
# ---------------------------------------------------------------------------


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
# Schema legacy-lift tests (moved from test_retrieval_flight.py)
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
    assert hs.endpoint == "0.0.0.0:6130"
    assert hs.protocol == "kamiwaza.retrieval.v1"
    assert hs.model_dump()["endpoint"] == "0.0.0.0:6130"


def test_grpchandshake_legacy_endpoint_remains_mutable():
    from kamiwaza_sdk.schemas.retrieval import GrpcHandshake

    handshake = GrpcHandshake.model_validate(
        {
            "endpoint": "grpc://first:6130",
            "token": "tok",
            "expires_at": "2099-01-01T00:00:00Z",
        }
    )

    handshake.endpoint = "grpc://second:6130"

    assert handshake.endpoint == "grpc://second:6130"
    assert handshake.endpoints[0].location == "grpc://second:6130"
    assert handshake.model_dump()["endpoint"] == "grpc://second:6130"


def test_grpchandshake_repr_hides_single_use_token():
    from kamiwaza_sdk.schemas.retrieval import GrpcHandshake

    handshake = GrpcHandshake.model_validate(
        {
            "endpoint": "grpc://host:6130",
            "token": "do-not-log-this-token",
            "expires_at": "2099-01-01T00:00:00Z",
        }
    )

    assert "do-not-log-this-token" not in repr(handshake)


def test_grpchandshake_missing_both_yields_empty():
    """When both ``endpoint`` and ``endpoints`` are absent, endpoints defaults to []."""
    from kamiwaza_sdk.schemas.retrieval import GrpcHandshake

    hs = GrpcHandshake.model_validate(
        {
            "token": "tok",
            "expires_at": "2099-01-01T00:00:00Z",
            "protocol": "arrow-flight",
        }
    )
    assert hs.endpoints == []
    assert hs.endpoint is None


# ---------------------------------------------------------------------------
# Exception-identity test (moved from test_retrieval_flight.py)
# ---------------------------------------------------------------------------


def test_flight_unavailable_error_importable_from_exceptions():
    """FlightUnavailableError must live in kamiwaza_sdk.exceptions."""
    from kamiwaza_sdk.exceptions import FlightUnavailableError as FUE  # noqa: F401
    from kamiwaza_sdk.services.retrieval_flight import FlightUnavailableError as FUE2

    # Both names must resolve to the same class
    assert FUE is FUE2


# ---------------------------------------------------------------------------
# Fix #3: Protocol discriminator guard
# ---------------------------------------------------------------------------


def test_wrong_protocol_raises_transport_not_supported():
    """open_flight_stream must raise TransportNotSupportedError for non-arrow-flight protocol."""
    from kamiwaza_sdk.exceptions import TransportNotSupportedError
    from kamiwaza_sdk.schemas.retrieval import GrpcHandshake
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    hs = GrpcHandshake.model_validate(
        {
            "endpoints": [{"location": "grpc://host:6130"}],
            "token": "tok",
            "expires_at": "2099-01-01T00:00:00Z",
            "protocol": "kamiwaza.retrieval.v1",
        }
    )
    with pytest.raises(TransportNotSupportedError, match="kamiwaza.retrieval.v1"):
        open_flight_stream(hs, job_id="00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Fix #6: Empty-endpoints guard
# ---------------------------------------------------------------------------


def test_empty_endpoints_raises_flight_unavailable():
    """open_flight_stream must raise FlightUnavailableError when handshake has no endpoints."""
    from kamiwaza_sdk.exceptions import FlightUnavailableError
    from kamiwaza_sdk.schemas.retrieval import GrpcHandshake
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    hs = GrpcHandshake.model_validate(
        {
            "token": "tok",
            "expires_at": "2099-01-01T00:00:00Z",
            "protocol": "arrow-flight",
        }
    )
    assert hs.endpoints == []
    with pytest.raises(FlightUnavailableError, match="no Flight endpoints"):
        open_flight_stream(hs, job_id="00000000-0000-0000-0000-000000000002")


def test_invalid_timeout_raises_eagerly():
    from kamiwaza_sdk.schemas.retrieval import FlightEndpoint, GrpcHandshake
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    hs = GrpcHandshake(
        endpoints=[FlightEndpoint(location="grpc+tls://host:6130")],
        token="tok",
        expires_at="2099-01-01T00:00:00Z",
        protocol="arrow-flight",
    )

    with pytest.raises(ValueError, match="finite positive"):
        open_flight_stream(
            hs,
            job_id="00000000-0000-0000-0000-000000000003",
            timeout_seconds=0,
        )
