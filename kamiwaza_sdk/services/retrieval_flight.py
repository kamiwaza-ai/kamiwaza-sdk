"""Arrow Flight consumption for retrieval jobs (requires kamiwaza-sdk[flight])."""

from __future__ import annotations

import contextlib
import json
import math
import os
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Generator, Iterator
from urllib.parse import urlsplit

from ..exceptions import (
    AuthenticationError,
    AuthorizationError,
    FlightConfigurationError,
    FlightTimeoutError,
    FlightUnavailableError,
    InsecureFlightEndpointError,
    KamiwazaError,
    TransportNotSupportedError,
)
from ..schemas.retrieval import GrpcHandshake

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

# Public re-export for callers that import transport errors from this module.
__all__ = ["FlightUnavailableError", "open_flight_stream"]

_ENDPOINT_RETRY_ATTEMPTS = 3
_ENDPOINT_RETRY_BACKOFFS = (0.5, 1.0)
_DEFAULT_FLIGHT_TIMEOUT_SECONDS = 3600.0
_GRPC_KEEPALIVE_OPTIONS = (
    ("grpc.keepalive_time_ms", 30_000),
    ("grpc.keepalive_timeout_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
)
_INSECURE_SCHEMES = frozenset({"grpc", "grpc+tcp", "grpc+unix"})


@dataclass
class _StreamState:
    yielded: bool = False


@dataclass(frozen=True)
class _FlightRuntime:
    flight: Any
    ticket: Any
    connect_kwargs: dict[str, Any]
    timeout_seconds: float


@dataclass(frozen=True)
class _FlightSettings:
    tls_root_certs: bytes | None
    override_hostname: str | None
    timeout_seconds: float


def _require_flight() -> Any:
    try:
        import pyarrow.flight as flight  # type: ignore[import-untyped]

        return flight
    except ImportError as exc:
        raise KamiwazaError(
            "Flight transport requires pyarrow; install kamiwaza-sdk[flight]"
        ) from exc


def open_flight_stream(
    handshake: GrpcHandshake,
    job_id: str,
    *,
    ca_cert_path: str | os.PathLike[str] | None = None,
    tls_root_certs: bytes | None = None,
    override_hostname: str | None = None,
    timeout_seconds: float = _DEFAULT_FLIGHT_TIMEOUT_SECONDS,
    allow_insecure: bool = False,
) -> Iterator["pa.RecordBatch"]:
    """Yield Arrow record batches for a retrieval job handshake.

    The server's token is single-use and is consumed atomically only when a
    ``DoGet`` is claimed. There is no endpoint-level handshake refresh API.
    The SDK therefore retries or falls back only for a typed Flight
    unavailability before any batch is delivered. Timeouts, server errors,
    authentication failures, authorization failures, and arbitrary exceptions
    are never retried.

    TLS is required for every advertised endpoint unless ``allow_insecure`` is
    explicitly enabled. Plaintext remains incompatible with supplied CA
    material because PyArrow ignores TLS roots on plaintext transports.

    ``timeout_seconds`` is a finite deadline for the complete ``DoGet`` and
    stream read. Its generous one-hour default is paired with gRPC keepalives
    so idle-but-live transfers remain observable.
    """
    _validate_handshake(handshake)
    _validate_timeout(timeout_seconds)
    roots = _load_tls_root_certs(tls_root_certs, ca_cert_path)
    _validate_endpoint_security(handshake, allow_insecure, roots is not None)

    flight = _require_flight()
    settings = _FlightSettings(
        tls_root_certs=roots,
        override_hostname=override_hostname,
        timeout_seconds=timeout_seconds,
    )
    runtime = _make_runtime(flight, job_id, handshake.token, settings)
    return _iter_flight_stream(runtime, handshake)


def _validate_handshake(handshake: GrpcHandshake) -> None:
    if handshake.protocol != "arrow-flight":
        raise TransportNotSupportedError(
            f"Unsupported Flight protocol: {handshake.protocol!r}; "
            "the server must explicitly advertise 'arrow-flight'"
        )
    if not handshake.endpoints:
        raise FlightUnavailableError(
            "Handshake advertised no Flight endpoints (server-side configuration issue)"
        )
    expires_at = _as_utc(handshake.expires_at)
    if expires_at <= datetime.now(timezone.utc):
        raise AuthenticationError(
            f"Arrow Flight handshake expired at {expires_at.isoformat()}"
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_timeout(timeout_seconds: float) -> None:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a finite positive number")


def _load_tls_root_certs(
    tls_root_certs: bytes | None,
    ca_cert_path: str | os.PathLike[str] | None,
) -> bytes | None:
    if tls_root_certs is not None:
        if not tls_root_certs:
            raise FlightConfigurationError("Flight TLS root certificates are empty")
        return tls_root_certs
    if ca_cert_path is None:
        return None
    try:
        with open(ca_cert_path, "rb") as fh:
            roots = fh.read()
    except OSError as exc:
        raise FlightConfigurationError(
            f"Unable to read Flight CA bundle: {ca_cert_path}"
        ) from exc
    if not roots:
        raise FlightConfigurationError(f"Flight CA bundle is empty: {ca_cert_path}")
    return roots


def _validate_endpoint_security(
    handshake: GrpcHandshake,
    allow_insecure: bool,
    has_tls_roots: bool,
) -> None:
    for endpoint in handshake.endpoints:
        scheme = urlsplit(endpoint.location).scheme.lower()
        if scheme == "grpc+tls":
            continue
        if scheme not in _INSECURE_SCHEMES:
            raise FlightConfigurationError(
                f"Unsupported Flight endpoint URI: {endpoint.location!r}"
            )
        if has_tls_roots:
            raise FlightConfigurationError(
                "Flight CA material cannot be used with plaintext endpoint "
                f"{endpoint.location!r}"
            )
        if not allow_insecure:
            raise InsecureFlightEndpointError(
                f"Plaintext Flight endpoint {endpoint.location!r} is disabled; "
                "pass allow_insecure=True only for trusted local development"
            )
        warnings.warn(
            f"Using insecure plaintext Arrow Flight endpoint {endpoint.location!r}",
            UserWarning,
            stacklevel=3,
        )


def _make_runtime(
    flight: Any,
    job_id: str,
    token: str,
    settings: _FlightSettings,
) -> _FlightRuntime:
    ticket = flight.Ticket(json.dumps({"job_id": job_id, "token": token}).encode())
    connect_kwargs: dict[str, Any] = {"generic_options": list(_GRPC_KEEPALIVE_OPTIONS)}
    if settings.tls_root_certs is not None:
        connect_kwargs["tls_root_certs"] = settings.tls_root_certs
    if settings.override_hostname:
        connect_kwargs["override_hostname"] = settings.override_hostname
    return _FlightRuntime(
        flight,
        ticket,
        connect_kwargs,
        settings.timeout_seconds,
    )


def _iter_flight_stream(
    runtime: _FlightRuntime,
    handshake: GrpcHandshake,
) -> Iterator["pa.RecordBatch"]:
    errors: list[str] = []
    final_exc: Exception | None = None
    for endpoint in handshake.endpoints:
        last_exc = yield from _iter_endpoint(runtime, endpoint.location)
        if last_exc is None:
            return
        errors.append(f"{endpoint.location}: {last_exc}")
        final_exc = last_exc
    raise FlightUnavailableError(
        "No Flight endpoint reachable: " + "; ".join(errors)
    ) from final_exc


def _iter_endpoint(
    runtime: _FlightRuntime,
    location: str,
) -> Generator["pa.RecordBatch", None, Exception | None]:
    state = _StreamState()
    last_exc: Exception | None = None
    for attempt in range(_ENDPOINT_RETRY_ATTEMPTS):
        try:
            yield from _iter_flight_attempt(runtime, location, state)
            return None
        except runtime.flight.FlightUnauthenticatedError as exc:
            raise AuthenticationError(
                f"Arrow Flight authentication failed at {location}: {exc}"
            ) from exc
        except runtime.flight.FlightUnauthorizedError as exc:
            raise AuthorizationError(
                f"Arrow Flight authorization failed at {location}: {exc}"
            ) from exc
        except runtime.flight.FlightTimedOutError as exc:
            raise FlightTimeoutError(
                f"Arrow Flight transfer at {location} exceeded "
                f"{runtime.timeout_seconds:g} seconds",
                timeout_seconds=runtime.timeout_seconds,
            ) from exc
        except runtime.flight.FlightUnavailableError as exc:
            if state.yielded:
                raise
            last_exc = exc
            _sleep_before_retry(attempt)
    return last_exc


def _iter_flight_attempt(
    runtime: _FlightRuntime,
    location: str,
    state: _StreamState,
) -> Iterator["pa.RecordBatch"]:
    client = runtime.flight.connect(location, **runtime.connect_kwargs)
    try:
        options = runtime.flight.FlightCallOptions(timeout=runtime.timeout_seconds)
        reader = client.do_get(runtime.ticket, options=options)
        yield from _iter_record_batches(reader, state)
    finally:
        with contextlib.suppress(Exception):
            client.close()


def _iter_record_batches(
    reader: Any, state: _StreamState
) -> Iterator["pa.RecordBatch"]:
    for chunk in reader:
        if chunk.data is not None:
            state.yielded = True
            yield chunk.data


def _sleep_before_retry(attempt: int) -> None:
    if attempt >= _ENDPOINT_RETRY_ATTEMPTS - 1:
        return
    backoff_index = min(attempt, len(_ENDPOINT_RETRY_BACKOFFS) - 1)
    time.sleep(_ENDPOINT_RETRY_BACKOFFS[backoff_index])
