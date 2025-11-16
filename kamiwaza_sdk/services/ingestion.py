"""Client wrapper for the ingestion service."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Sequence

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

    def health(self) -> Dict[str, Any]:
        """Return ingestion service health metadata."""

        return self.client.get("/ingestion/health")

    def run_active(self, source_type: str, **kwargs: Any) -> IngestResponse:
        payload = ActiveIngestRequest(source_type=source_type, kwargs=kwargs)
        response = self.client.post("/ingestion/ingest/run", json=payload.model_dump())
        return IngestResponse.model_validate(response)

    def run_slack_ingest(
        self,
        *,
        channels: Sequence[str] | None = None,
        channel_selector: str | None = None,
        include_dm: bool | None = None,
        token: str | None = None,
        token_secret_name: str | None = None,
        team_id: str | None = None,
        max_messages: int | None = None,
        since_ts: str | datetime | None = None,
        until_ts: str | datetime | None = None,
        extra_kwargs: Mapping[str, Any] | None = None,
    ) -> IngestResponse:
        """Ingest Slack channels using the backend's multi-channel support.

        Args:
            channels: Explicit list of channel IDs/names to ingest. Optional when using
                ``channel_selector``.
            channel_selector: Backend selector (``all``, ``public``, ``private``, ``dm``, ``list``)
                controlling which channels are enumerated when ``channels`` is not provided.
            include_dm: Whether to allow bot-accessible DM conversations when using selectors.
            token: Slack token override for this ingest run.
            token_secret_name: Secret name to resolve the Slack token.
            team_id: Workspace/team identifier (auto-detected when omitted).
            max_messages: Limit of messages to capture for documentation.
            since_ts: Lower timestamp bound (``datetime`` or ISO string).
            until_ts: Upper timestamp bound (``datetime`` or ISO string).
            extra_kwargs: Additional backend-specific parameters merged into the payload.
        """

        payload: MutableMapping[str, Any] = {}
        if channels:
            payload["channels"] = list(channels)
        if channel_selector:
            selector = channel_selector.lower()
            payload["channel_selector"] = selector
        if include_dm is not None:
            payload["include_dm"] = bool(include_dm)
        if token:
            payload["token"] = token
        if token_secret_name:
            payload["token_secret_name"] = token_secret_name
        if team_id:
            payload["team_id"] = team_id
        if max_messages is not None:
            if max_messages <= 0:
                raise ValueError("max_messages must be positive")
            payload["max_messages"] = max_messages
        if since_ts:
            payload["since_ts"] = self._normalize_timestamp(since_ts)
        if until_ts:
            payload["until_ts"] = self._normalize_timestamp(until_ts)
        if extra_kwargs:
            payload.update(dict(extra_kwargs))

        return self.run_active("slack", **payload)

    @staticmethod
    def _normalize_timestamp(value: str | datetime) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return value

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
