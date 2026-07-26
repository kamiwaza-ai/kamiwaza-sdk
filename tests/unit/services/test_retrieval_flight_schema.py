"""Pyarrow-free unit tests for retrieval flight client.

This module intentionally has NO ``pytest.importorskip("pyarrow.flight")`` so
that schema, exception, and guard tests run regardless of whether pyarrow is
installed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def test_grpchandshake_missing_both_yields_empty():
    """When both ``endpoint`` and ``endpoints`` are absent, endpoints defaults to []."""
    from kamiwaza_sdk.schemas.retrieval import GrpcHandshake

    hs = GrpcHandshake.model_validate(
        {"token": "tok", "expires_at": "2099-01-01T00:00:00Z"}
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
        {"token": "tok", "expires_at": "2099-01-01T00:00:00Z"}
    )
    assert hs.endpoints == []
    with pytest.raises(FlightUnavailableError, match="no Flight endpoints"):
        open_flight_stream(hs, job_id="00000000-0000-0000-0000-000000000002")


def test_invalid_timeout_raises_eagerly():
    from kamiwaza_sdk.schemas.retrieval import FlightEndpoint, GrpcHandshake
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    hs = GrpcHandshake(
        endpoints=[FlightEndpoint(location="grpc://host:6130")],
        token="tok",
        expires_at="2099-01-01T00:00:00Z",
    )

    with pytest.raises(ValueError, match="finite positive"):
        open_flight_stream(
            hs,
            job_id="00000000-0000-0000-0000-000000000003",
            timeout_seconds=0,
        )


# ---------------------------------------------------------------------------
# Fix #2: TLS bridging from client.session.verify
# ---------------------------------------------------------------------------


def test_flight_batches_tls_bridge_from_session_str_path():
    """When session.verify is a str path, bridge it as ca_cert_path in tls_kwargs."""
    from kamiwaza_sdk.schemas.retrieval import (
        DatasetDescriptor,
        FlightEndpoint,
        GrpcHandshake,
        RetrievalJob,
        TransportType,
    )
    from kamiwaza_sdk.services.retrieval import RetrievalService

    fake_session = MagicMock()
    fake_session.verify = "/etc/ssl/certs/ca-bundle.crt"
    fake_client = MagicMock()
    fake_client.session = fake_session

    svc = RetrievalService(fake_client)

    handshake = GrpcHandshake(
        endpoints=[FlightEndpoint(location="grpc://host:6130")],
        token="tok",
        expires_at=datetime.now(timezone.utc),
    )
    job = RetrievalJob(
        job_id="00000000-0000-0000-0000-000000000011",
        transport=TransportType.GRPC,
        status="ready",
        dataset=DatasetDescriptor(urn="urn:li:dataset:test", platform="test"),
        grpc=handshake,
    )

    captured: dict = {}

    def fake_open_flight_stream(hs, job_id, **kwargs):
        captured.update(kwargs)
        return iter([])

    with patch(
        "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
        fake_open_flight_stream,
    ):
        list(svc.flight_batches(job))

    assert captured.get("ca_cert_path") == "/etc/ssl/certs/ca-bundle.crt"


def test_flight_batches_tls_no_bridge_when_verify_is_bool():
    """When session.verify is bool (True/False), no ca_cert_path is injected."""
    from kamiwaza_sdk.schemas.retrieval import (
        DatasetDescriptor,
        FlightEndpoint,
        GrpcHandshake,
        RetrievalJob,
        TransportType,
    )
    from kamiwaza_sdk.services.retrieval import RetrievalService

    for verify_value in (True, False):
        fake_session = MagicMock()
        fake_session.verify = verify_value
        fake_client = MagicMock()
        fake_client.session = fake_session

        svc = RetrievalService(fake_client)

        handshake = GrpcHandshake(
            endpoints=[FlightEndpoint(location="grpc://host:6130")],
            token="tok",
            expires_at=datetime.now(timezone.utc),
        )
        job = RetrievalJob(
            job_id="00000000-0000-0000-0000-000000000012",
            transport=TransportType.GRPC,
            status="ready",
            dataset=DatasetDescriptor(urn="urn:li:dataset:test", platform="test"),
            grpc=handshake,
        )

        captured: dict = {}

        def fake_open_flight_stream(hs, job_id, **kwargs):
            captured.update(kwargs)
            return iter([])

        with patch(
            "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
            fake_open_flight_stream,
        ):
            list(svc.flight_batches(job))

        assert (
            "ca_cert_path" not in captured
        ), f"ca_cert_path must not be injected when verify={verify_value!r}"


def test_flight_batches_tls_explicit_wins_over_session():
    """Caller-supplied ca_cert_path takes precedence over session.verify bridge."""
    from kamiwaza_sdk.schemas.retrieval import (
        DatasetDescriptor,
        FlightEndpoint,
        GrpcHandshake,
        RetrievalJob,
        TransportType,
    )
    from kamiwaza_sdk.services.retrieval import RetrievalService

    fake_session = MagicMock()
    fake_session.verify = "/session/ca.pem"
    fake_client = MagicMock()
    fake_client.session = fake_session

    svc = RetrievalService(fake_client)

    handshake = GrpcHandshake(
        endpoints=[FlightEndpoint(location="grpc://host:6130")],
        token="tok",
        expires_at=datetime.now(timezone.utc),
    )
    job = RetrievalJob(
        job_id="00000000-0000-0000-0000-000000000013",
        transport=TransportType.GRPC,
        status="ready",
        dataset=DatasetDescriptor(urn="urn:li:dataset:test", platform="test"),
        grpc=handshake,
    )

    captured: dict = {}

    def fake_open_flight_stream(hs, job_id, **kwargs):
        captured.update(kwargs)
        return iter([])

    with patch(
        "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
        fake_open_flight_stream,
    ):
        list(svc.flight_batches(job, ca_cert_path="/explicit/ca.pem"))

    assert captured.get("ca_cert_path") == "/explicit/ca.pem"


def test_flight_batches_tls_bridge_accepts_pathlike():
    from kamiwaza_sdk.schemas.retrieval import (
        DatasetDescriptor,
        FlightEndpoint,
        GrpcHandshake,
        RetrievalJob,
        TransportType,
    )
    from kamiwaza_sdk.services.retrieval import RetrievalService

    fake_session = MagicMock()
    fake_session.verify = Path("/etc/ssl/certs/ca-bundle.crt")
    fake_client = MagicMock()
    fake_client.session = fake_session
    svc = RetrievalService(fake_client)
    job = RetrievalJob(
        job_id="00000000-0000-0000-0000-000000000014",
        transport=TransportType.GRPC,
        status="ready",
        dataset=DatasetDescriptor(urn="urn:li:dataset:test", platform="test"),
        grpc=GrpcHandshake(
            endpoints=[FlightEndpoint(location="grpc://host:6130")],
            token="tok",
            expires_at=datetime.now(timezone.utc),
        ),
    )
    captured: dict = {}

    def fake_open_flight_stream(hs, job_id, **kwargs):
        captured.update(kwargs)
        return iter([])

    with patch(
        "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
        fake_open_flight_stream,
    ):
        list(svc.flight_batches(job))

    assert captured["ca_cert_path"] == "/etc/ssl/certs/ca-bundle.crt"


def test_flight_batches_forwards_timeout():
    from kamiwaza_sdk.schemas.retrieval import (
        DatasetDescriptor,
        FlightEndpoint,
        GrpcHandshake,
        RetrievalJob,
        TransportType,
    )
    from kamiwaza_sdk.services.retrieval import RetrievalService

    svc = RetrievalService(MagicMock(session=None))
    job = RetrievalJob(
        job_id="00000000-0000-0000-0000-000000000015",
        transport=TransportType.GRPC,
        status="ready",
        dataset=DatasetDescriptor(urn="urn:li:dataset:test", platform="test"),
        grpc=GrpcHandshake(
            endpoints=[FlightEndpoint(location="grpc://host:6130")],
            token="tok",
            expires_at=datetime.now(timezone.utc),
        ),
    )
    captured: dict = {}

    def fake_open_flight_stream(hs, job_id, **kwargs):
        captured.update(kwargs)
        return iter([])

    with patch(
        "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
        fake_open_flight_stream,
    ):
        list(svc.flight_batches(job, timeout_seconds=45))

    assert captured["timeout_seconds"] == 45
