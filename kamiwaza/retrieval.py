"""Legacy ``kamiwaza.retrieval`` namespace — re-exports from canonical
``kamiwaza_sdk.services.retrieval_federation``. WS-M3.2 / T7.11 (ENG-5045)."""

from __future__ import annotations

from kamiwaza_sdk.services.retrieval_federation import RetrievalAPI

__all__ = ["RetrievalAPI"]
