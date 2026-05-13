"""Legacy ``kamiwaza.jobs`` namespace — re-exports from canonical
``kamiwaza_sdk.services.jobs_federation``. WS-M3.2 / T7.6 (ENG-5040)."""

from __future__ import annotations

from kamiwaza_sdk.services.jobs_federation import JobsAPI

__all__ = ["JobsAPI"]
