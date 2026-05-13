"""Legacy ``kamiwaza.gates`` namespace — re-exports from canonical
``kamiwaza_sdk.services.gates``. WS-M3.2 / T7.10 (ENG-5044)."""

from __future__ import annotations

from kamiwaza_sdk.services.gates import GatesAPI

__all__ = ["GatesAPI"]
