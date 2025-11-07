"""Client helper for the retrieval service."""

from __future__ import annotations

from typing import Iterator, Optional

from .base_service import BaseService
from ..schemas.retrieval import (
    RetrievalRequest,
    RetrievalJob,
    RetrievalJobStatus,
)


class RetrievalService(BaseService):
    """High-level wrapper for dataset materialisation jobs."""

    _BASE_PATH = "/retrieval/retrieval"

    def create_job(self, request: RetrievalRequest) -> RetrievalJob:
        response = self.client.post(
            f"{self._BASE_PATH}/jobs",
            json=request.model_dump(exclude_none=True),
        )
        return RetrievalJob.model_validate(response)

    def get_job(self, job_id: str) -> RetrievalJobStatus:
        response = self.client.get(f"{self._BASE_PATH}/jobs/{job_id}")
        return RetrievalJobStatus.model_validate(response)

    def stream_job(self, job_id: str) -> Iterator[str]:
        resp = self.client.get(
            f"{self._BASE_PATH}/jobs/{job_id}/stream",
            expect_json=False,
        )
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line:
                yield line.decode("utf-8")

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
