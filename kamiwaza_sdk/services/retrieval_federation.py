"""T7.11 / ENG-5045 — Federation-aware RetrievalAPI on the canonical surface.

WS-M3.2 service migration. Brings the M3-shaped retrieval list+cancel
surface from ``kamiwaza/retrieval.py`` (T5.36 / ENG-4713) into
``kamiwaza_sdk.services.retrieval_federation``.

Module name disambiguation per design §6.2 T7.11: the existing
``kamiwaza_sdk.services.retrieval`` covers the legacy ``RetrievalService``
streaming surface (transports, embeddings, etc.). This module ships the
M3-specific list+cancel slice setup.py + cmd_m3 smoke reach for. The
two surfaces don't overlap functionally — they coexist as separate
service classes.

Customer-facing API:

    kz.retrieval.list(...)         -> list[dict]
    kz.retrieval.cancel(query_id)  -> dict (updated status)

Server-side correlates:
- GET  /api/retrieval/jobs                  (ENG-4707 / T5.30 / FR-85)
- POST /api/retrieval/jobs/{id}/cancel      (ENG-4709 / T5.32 / FR-84)
"""

from __future__ import annotations

from typing import Any

from .retrieval import RetrievalService


class RetrievalAPI(RetrievalService):
    """Retrieval job management on the local cluster (M3 surface).

    Inherits from ``RetrievalService`` (the legacy streaming/embedding
    retrieval surface — ``create_job``, ``materialize``, ``stream_events``,
    etc.) so a single ``client.retrieval`` attribute exposes both
    surfaces — legacy streaming methods and the M3+ federation-aware
    ``list`` / ``cancel`` methods below. No method-name collisions.
    """

    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List retrieval jobs newest-first (T5.36).

        Native admin sees all; non-admin and mesh-origin callers are
        scoped server-side to their own requester URN.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        body = self.client._request("GET", "/api/retrieval/jobs", params=params)
        return list(body) if isinstance(body, list) else []

    def cancel(self, query_id: str) -> dict[str, Any]:
        """Cancel a retrieval job (T5.36).

        Returns the updated job-status dict (status will typically be
        ``"CANCELED"``). Server-side enforces ownership; the SDK surfaces
        ``KamiwazaError`` with the structured detail on 4xx.
        """
        response = self.client._request(
            "POST", f"/api/retrieval/jobs/{query_id}/cancel"
        )
        return dict(response) if isinstance(response, dict) else {}
