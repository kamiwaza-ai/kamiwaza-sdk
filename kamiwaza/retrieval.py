"""T5.36 / ENG-4713 — kamiwaza.retrieval module.

Customer-facing surface per design §4.2.11:

    kz.retrieval.list(...)         -> list[dict]
    kz.retrieval.cancel(query_id)  -> dict (updated status)

Server-side correlates:
- GET  /api/retrieval/jobs                  (ENG-4707 / T5.30 / FR-85)
- POST /api/retrieval/jobs/{id}/cancel      (ENG-4709 / T5.32 / FR-84)

The list / cancel return values are surfaced as raw dicts (rather than
typed models) — the retrieval response shape is richer than the WS-M2
demo gate needs, and customers can model-validate locally if they want.
"""

from __future__ import annotations

from typing import Any, Optional


class RetrievalAPI:
    """Retrieval job management on the local cluster."""

    def __init__(self, client: Any) -> None:
        self._client = client

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
        body = self._client._request("GET", "/api/retrieval/jobs", params=params)
        return list(body) if isinstance(body, list) else []

    def cancel(self, query_id: str) -> dict[str, Any]:
        """Cancel a retrieval job (T5.36).

        Returns the updated job-status dict (status will typically be
        ``"CANCELED"``). Server-side enforces ownership; the SDK surfaces
        ``KamiwazaError`` with the structured detail on 4xx.
        """
        response = self._client._request(
            "POST", f"/api/retrieval/jobs/{query_id}/cancel"
        )
        return dict(response) if isinstance(response, dict) else {}

    _unused: Optional[Any] = None  # vulture-noop placeholder
