"""Client helper for the retrieval service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterator, Optional

from ..exceptions import (
    APIError,
    AuthorizationError,
    DatasetNotFoundError,
    TransportNotSupportedError,
)
from .base_service import BaseService
from ..schemas.retrieval import (
    GrpcHandshake,
    InlineData,
    RetrievalJob,
    RetrievalJobStatus,
    RetrievalRequest,
    RetrievalStreamEvent,
    TransportType,
)
from ..utils import reveal_secrets


@dataclass
class RetrievalResult:
    """Structured return for automatic transport selection."""

    job: RetrievalJob
    inline: Optional[InlineData] = None
    stream: Optional[Iterator[RetrievalStreamEvent]] = None
    grpc: Optional[GrpcHandshake] = None


class RetrievalService(BaseService):
    """High-level wrapper for dataset materialisation jobs."""

    _BASE_PATH = "/retrieval/retrieval"

    def create_job(self, request: RetrievalRequest) -> RetrievalJob:
        try:
            payload = reveal_secrets(request.model_dump(exclude_none=True))
            response = self.client.post(
                f"{self._BASE_PATH}/jobs",
                json=payload,
            )
        except APIError as exc:
            raise self._translate_error(exc) from exc
        return RetrievalJob.model_validate(response)

    def materialize(self, request: RetrievalRequest) -> RetrievalResult:
        """Create a retrieval job and normalise the transport handling."""
        job = self.create_job(request)
        if job.transport == TransportType.INLINE:
            if not job.inline:
                raise TransportNotSupportedError("Inline transport returned no payload")
            return RetrievalResult(job=job, inline=job.inline)
        if job.transport == TransportType.SSE:
            return RetrievalResult(job=job, stream=self.stream_events(job.job_id))
        if job.transport == TransportType.GRPC:
            return RetrievalResult(job=job, grpc=job.grpc)
        raise TransportNotSupportedError(f"Unsupported transport {job.transport}")

    def get_job(self, job_id: str) -> RetrievalJobStatus:
        try:
            response = self.client.get(f"{self._BASE_PATH}/jobs/{job_id}")
        except APIError as exc:
            raise self._translate_error(exc) from exc
        return RetrievalJobStatus.model_validate(response)

    def stream_events(self, job_id: str) -> Iterator[RetrievalStreamEvent]:
        """Stream SSE events for a retrieval job."""
        response = self.client.get(
            f"{self._BASE_PATH}/jobs/{job_id}/stream",
            expect_json=False,
            stream=True,
        )
        response.raise_for_status()
        return self._iter_sse(response)

    def stream_job(self, job_id: str) -> Iterator[RetrievalStreamEvent]:
        """Backward-compatible alias for :meth:`stream_events`."""
        return self.stream_events(job_id)

    def create_inline_job(
        self,
        dataset_urn: str,
        *,
        format_hint: Optional[str] = None,
        credential_override: Optional[str] = None,
        **options,
    ) -> RetrievalJob:
        request = RetrievalRequest(
            dataset_urn=dataset_urn,
            transport="inline",
            format_hint=format_hint,
            credential_override=credential_override,
            options=options or None,
        )
        return self.create_job(request)

    def _iter_sse(self, response) -> Iterator[RetrievalStreamEvent]:
        buffer: list[str] = []
        event_type = "message"
        try:
            for raw in response.iter_lines():
                if raw is None:
                    continue
                line = raw.decode("utf-8")
                if not line:
                    if buffer:
                        data_str = "\n".join(buffer)
                        buffer = []
                        yield self._build_event(event_type, data_str)
                        event_type = "message"
                    continue
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip() or "message"
                elif line.startswith("data:"):
                    buffer.append(line.split(":", 1)[1].lstrip())
            if buffer:
                data_str = "\n".join(buffer)
                yield self._build_event(event_type, data_str)
        finally:
            response.close()

    @staticmethod
    def _build_event(event_type: str, payload: str) -> RetrievalStreamEvent:
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                data = {"value": data}
        except json.JSONDecodeError:
            data = {"value": payload}
        return RetrievalStreamEvent(event=event_type or "message", data=data)

    @staticmethod
    def _translate_error(exc: APIError) -> Exception:
        detail = RetrievalService._error_detail(exc)
        if exc.status_code == 404:
            return DatasetNotFoundError(detail or "Dataset not found")
        if exc.status_code == 403:
            return AuthorizationError(detail or "Not authorised to access dataset")
        if exc.status_code == 422:
            return TransportNotSupportedError(detail or "Requested transport is not supported")
        return APIError(
            exc.message,
            status_code=exc.status_code,
            response_text=exc.response_text,
            response_data=exc.response_data,
        )

    @staticmethod
    def _error_detail(exc: APIError) -> Optional[str]:
        if isinstance(exc.response_data, dict):
            detail = exc.response_data.get("detail")
            if isinstance(detail, str):
                return detail
        if exc.response_text:
            return exc.response_text
        return None
