"""Legacy ``kamiwaza.cluster`` namespace — re-exports from canonical
``kamiwaza_sdk.services.cluster_federation``. WS-M3.2 / T7.7 (ENG-5041)."""

from __future__ import annotations

from kamiwaza_sdk.services.cluster_federation import ClusterAPI

__all__ = ["ClusterAPI"]
