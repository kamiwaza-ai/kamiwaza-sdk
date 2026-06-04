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
    try:
        return int(port_str.rsplit(":", 1)[-1])
    except (ValueError, TypeError):
        return None
