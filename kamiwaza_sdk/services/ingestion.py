"""Client wrapper for the ingestion service."""

from __future__ import annotations

from typing import Any, Dict

from .base_service import BaseService
from ..schemas.ingestion import (
    ActiveIngestRequest,
    IngestJobCreate,
    IngestJobStatus,
    IngestResponse,
    MCPEmitRequest,
)


class IngestionService(BaseService):
    """High level helper for ingestion operations."""

    def run_active(self, source_type: str, **kwargs: Any) -> IngestResponse:
        payload = ActiveIngestRequest(source_type=source_type, kwargs=kwargs)
        response = self.client.post("/ingestion/ingest/run", json=payload.model_dump())
        return IngestResponse.model_validate(response)

    def emit_mcp(self, mcp: Dict[str, Any]) -> None:
        payload = MCPEmitRequest(mcp=mcp)
        self.client.post("/ingestion/ingest/emit", json=payload.model_dump())

    def schedule_job(self, job: IngestJobCreate) -> None:
        self.client.post("/ingestion/ingest/jobs", json=job.model_dump())

    def get_job_status(self, job_id: str) -> IngestJobStatus:
        response = self.client.get(f"/ingestion/ingest/status/{job_id}")
        return IngestJobStatus.model_validate(response)
