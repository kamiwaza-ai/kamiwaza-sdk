"""Arrow Flight consumption for retrieval jobs (requires kamiwaza-sdk[flight])."""
from __future__ import annotations

import contextlib
import json
import time
from typing import TYPE_CHECKING, Iterator, Optional

from ..exceptions import FlightUnavailableError, KamiwazaError, TransportNotSupportedError
from ..schemas.retrieval import GrpcHandshake

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

# Re-export so existing `from .retrieval_flight import FlightUnavailableError` keeps working.
__all__ = ["FlightUnavailableError", "open_flight_stream"]

# Per-endpoint pre-stream retry settings.
_ENDPOINT_RETRY_ATTEMPTS = 3
_ENDPOINT_RETRY_BACKOFFS = (0.5, 1.0)


def _require_flight():
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
    ca_cert_path: Optional[str] = None,
    tls_root_certs: Optional[bytes] = None,
    override_hostname: Optional[str] = None,
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
    """
    # Early validation — performed before pyarrow import so callers without
    # pyarrow still get clean errors for obviously-wrong handshakes.
    if handshake.protocol and handshake.protocol != "arrow-flight":
        raise TransportNotSupportedError(
            f"Unsupported Flight protocol: {handshake.protocol!r}; "
            "this client only supports 'arrow-flight'"
        )
    if not handshake.endpoints:
        raise FlightUnavailableError(
            "Handshake advertised no Flight endpoints (server-side configuration issue)"
        )

    flight = _require_flight()

    if tls_root_certs is None and ca_cert_path:
        with open(ca_cert_path, "rb") as fh:
            tls_root_certs = fh.read()

    ticket = flight.Ticket(
        json.dumps({"job_id": job_id, "token": handshake.token}).encode()
    )

    connect_kwargs: dict = {}
    if tls_root_certs is not None:
        connect_kwargs["tls_root_certs"] = tls_root_certs
    if override_hostname:
        connect_kwargs["override_hostname"] = override_hostname

    errors: list[str] = []
    for endpoint in handshake.endpoints:
        yielded = False
        last_exc: Optional[Exception] = None
        for attempt in range(_ENDPOINT_RETRY_ATTEMPTS):
            client = None
            try:
                client = flight.connect(endpoint.location, **connect_kwargs)
                reader = client.do_get(ticket)
                for chunk in reader:
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
    raise FlightUnavailableError(
        "No Flight endpoint reachable: " + "; ".join(errors)
    )
