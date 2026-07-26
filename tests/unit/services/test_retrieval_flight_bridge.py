"""High-level Flight TLS bridging and option forwarding tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# TLS bridging from client.session.verify
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
    svc.get_job = MagicMock(return_value=MagicMock(status="COMPLETED"))

    handshake = GrpcHandshake(
        endpoints=[FlightEndpoint(location="grpc+tls://host:6130")],
        token="tok",
        expires_at="2099-01-01T00:00:00Z",
        protocol="arrow-flight",
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
        svc.get_job = MagicMock(return_value=MagicMock(status="COMPLETED"))

        handshake = GrpcHandshake(
            endpoints=[FlightEndpoint(location="grpc+tls://host:6130")],
            token="tok",
            expires_at="2099-01-01T00:00:00Z",
            protocol="arrow-flight",
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

        assert captured["ca_cert_path"] is None, (
            f"ca_cert_path must stay unset when verify={verify_value!r}"
        )


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
    svc.get_job = MagicMock(return_value=MagicMock(status="COMPLETED"))

    handshake = GrpcHandshake(
        endpoints=[FlightEndpoint(location="grpc+tls://host:6130")],
        token="tok",
        expires_at="2099-01-01T00:00:00Z",
        protocol="arrow-flight",
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
    svc.get_job = MagicMock(return_value=MagicMock(status="COMPLETED"))
    job = RetrievalJob(
        job_id="00000000-0000-0000-0000-000000000014",
        transport=TransportType.GRPC,
        status="ready",
        dataset=DatasetDescriptor(urn="urn:li:dataset:test", platform="test"),
        grpc=GrpcHandshake(
            endpoints=[FlightEndpoint(location="grpc+tls://host:6130")],
            token="tok",
            expires_at="2099-01-01T00:00:00Z",
            protocol="arrow-flight",
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
    svc.get_job = MagicMock(return_value=MagicMock(status="COMPLETED"))
    job = RetrievalJob(
        job_id="00000000-0000-0000-0000-000000000015",
        transport=TransportType.GRPC,
        status="ready",
        dataset=DatasetDescriptor(urn="urn:li:dataset:test", platform="test"),
        grpc=GrpcHandshake(
            endpoints=[FlightEndpoint(location="grpc+tls://host:6130")],
            token="tok",
            expires_at="2099-01-01T00:00:00Z",
            protocol="arrow-flight",
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
        list(
            svc.flight_batches(
                job,
                timeout_seconds=45,
                allow_insecure=True,
            )
        )

    assert captured["timeout_seconds"] == 45
    assert captured["allow_insecure"] is True


def test_flight_batches_insecure_opt_in_skips_implicit_http_ca_bridge():
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
    service = RetrievalService(fake_client)
    service.get_job = MagicMock(return_value=MagicMock(status="COMPLETED"))
    job = RetrievalJob(
        job_id="job-insecure",
        transport=TransportType.GRPC,
        status="ready",
        dataset=DatasetDescriptor(urn="urn:test", platform="test"),
        grpc=GrpcHandshake(
            endpoints=[FlightEndpoint(location="grpc://localhost:6130")],
            token="tok",
            expires_at="2099-01-01T00:00:00Z",
            protocol="arrow-flight",
        ),
    )
    captured: dict = {}

    def fake_open_flight_stream(handshake, job_id, **kwargs):
        captured.update(kwargs)
        return iter(())

    with patch(
        "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
        fake_open_flight_stream,
    ):
        list(service.flight_batches(job, allow_insecure=True))

    assert captured["ca_cert_path"] is None


# ---------------------------------------------------------------------------
