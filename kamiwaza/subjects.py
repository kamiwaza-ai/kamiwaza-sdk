"""Legacy ``kamiwaza.subjects`` namespace — re-exports from canonical
``kamiwaza_sdk.services.subjects``. WS-M3.2 / T7.8 (ENG-5042)."""

from __future__ import annotations

from kamiwaza_sdk.services.subjects import SubjectGrantsAPI, SubjectsAPI

__all__ = ["SubjectGrantsAPI", "SubjectsAPI"]
