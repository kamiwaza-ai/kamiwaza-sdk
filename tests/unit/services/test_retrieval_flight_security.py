"""PyArrow-free Flight protocol, expiry, and endpoint security tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


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


def test_handshake_expiring_after_stream_creation_is_rejected_before_connect():
    from kamiwaza_sdk.exceptions import AuthenticationError
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    handshake = _flight_handshake()
    fake_flight = MagicMock()
    with patch(
        "kamiwaza_sdk.services.retrieval_flight._require_flight",
        return_value=fake_flight,
    ):
        stream = open_flight_stream(handshake, job_id="job")
    handshake.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(AuthenticationError, match="expired"):
        list(stream)

    fake_flight.connect.assert_not_called()


def test_endpoint_mutation_after_validation_cannot_downgrade_tls():
    from kamiwaza_sdk.services.retrieval_flight import open_flight_stream

    handshake = _flight_handshake()
    fake_flight = MagicMock()
    fake_client = MagicMock()
    fake_client.do_get.return_value = iter(())
    fake_flight.connect.return_value = fake_client

    with patch(
        "kamiwaza_sdk.services.retrieval_flight._require_flight",
        return_value=fake_flight,
    ):
        stream = open_flight_stream(handshake, job_id="job")
    handshake.endpoint = "grpc://plaintext-downgrade:6130"

    list(stream)

    fake_flight.connect.assert_called_once()
    assert fake_flight.connect.call_args.args == ("grpc+tls://host:6130",)


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
