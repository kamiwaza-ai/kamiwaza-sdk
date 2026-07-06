"""Arrow Flight consumption for retrieval jobs (requires kamiwaza-sdk[flight])."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Iterator, Optional

from ..exceptions import KamiwazaError
from ..schemas.retrieval import GrpcHandshake

if TYPE_CHECKING:
    import pyarrow as pa


class FlightUnavailableError(KamiwazaError):
    """No advertised Flight endpoint could be reached."""


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
    the next on any connection error.  Raises ``FlightUnavailableError`` if
    all endpoints are exhausted.

    Args:
        handshake: gRPC handshake returned by the server for a grpc-transport job.
        job_id: ID of the retrieval job (encoded into the Flight ticket).
        ca_cert_path: Path to a PEM CA bundle for TLS verification.
        tls_root_certs: Raw PEM bytes for TLS; takes precedence over ca_cert_path.
        override_hostname: Override the TLS SNI hostname (useful for dev/testing).

    Yields:
        Arrow RecordBatch objects streamed from the Flight server.

    Raises:
        FlightUnavailableError: When no endpoint in the handshake could be reached.
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
        try:
            connect_kwargs: dict = {}
            if tls_root_certs is not None:
                connect_kwargs["tls_root_certs"] = tls_root_certs
            if override_hostname:
                connect_kwargs["override_hostname"] = override_hostname
            client = flight.connect(endpoint.location, **connect_kwargs)
            reader = client.do_get(ticket)
            for chunk in reader:
                yield chunk.data
            return
        except Exception as exc:  # noqa: BLE001 — endpoint fallback by design
            errors.append(f"{endpoint.location}: {exc}")
    raise FlightUnavailableError(
        "No Flight endpoint reachable: " + "; ".join(errors)
    )
