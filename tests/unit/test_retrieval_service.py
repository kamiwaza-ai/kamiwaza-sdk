from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kamiwaza_sdk.schemas.retrieval import RetrievalRequest
from kamiwaza_sdk.services.retrieval import RetrievalService


class DummyResponse:
    def __init__(self, lines: list[str]):
        self._lines = lines
        self.raise_called = False

    def raise_for_status(self) -> None:
        self.raise_called = True

    def iter_lines(self) -> Iterator[bytes]:
        for line in self._lines:
            yield line.encode("utf-8")


class DummyClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, path: str, **kwargs):
        self.calls.append(("post", path, kwargs))
        return self.responses[("post", path)]

    def get(self, path: str, **kwargs):
        self.calls.append(("get", path, kwargs))
        return self.responses[("get", path)]


def test_create_job_returns_model():
    job_payload = {
        "job_id": "123",
        "transport": "inline",
        "status": "completed",
        "dataset": {"urn": "urn", "platform": "s3", "path": None, "format": None},
        "inline": {"media_type": "application/json", "data": [{"a": 1}], "row_count": 1, "metadata": {}},
    }
    responses = {("post", "/retrieval/retrieval/jobs"): job_payload}
    service = RetrievalService(DummyClient(responses))

    job = service.create_job(RetrievalRequest(dataset_urn="urn"))

    assert job.job_id == "123"
    assert service.client.calls[0][1] == "/retrieval/retrieval/jobs"


def test_get_job_status_returns_model():
    status_payload = {
        "job_id": "123",
        "status": "running",
        "transport": "inline",
        "dataset": {"urn": "urn", "platform": "s3", "path": None, "format": None},
        "progress": {"bytes_processed": 10, "rows_processed": 2, "chunks_emitted": 1},
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:01Z",
    }
    responses = {("get", "/retrieval/retrieval/jobs/123"): status_payload}
    service = RetrievalService(DummyClient(responses))

    status = service.get_job("123")

    assert status.status == "running"


def test_stream_job_yields_lines():
    resp = DummyResponse(["data: chunk1", "data: chunk2"])
    responses = {("get", "/retrieval/retrieval/jobs/abc/stream"): resp}
    service = RetrievalService(DummyClient(responses))

    lines = list(service.stream_job("abc"))

    assert resp.raise_called is True
    assert lines == ["data: chunk1", "data: chunk2"]
