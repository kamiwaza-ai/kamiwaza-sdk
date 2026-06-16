"""Shared parsing for compose-spec port entries.

Compose accepts ports in three shapes:
- bare string ``"19530"`` (container port only)
- mapped string ``"8080:19530"`` (host:container, optionally ``/tcp``)
- long-form dict ``{"target": 19530, "name": "grpc", ...}``

Use ``extract_container_port()`` when a caller only needs the container
port number and wants to treat all three forms uniformly. Sites that
need to distinguish host-binding intent should keep their own handling
of ``published`` / ``host_ip`` (long-form) and the colon-prefix
(short-form) — this helper deliberately ignores those.
"""
from __future__ import annotations

from typing import Any, Optional

# Well-known port → Istio port-name prefix. Istio's port-name-prefix convention
# determines the L7 protocol applied by the sidecar (``http*`` → HTTP/1.1 codec,
# ``tcp*`` → raw TCP passthrough). Naming a non-HTTP backend port ``http`` makes
# the sidecar apply HTTP parsing to a binary wire protocol — e.g. it answers a
# postgres SSL handshake with an HTTP error, CrashLooping the client.
#
# This must stay in sync with the platform's compose→CR translation, which
# carries the same maps and heuristic (the App-Garden install path). Canonical
# source: ``kamiwaza/serving/garden/apps/k8s_adapter.py`` (``_HTTP_DEFAULT_PORTS``
# / ``_KNOWN_TCP_PORT_NAMES`` / ``_service_port_name``). Keep both in lockstep
# when adding a backend, or the two deploy paths will name the same port
# differently.
# 9200 is the Elasticsearch/OpenSearch HTTP REST port (the binary transport
# protocol is 9300), so it belongs here, not in the TCP map.
# TODO(ENG-7092): platform core's k8s_adapter still maps 9200 → tcp-elastic;
# realign it there so the two compose→CR paths stay in lockstep.
_HTTP_DEFAULT_PORTS = {80, 443, 3000, 5000, 8000, 8080, 8443, 9090, 9200}
_KNOWN_TCP_PORT_NAMES = {
    5432: "tcp-postgres",
    3306: "tcp-mysql",
    6379: "tcp-redis",
    27017: "tcp-mongo",
    5672: "tcp-amqp",
    9092: "tcp-kafka",
    2379: "tcp-etcd",
    2380: "tcp-etcd-peer",
    19530: "tcp-milvus",  # Milvus gRPC (HTTP/2); never the HTTP/1.1 codec
}


def default_service_port_name(port: int, is_primary: bool) -> str:
    """Fallback Istio port name for a port the compose author didn't name.

    Used only when a compose port is bare short-form (no ``name`` /
    ``app_protocol``) or a long-form entry without an explicit ``name``; a
    declared name always takes precedence. Returns ``"http"`` for known HTTP
    ports and for unknown *primary* ports (preserves the historical app
    frontend/backend behavior), ``"tcp-<service>"`` for known TCP backends like
    postgres/redis, and ``"tcp-port-<n>"`` for unknown non-primary ports.
    """
    if port in _KNOWN_TCP_PORT_NAMES:
        return _KNOWN_TCP_PORT_NAMES[port]
    if port in _HTTP_DEFAULT_PORTS:
        return "http"
    if is_primary:
        return "http"
    return f"tcp-port-{port}"


def extract_container_port(port_spec: Any) -> Optional[int]:
    """Return the container port from a compose port entry, or ``None``
    if the entry is malformed.

    Handles long-form dicts (``target`` key) and short-form strings
    (``"PORT"``, ``"HOST:CONTAINER"``, with optional ``"/tcp"`` suffix).
    """
    if isinstance(port_spec, dict):
        target = port_spec.get("target")
        if target is None:
            return None
        try:
            return int(target)
        except (ValueError, TypeError):
            return None

    port_str = str(port_spec).split("/", 1)[0]
    container_part = port_str.rsplit(":", 1)[-1]
    # Compose-spec allows ranges (``"3000-3005"`` bare or
    # ``"9090-9091:3000-3001"`` mapped). Use the lower bound as the
    # representative container port — sufficient for membership-style
    # callers (frontend detection, analysis port lists).
    if "-" in container_part:
        container_part = container_part.split("-", 1)[0]
    try:
        return int(container_part)
    except (ValueError, TypeError):
        return None
