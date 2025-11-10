from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kamiwaza_sdk.exceptions import APIError, DatasetNotFoundError
from kamiwaza_sdk.schemas.retrieval import RetrievalRequest
from kamiwaza_sdk.services.retrieval import RetrievalResult, RetrievalService


class DummyResponse:
    def __init__(self, lines: list[str]):
        self._lines = lines
        self.raise_called = False
        self.closed = False

    def raise_for_status(self) -> None:
        self.raise_called = True

    def iter_lines(self) -> Iterator[bytes]:
        for line in self._lines:
            yield line.encode("utf-8")

    def close(self) -> None:
        self.closed = True


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


def test_stream_job_yields_events():
    resp = DummyResponse(
        [
            "event: chunk",
            "data: {\"sequence\": 1, \"data\": [1]}",
            "",
            "event: complete",
            "data: {\"event\": \"complete\"}",
            "",
        ]
    )
    responses = {("get", "/retrieval/retrieval/jobs/abc/stream"): resp}
    client = DummyClient(responses)
    service = RetrievalService(client)

    events = list(service.stream_job("abc"))

    assert resp.raise_called is True
    assert resp.closed is True
    assert events[0].event == "chunk"
    assert events[0].data["sequence"] == 1
    assert events[1].event == "complete"
    method, path, kwargs = client.calls[0]
    assert kwargs["stream"] is True


def test_materialize_inline_returns_inline_payload():
    job_payload = {
        "job_id": "job-1",
        "transport": "inline",
        "status": "complete",
        "dataset": {"urn": "urn", "platform": "s3", "path": None, "format": None},
        "inline": {"media_type": "application/json", "data": [{"a": 1}], "row_count": 1, "metadata": {}},
    }
    responses = {("post", "/retrieval/retrieval/jobs"): job_payload}
    service = RetrievalService(DummyClient(responses))

    result = service.materialize(RetrievalRequest(dataset_urn="urn"))

    assert isinstance(result, RetrievalResult)
    assert result.inline is not None
    assert result.inline.row_count == 1


def test_materialize_sse_returns_stream():
    job_payload = {
        "job_id": "job-2",
        "transport": "sse",
        "status": "queued",
        "dataset": {"urn": "urn", "platform": "s3", "path": None, "format": None},
    }
    stream_response = DummyResponse(
        [
            "event: chunk",
            "data: {\"sequence\": 1, \"data\": {\"value\": 1}}",
            "",
        ]
    )
    responses = {
        ("post", "/retrieval/retrieval/jobs"): job_payload,
        ("get", "/retrieval/retrieval/jobs/job-2/stream"): stream_response,
    }
    service = RetrievalService(DummyClient(responses))

    result = service.materialize(RetrievalRequest(dataset_urn="urn", transport="sse"))

    assert result.stream is not None
    events = list(result.stream)
    assert events[0].event == "chunk"


def test_create_job_translates_not_found():
    class FailingClient:
        def post(self, *_args, **_kwargs):
            raise APIError(
                "not found",
                status_code=404,
                response_data={"detail": "Dataset missing"},
            )

    service = RetrievalService(FailingClient())

    try:
        service.create_job(RetrievalRequest(dataset_urn="missing"))
    except Exception as exc:  # pylint: disable=broad-except
        assert isinstance(exc, DatasetNotFoundError)
        assert "Dataset missing" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected exception was not raised")
