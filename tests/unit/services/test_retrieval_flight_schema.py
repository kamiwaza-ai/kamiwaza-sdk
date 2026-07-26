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
    assert hs.protocol == "kamiwaza.retrieval.v1"


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


# ---------------------------------------------------------------------------
# Conservative protocol, expiry, and endpoint-security guards
# ---------------------------------------------------------------------------


def _flight_handshake(location: str = "grpc+tls://host:6130"):
    from kamiwaza_sdk.schemas.retrieval import GrpcHandshake

    return GrpcHandshake.model_validate(
        {
            "endpoints": [{"location": location}],
            "token": "tok",
            "expires_at": "2099-01-01T00:00:00Z",
            "protocol": "arrow-flight",
        }
    )


def test_omitted_protocol_defaults_to_legacy_and_is_rejected_before_pyarrow():
    from kamiwaza_sdk.exceptions import TransportNotSupportedError
    from kamiwaza_sdk.schemas.retrieval import GrpcHandshake
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    handshake = GrpcHandshake.model_validate(
        {
            "endpoints": [{"location": "grpc+tls://host:6130"}],
            "token": "tok",
            "expires_at": "2099-01-01T00:00:00Z",
        }
    )

    with (
        patch(
            "kamiwaza_sdk.services.retrieval_flight._require_flight",
            side_effect=AssertionError("pyarrow must not be loaded"),
        ),
        pytest.raises(TransportNotSupportedError, match="kamiwaza.retrieval.v1"),
    ):
        open_flight_stream(handshake, job_id="job")


@pytest.mark.parametrize("protocol", ["", "kamiwaza.retrieval.v1"])
def test_only_explicit_arrow_flight_protocol_is_accepted(protocol):
    from kamiwaza_sdk.exceptions import TransportNotSupportedError
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    handshake = _flight_handshake()
    handshake.protocol = protocol

    with pytest.raises(TransportNotSupportedError):
        open_flight_stream(handshake, job_id="job")


def test_expired_handshake_is_rejected_before_connect():
    from kamiwaza_sdk.exceptions import AuthenticationError
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    handshake = _flight_handshake()
    handshake.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)

    with (
        patch(
            "kamiwaza_sdk.services.retrieval_flight._require_flight",
            side_effect=AssertionError("pyarrow must not be loaded"),
        ),
        pytest.raises(AuthenticationError, match="expired"),
    ):
        open_flight_stream(handshake, job_id="job")


def test_plaintext_is_rejected_by_default_before_pyarrow():
    from kamiwaza_sdk.exceptions import InsecureFlightEndpointError
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    with (
        patch(
            "kamiwaza_sdk.services.retrieval_flight._require_flight",
            side_effect=AssertionError("pyarrow must not be loaded"),
        ),
        pytest.raises(InsecureFlightEndpointError, match="allow_insecure=True"),
    ):
        open_flight_stream(_flight_handshake("grpc://host:6130"), job_id="job")


def test_all_endpoint_security_is_validated_before_pyarrow():
    from kamiwaza_sdk.exceptions import InsecureFlightEndpointError
    from kamiwaza_sdk.schemas.retrieval import FlightEndpoint
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    handshake = _flight_handshake()
    handshake.endpoints.append(FlightEndpoint(location="grpc://host:6130"))

    with (
        patch(
            "kamiwaza_sdk.services.retrieval_flight._require_flight",
            side_effect=AssertionError("pyarrow must not be loaded"),
        ),
        pytest.raises(InsecureFlightEndpointError),
    ):
        open_flight_stream(handshake, job_id="job")


def test_plaintext_requires_opt_in_and_warns():
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    fake_flight = MagicMock()
    with (
        patch(
            "kamiwaza_sdk.services.retrieval_flight._require_flight",
            return_value=fake_flight,
        ),
        pytest.warns(UserWarning, match="insecure plaintext"),
    ):
        stream = open_flight_stream(
            _flight_handshake("grpc://localhost:6130"),
            job_id="job",
            allow_insecure=True,
        )

    assert iter(stream) is stream
    fake_flight.Ticket.assert_called_once()


def test_plaintext_with_ca_fails_even_when_opted_in():
    from kamiwaza_sdk.exceptions import FlightConfigurationError
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    with pytest.raises(FlightConfigurationError, match="cannot be used"):
        open_flight_stream(
            _flight_handshake("grpc://localhost:6130"),
            job_id="job",
            tls_root_certs=b"CA",
            allow_insecure=True,
        )


def test_empty_raw_ca_is_rejected():
    from kamiwaza_sdk.exceptions import FlightConfigurationError
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    with pytest.raises(FlightConfigurationError, match="empty"):
        open_flight_stream(
            _flight_handshake(),
            job_id="job",
            tls_root_certs=b"",
        )


def test_empty_ca_file_is_rejected(tmp_path):
    from kamiwaza_sdk.exceptions import FlightConfigurationError
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    ca_path = tmp_path / "empty.pem"
    ca_path.touch()

    with pytest.raises(FlightConfigurationError, match="empty"):
        open_flight_stream(
            _flight_handshake(),
            job_id="job",
            ca_cert_path=ca_path,
        )


def test_unknown_endpoint_scheme_is_rejected():
    from kamiwaza_sdk.exceptions import FlightConfigurationError
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    with pytest.raises(FlightConfigurationError, match="Unsupported"):
        open_flight_stream(_flight_handshake("http://host:6130"), job_id="job")


# ---------------------------------------------------------------------------
# High-level clean-EOF completion verification
# ---------------------------------------------------------------------------


def _retrieval_job():
    from kamiwaza_sdk.schemas.retrieval import (
        DatasetDescriptor,
        RetrievalJob,
        TransportType,
    )

    return RetrievalJob(
        job_id="job-completion",
        transport=TransportType.GRPC,
        status="STREAMING",
        dataset=DatasetDescriptor(urn="urn:test", platform="test"),
        grpc=_flight_handshake(),
    )


def test_flight_batches_verifies_completed_after_clean_exhaustion():
    from kamiwaza_sdk.services.retrieval import RetrievalService

    service = RetrievalService(MagicMock(session=None))
    service.get_job = MagicMock(return_value=MagicMock(status="COMPLETED"))

    with patch(
        "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
        return_value=iter(["batch"]),
    ):
        assert list(service.flight_batches(_retrieval_job())) == ["batch"]

    service.get_job.assert_called_once_with("job-completion")


def test_flight_batches_rejects_clean_eof_before_completed():
    from kamiwaza_sdk.exceptions import FlightIncompleteStreamError
    from kamiwaza_sdk.services.retrieval import RetrievalService

    service = RetrievalService(MagicMock(session=None))
    service.get_job = MagicMock(return_value=MagicMock(status="STREAMING"))

    with (
        patch(
            "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
            return_value=iter(()),
        ),
        pytest.raises(FlightIncompleteStreamError) as exc_info,
    ):
        list(service.flight_batches(_retrieval_job()))

    assert exc_info.value.job_id == "job-completion"
    assert exc_info.value.status == "STREAMING"


def test_flight_batches_wraps_completion_lookup_failure():
    from kamiwaza_sdk.exceptions import FlightIncompleteStreamError, KamiwazaError
    from kamiwaza_sdk.services.retrieval import RetrievalService

    original_error = KamiwazaError("status unavailable")
    service = RetrievalService(MagicMock(session=None))
    service.get_job = MagicMock(side_effect=original_error)

    with (
        patch(
            "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
            return_value=iter(()),
        ),
        pytest.raises(FlightIncompleteStreamError) as exc_info,
    ):
        list(service.flight_batches(_retrieval_job()))

    assert exc_info.value.status is None
    assert exc_info.value.__cause__ is original_error


def test_flight_batches_early_close_skips_completion_check():
    from kamiwaza_sdk.services.retrieval import RetrievalService

    closed: list[bool] = []

    def source():
        try:
            yield "batch"
            yield "unused"
        finally:
            closed.append(True)

    service = RetrievalService(MagicMock(session=None))
    service.get_job = MagicMock()
    with patch(
        "kamiwaza_sdk.services.retrieval_flight.open_flight_stream",
        return_value=source(),
    ):
        batches = service.flight_batches(_retrieval_job())
        assert next(batches) == "batch"
        batches.close()

    assert closed == [True]
    service.get_job.assert_not_called()
