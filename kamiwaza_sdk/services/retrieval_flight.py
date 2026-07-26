"""Arrow Flight consumption for retrieval jobs (requires kamiwaza-sdk[flight])."""

from __future__ import annotations

import contextlib
import json
import math
import os
import time
from typing import TYPE_CHECKING, Any, Iterator

from ..exceptions import (
    FlightUnavailableError,
    KamiwazaError,
    TransportNotSupportedError,
)
from ..schemas.retrieval import GrpcHandshake

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

# Public re-export for callers that import transport errors from this module.
__all__ = ["FlightUnavailableError", "open_flight_stream"]

# Per-endpoint pre-stream retry settings.
_ENDPOINT_RETRY_ATTEMPTS = 3
_ENDPOINT_RETRY_BACKOFFS = (0.5, 1.0)
_DEFAULT_FLIGHT_TIMEOUT_SECONDS = 30.0


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
) -> Iterator["pa.RecordBatch"]:
    """Yield Arrow record batches for a retrieval job handshake.

    Tries each endpoint in ``handshake.endpoints`` in order, falling over to
    the next on any *pre-stream* connection error.  Each endpoint is attempted
    up to ``_ENDPOINT_RETRY_ATTEMPTS`` times with short sleeps between retries
    before the endpoint is marked failed and the next one is tried.  Raises
    ``FlightUnavailableError`` if all endpoints are exhausted before streaming
    begins.

    **Endpoint semantics**: endpoints are connection *alternatives*, not resume
    points.  Fallback to the next endpoint happens only for failures that occur
    before any batch has been yielded (connect / ``do_get`` / first-batch
    errors).  If a failure occurs after at least one batch has already been
    delivered to the caller, the error propagates immediately — attempting
    another endpoint from the beginning would silently re-deliver all data from
    offset 0, causing duplicate rows.

    Args:
        handshake: gRPC handshake returned by the server for a grpc-transport job.
        job_id: ID of the retrieval job (encoded into the Flight ticket).
        ca_cert_path: Path to a PEM CA bundle for TLS verification.
        tls_root_certs: Raw PEM bytes for TLS; takes precedence over ca_cert_path.
        override_hostname: Override the TLS SNI hostname (useful for dev/testing).
        timeout_seconds: Per-attempt deadline for the Arrow Flight ``do_get``
            call, including stream reads.

    Yields:
        Arrow RecordBatch objects streamed from the Flight server.

    Raises:
        FlightUnavailableError: When no endpoint in the handshake could be
            reached *before* streaming began, or when the handshake advertises
            no endpoints at all.
        TransportNotSupportedError: When the handshake protocol is not
            ``"arrow-flight"``.
        Exception: The original exception when a failure occurs mid-stream
            (after at least one batch has been yielded).
        KamiwazaError: When pyarrow is not installed.
        ValueError: When ``timeout_seconds`` is not finite and positive.
    """
    _validate_handshake(handshake)
    _validate_timeout(timeout_seconds)

    flight = _require_flight()
    tls_root_certs = _load_tls_root_certs(tls_root_certs, ca_cert_path)
    ticket = flight.Ticket(
        json.dumps({"job_id": job_id, "token": handshake.token}).encode()
    )
    connect_kwargs: dict[str, Any] = {}
    if tls_root_certs is not None:
        connect_kwargs["tls_root_certs"] = tls_root_certs
    if override_hostname:
        connect_kwargs["override_hostname"] = override_hostname

    return _iter_flight_stream(
        flight,
        handshake,
        ticket,
        connect_kwargs,
        timeout_seconds,
    )


def _validate_handshake(handshake: GrpcHandshake) -> None:
    if handshake.protocol and handshake.protocol != "arrow-flight":
        raise TransportNotSupportedError(
            f"Unsupported Flight protocol: {handshake.protocol!r}; "
            "this client only supports 'arrow-flight'"
        )
    if not handshake.endpoints:
        raise FlightUnavailableError(
            "Handshake advertised no Flight endpoints (server-side configuration issue)"
        )


def _validate_timeout(timeout_seconds: float) -> None:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a finite positive number")


def _load_tls_root_certs(
    tls_root_certs: bytes | None,
    ca_cert_path: str | os.PathLike[str] | None,
) -> bytes | None:
    if tls_root_certs is not None or ca_cert_path is None:
        return tls_root_certs
    try:
        with open(ca_cert_path, "rb") as fh:
            return fh.read()
    except OSError as exc:
        raise KamiwazaError(f"Unable to read Flight CA bundle: {ca_cert_path}") from exc


def _iter_flight_stream(
    flight: Any,
    handshake: GrpcHandshake,
    ticket: Any,
    connect_kwargs: dict[str, Any],
    timeout_seconds: float,
) -> Iterator["pa.RecordBatch"]:
    errors: list[str] = []
    final_exc: Exception | None = None
    for endpoint in handshake.endpoints:
        yielded = False
        last_exc: Exception | None = None
        for attempt in range(_ENDPOINT_RETRY_ATTEMPTS):
            client = None
            try:
                client = flight.connect(endpoint.location, **connect_kwargs)
                call_options = flight.FlightCallOptions(timeout=timeout_seconds)
                reader = client.do_get(ticket, options=call_options)
                for chunk in reader:
                    if chunk.data is None:
                        continue
                    yielded = True
                    yield chunk.data
                return
            except Exception as exc:  # noqa: BLE001
                if yielded:
                    # Mid-stream failure: re-raise to avoid re-delivering data
                    # from offset 0 on the next endpoint (silent duplication).
                    raise
                last_exc = exc
                if attempt < _ENDPOINT_RETRY_ATTEMPTS - 1:
                    time.sleep(
                        _ENDPOINT_RETRY_BACKOFFS[
                            min(attempt, len(_ENDPOINT_RETRY_BACKOFFS) - 1)
                        ]
                    )
            finally:
                if client is not None:
                    with contextlib.suppress(Exception):
                        client.close()
        errors.append(f"{endpoint.location}: {last_exc}")
        final_exc = last_exc
    raise FlightUnavailableError(
        "No Flight endpoint reachable: " + "; ".join(errors)
    ) from final_exc
