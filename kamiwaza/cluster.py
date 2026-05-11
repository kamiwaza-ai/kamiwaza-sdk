"""T5.21 / ENG-4698 — kamiwaza.cluster module.

Customer-facing cluster surface per design §4.2.11 / §4.4.3:

    kz.cluster.capabilities()    -> ClusterCapabilities

The capabilities endpoint is mesh-routable since ENG-4697 (auth widened
to viewer); a probing peer reaches the same surface through
``kz.federations[name].probe()`` (see kamiwaza.federations.FederationProxy).

Subsequent WS-M2 cycles layer ``diagnose()``, ``operations()``, and
``fix()`` onto this module (T5.7 / T5.8 / T5.13 / T5.14).

Server-side correlate: ``GET /api/cluster/cluster_capabilities`` in
``kamiwaza.cluster.api`` (extended by ENG-4696).
"""

from __future__ import annotations

from typing import Any

from kamiwaza.models import ClusterCapabilities


class ClusterAPI:
    """Top-level cluster operations on the local cluster."""

    def __init__(self, client: Any) -> None:
        # client is a kamiwaza.client.Kamiwaza instance — Any avoids a
        # runtime cycle since the client lazy-imports this module.
        self._client = client

    def capabilities(self) -> ClusterCapabilities:
        """Return the local cluster's capabilities (T5.19 + T5.21).

        Hits ``GET /api/cluster/cluster_capabilities`` on the local cluster.
        Auth: any authenticated user with viewer or owner on
        ``cluster:<local_uuid>`` (admin's install-seeded owner passes).

        Returns:
            ClusterCapabilities — hardware, available platforms, GPU count,
            federation_count, active_deployments, ray_ready, etc.
        """
        body = self._client._request("GET", "/api/cluster/cluster_capabilities")
        return ClusterCapabilities.model_validate(body)
