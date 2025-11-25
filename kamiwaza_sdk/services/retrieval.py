"""Client helper for the retrieval service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional, Sequence

from ..exceptions import (
    APIError,
    AuthorizationError,
    DatasetNotFoundError,
    TransportNotSupportedError,
)
from .base_service import BaseService
from pydantic import SecretStr
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

    _BASE_PATH = "/retrieval"
    _KAFKA_PLATFORM_TAG = "urn:li:dataset:(urn:li:dataplatform:kafka"

    def create_job(self, request: RetrievalRequest) -> RetrievalJob:
        self._ensure_kafka_supported(request)
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

    def _ensure_kafka_supported(self, request: RetrievalRequest) -> None:
        urn = (request.dataset_urn or "").lower()
        if self._KAFKA_PLATFORM_TAG in urn:
            raise TransportNotSupportedError(
                "Kafka datasets cannot be materialized via the Kamiwaza SDK yet; "
                "use the Catalog service (e.g. client.get('/catalog/datasets/by-urn', params={'urn': <dataset_urn>})) "
                "to retrieve connection metadata and connect directly."
            )

    def slack_messages(
        self,
        dataset_urn: str,
        *,
        channels: Sequence[str] | None = None,
        include_replies: bool | None = None,
        max_messages: int | None = None,
        since_ts: str | datetime | None = None,
        until_ts: str | datetime | None = None,
        credential_override: str | SecretStr | None = None,
        transport: TransportType | str = TransportType.INLINE,
        format_hint: str = "json",
    ) -> list[dict]:
        options: dict[str, object] = {}
        if channels:
            options["channels"] = list(channels)
        if include_replies is not None:
            options["include_replies"] = bool(include_replies)
        if max_messages is not None:
            if max_messages <= 0:
                raise ValueError("max_messages must be positive")
            options["max_messages"] = max_messages
        if since_ts:
            options["since_ts"] = self._normalize_timestamp(since_ts)
        if until_ts:
            options["until_ts"] = self._normalize_timestamp(until_ts)

        cred: SecretStr | None = None
        if isinstance(credential_override, SecretStr):
            cred = credential_override
        elif isinstance(credential_override, str):
            cred = SecretStr(credential_override)

        transport_value = transport.value if isinstance(transport, TransportType) else str(transport)

        request = RetrievalRequest(
            dataset_urn=dataset_urn,
            transport=transport_value,
            format_hint=format_hint,
            credential_override=cred,
            options=options or None,
        )

        result = self.materialize(request)
        if not result.inline:
            raise TransportNotSupportedError("Slack retrieval requires inline transport")
        data = result.inline.data
        if isinstance(data, list):
            return data
        return [data]

    @staticmethod
    def _normalize_timestamp(value: str | datetime) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return value

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
