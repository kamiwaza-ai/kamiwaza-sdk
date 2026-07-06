"""Arrow Flight consumption for retrieval jobs (requires kamiwaza-sdk[flight])."""
from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Iterator, Optional

from ..exceptions import FlightUnavailableError, KamiwazaError
from ..schemas.retrieval import GrpcHandshake

if TYPE_CHECKING:
    import pyarrow as pa

# Re-export so existing `from .retrieval_flight import FlightUnavailableError` keeps working.
__all__ = ["FlightUnavailableError", "open_flight_stream"]


def _require_flight():
    try:
        import pyarrow.flight as flight

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
    the next on any *pre-stream* connection error.  Raises
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
            reached *before* streaming began.
        Exception: The original exception when a failure occurs mid-stream
            (after at least one batch has been yielded).
        KamiwazaError: When pyarrow is not installed.
    """
    flight = _require_flight()

    if tls_root_certs is None and ca_cert_path:
        with open(ca_cert_path, "rb") as fh:
            tls_root_certs = fh.read()

    ticket = flight.Ticket(
        json.dumps({"job_id": job_id, "token": handshake.token}).encode()
    )
    errors: list[str] = []
    for endpoint in handshake.endpoints:
        client = None
        yielded = False
        try:
            connect_kwargs: dict = {}
            if tls_root_certs is not None:
                connect_kwargs["tls_root_certs"] = tls_root_certs
            if override_hostname:
                connect_kwargs["override_hostname"] = override_hostname
            client = flight.connect(endpoint.location, **connect_kwargs)
            reader = client.do_get(ticket)
            for chunk in reader:
                yielded = True
                yield chunk.data
            return
        except Exception as exc:  # noqa: BLE001
            if yielded:
                # Mid-stream failure: re-raise to avoid re-delivering data from
                # offset 0 on the next endpoint (silent duplication).
                raise
            errors.append(f"{endpoint.location}: {exc}")
        finally:
            if client is not None:
                with contextlib.suppress(Exception):
                    client.close()
    raise FlightUnavailableError(
        "No Flight endpoint reachable: " + "; ".join(errors)
    )
