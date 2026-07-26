"""Unit tests for the Arrow Flight streaming client (pyarrow required)."""

from __future__ import annotations

import pytest

flight = pytest.importorskip("pyarrow.flight")

from datetime import datetime, timezone  # noqa: E402

from kamiwaza_sdk.exceptions import (  # noqa: E402
    AuthenticationError,
    AuthorizationError,
    FlightTimeoutError,
)
from kamiwaza_sdk.schemas.retrieval import FlightEndpoint, GrpcHandshake  # noqa: E402
from kamiwaza_sdk.services.retrieval_flight import (  # noqa: E402
    FlightUnavailableError,
    open_flight_stream,
)

pytestmark = pytest.mark.unit

_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _handshake(*locations: str) -> GrpcHandshake:
    return GrpcHandshake(
        endpoints=[FlightEndpoint(location=value) for value in locations],
        token="single-use-token",
        expires_at=_FUTURE,
        protocol="arrow-flight",
    )


class _EmptyReader:
    def __iter__(self):
        return iter(())


class _EmptyClient:
    def do_get(self, ticket, options=None):
        return _EmptyReader()

    def close(self):
        pass


def test_handshake_schema_parses_explicit_flight_protocol():
    handshake = _handshake("grpc+tls://host:6130")

    assert handshake.endpoints[0].location == "grpc+tls://host:6130"
    assert handshake.protocol == "arrow-flight"


def test_open_flight_stream_retries_unavailable_then_falls_back(monkeypatch):
    calls: list[str] = []

    def fake_connect(location, **kwargs):
        calls.append(location)
        if location == "grpc+tls://bad:6130":
            raise flight.FlightUnavailableError("unreachable")
        return _EmptyClient()

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)
    monkeypatch.setattr(
        "kamiwaza_sdk.services.retrieval_flight.time.sleep", lambda _: None
    )

    list(
        open_flight_stream(
            _handshake(
                "grpc+tls://bad:6130",
                "grpc+tls://good:6130",
            ),
            job_id="job-order",
        )
    )

    from kamiwaza_sdk.services.retrieval_flight import _ENDPOINT_RETRY_ATTEMPTS

    assert calls.count("grpc+tls://bad:6130") == _ENDPOINT_RETRY_ATTEMPTS
    assert calls.count("grpc+tls://good:6130") == 1


def test_all_unavailable_endpoints_raise_typed_error(monkeypatch):
    original_error = flight.FlightUnavailableError("connection refused")

    def fake_connect(location, **kwargs):
        raise original_error

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)
    monkeypatch.setattr(
        "kamiwaza_sdk.services.retrieval_flight.time.sleep", lambda _: None
    )

    with pytest.raises(FlightUnavailableError) as exc_info:
        list(open_flight_stream(_handshake("grpc+tls://dead:6130"), job_id="job"))

    assert exc_info.value.__cause__ is original_error
    assert "grpc+tls://dead:6130" in str(exc_info.value)


def test_midstream_failure_propagates_without_fallback(monkeypatch):
    calls: list[str] = []

    class Chunk:
        data = "batch-0"

    class FailingReader:
        def __iter__(self):
            yield Chunk()
            raise RuntimeError("mid-stream boom")

    class Client:
        def do_get(self, ticket, options=None):
            return FailingReader()

        def close(self):
            pass

    def fake_connect(location, **kwargs):
        calls.append(location)
        return Client()

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)

    with pytest.raises(RuntimeError, match="mid-stream boom"):
        list(
            open_flight_stream(
                _handshake(
                    "grpc+tls://first:6130",
                    "grpc+tls://second:6130",
                ),
                job_id="job",
            )
        )

    assert calls == ["grpc+tls://first:6130"]


@pytest.mark.parametrize(
    ("flight_error", "sdk_error"),
    [
        (flight.FlightUnauthenticatedError, AuthenticationError),
        (flight.FlightUnauthorizedError, AuthorizationError),
    ],
)
def test_permanent_auth_errors_fail_fast(
    monkeypatch,
    flight_error,
    sdk_error,
):
    calls: list[str] = []
    original_error = flight_error("invalid Flight credentials")

    class Client:
        def do_get(self, ticket, options=None):
            raise original_error

        def close(self):
            pass

    def fake_connect(location, **kwargs):
        calls.append(location)
        return Client()

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)

    with pytest.raises(sdk_error, match="invalid Flight credentials") as exc_info:
        list(
            open_flight_stream(
                _handshake(
                    "grpc+tls://first:6130",
                    "grpc+tls://second:6130",
                ),
                job_id="job",
            )
        )

    assert exc_info.value.__cause__ is original_error
    assert calls == ["grpc+tls://first:6130"]


def test_timeout_maps_to_typed_error_without_retry(monkeypatch):
    calls: list[str] = []
    original_error = flight.FlightTimedOutError("deadline exceeded")

    def fake_connect(location, **kwargs):
        calls.append(location)
        raise original_error

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)

    with pytest.raises(FlightTimeoutError) as exc_info:
        list(
            open_flight_stream(
                _handshake(
                    "grpc+tls://first:6130",
                    "grpc+tls://second:6130",
                ),
                job_id="job",
                timeout_seconds=45,
            )
        )

    assert exc_info.value.timeout_seconds == 45
    assert exc_info.value.__cause__ is original_error
    assert calls == ["grpc+tls://first:6130"]


def test_server_error_propagates_without_retry(monkeypatch):
    calls: list[str] = []
    original_error = flight.FlightServerError("application failure")

    def fake_connect(location, **kwargs):
        calls.append(location)
        raise original_error

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)

    with pytest.raises(flight.FlightServerError) as exc_info:
        list(
            open_flight_stream(
                _handshake(
                    "grpc+tls://first:6130",
                    "grpc+tls://second:6130",
                ),
                job_id="job",
            )
        )

    assert exc_info.value is original_error
    assert calls == ["grpc+tls://first:6130"]


def test_arbitrary_prestream_error_propagates_without_retry(monkeypatch):
    calls: list[str] = []
    original_error = RuntimeError("unexpected client failure")

    def fake_connect(location, **kwargs):
        calls.append(location)
        raise original_error

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)

    with pytest.raises(RuntimeError) as exc_info:
        list(
            open_flight_stream(
                _handshake(
                    "grpc+tls://first:6130",
                    "grpc+tls://second:6130",
                ),
                job_id="job",
            )
        )

    assert exc_info.value is original_error
    assert calls == ["grpc+tls://first:6130"]


def test_unavailable_after_first_batch_does_not_retry(monkeypatch):
    calls: list[str] = []
    original_error = flight.FlightUnavailableError("stream interrupted")

    class Chunk:
        data = "batch-0"

    class Reader:
        def __iter__(self):
            yield Chunk()
            raise original_error

    class Client:
        def do_get(self, ticket, options=None):
            return Reader()

        def close(self):
            pass

    def fake_connect(location, **kwargs):
        calls.append(location)
        return Client()

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)

    with pytest.raises(flight.FlightUnavailableError) as exc_info:
        list(
            open_flight_stream(
                _handshake(
                    "grpc+tls://first:6130",
                    "grpc+tls://second:6130",
                ),
                job_id="job",
            )
        )

    assert exc_info.value is original_error
    assert calls == ["grpc+tls://first:6130"]


def test_do_get_receives_one_hour_default_and_connect_keepalives(monkeypatch):
    observed_timeouts: list[float] = []
    observed_options: list[list[tuple[str, int]]] = []

    class Client:
        def do_get(self, ticket, options=None):
            observed_timeouts.append(options.timeout)
            return _EmptyReader()

        def close(self):
            pass

    def fake_connect(location, **kwargs):
        observed_options.append(kwargs["generic_options"])
        return Client()

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)

    list(open_flight_stream(_handshake("grpc+tls://host:6130"), job_id="job"))

    assert observed_timeouts == [3600.0]
    assert observed_options == [
        [
            ("grpc.keepalive_time_ms", 30_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.keepalive_permit_without_calls", 1),
        ]
    ]


def test_tls_settings_reach_connect(monkeypatch):
    observed_kwargs: list[dict] = []

    def fake_connect(location, **kwargs):
        observed_kwargs.append(kwargs)
        return _EmptyClient()

    monkeypatch.setattr("pyarrow.flight.connect", fake_connect)

    list(
        open_flight_stream(
            _handshake("grpc+tls://host:6130"),
            job_id="job",
            tls_root_certs=b"PRIVATE CA",
            override_hostname="flight.internal",
        )
    )

    assert observed_kwargs[0]["tls_root_certs"] == b"PRIVATE CA"
    assert observed_kwargs[0]["override_hostname"] == "flight.internal"


def test_custom_timeout_reaches_do_get(monkeypatch):
    observed_timeouts: list[float] = []

    class Client:
        def do_get(self, ticket, options=None):
            observed_timeouts.append(options.timeout)
            return _EmptyReader()

        def close(self):
            pass

    monkeypatch.setattr(
        "pyarrow.flight.connect",
        lambda *_args, **_kwargs: Client(),
    )

    list(
        open_flight_stream(
            _handshake("grpc+tls://host:6130"),
            job_id="job",
            timeout_seconds=12.5,
        )
    )

    assert observed_timeouts == [12.5]


def test_metadata_only_chunks_are_not_yielded(monkeypatch):
    class Chunk:
        def __init__(self, data):
            self.data = data

    class Reader:
        def __iter__(self):
            return iter([Chunk(None), Chunk("batch-0")])

    class Client:
        def do_get(self, ticket, options=None):
            return Reader()

        def close(self):
            pass

    monkeypatch.setattr(
        "pyarrow.flight.connect",
        lambda *_args, **_kwargs: Client(),
    )

    batches = list(
        open_flight_stream(
            _handshake("grpc+tls://host:6130"),
            job_id="job",
        )
    )

    assert batches == ["batch-0"]
