"""Legacy ``kamiwaza.datasets`` namespace — re-exports from canonical
``kamiwaza_sdk.services.datasets``. WS-M3.2 / T7.9 (ENG-5043)."""

from __future__ import annotations

from kamiwaza_sdk.services.datasets import DatasetsAPI

__all__ = ["DatasetsAPI"]
