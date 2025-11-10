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
    OperationStatus,
)


class IngestionService(BaseService):
    """High level helper for ingestion operations."""

    def run_active(self, source_type: str, **kwargs: Any) -> IngestResponse:
        payload = ActiveIngestRequest(source_type=source_type, kwargs=kwargs)
        response = self.client.post("/ingestion/ingest/run", json=payload.model_dump())
        return IngestResponse.model_validate(response)

    def emit_mcp(self, mcp: Dict[str, Any]) -> OperationStatus:
        payload = MCPEmitRequest(mcp=mcp)
        response = self.client.post("/ingestion/ingest/emit", json=payload.model_dump())
        return OperationStatus.model_validate(response)

    def schedule_job(self, job: IngestJobCreate) -> OperationStatus:
        response = self.client.post("/ingestion/ingest/jobs", json=job.model_dump())
        return OperationStatus.model_validate(response)

    def get_job_status(self, job_id: str) -> IngestJobStatus:
        response = self.client.get(f"/ingestion/ingest/status/{job_id}")
        return IngestJobStatus.model_validate(response)
