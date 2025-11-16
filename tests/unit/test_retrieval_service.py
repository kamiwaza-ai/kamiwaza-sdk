from __future__ import annotations

from typing import Iterator

import pytest

from datetime import datetime

from kamiwaza_sdk.exceptions import APIError, DatasetNotFoundError, TransportNotSupportedError
from kamiwaza_sdk.schemas.retrieval import RetrievalRequest
from kamiwaza_sdk.services.retrieval import RetrievalResult, RetrievalService

pytestmark = pytest.mark.unit


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


def test_create_job_returns_model(dummy_client):
    job_payload = {
        "job_id": "123",
        "transport": "inline",
        "status": "completed",
        "dataset": {"urn": "urn", "platform": "s3", "path": None, "format": None},
        "inline": {"media_type": "application/json", "data": [{"a": 1}], "row_count": 1, "metadata": {}},
    }
    responses = {("post", "/retrieval/retrieval/jobs"): job_payload}
    service = RetrievalService(dummy_client(responses))

    job = service.create_job(RetrievalRequest(dataset_urn="urn"))

    assert job.job_id == "123"
    assert service.client.calls[0][1] == "/retrieval/retrieval/jobs"


def test_get_job_status_returns_model(dummy_client):
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
    service = RetrievalService(dummy_client(responses))

    status = service.get_job("123")

    assert status.status == "running"


def test_stream_job_yields_events(dummy_client):
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
    client = dummy_client(responses)
    service = RetrievalService(client)

    events = list(service.stream_job("abc"))

    assert resp.raise_called is True
    assert resp.closed is True
    assert events[0].event == "chunk"
    assert events[0].data["sequence"] == 1
    assert events[1].event == "complete"
    method, path, kwargs = client.calls[0]
    assert kwargs["stream"] is True


def test_materialize_inline_returns_inline_payload(dummy_client):
    job_payload = {
        "job_id": "job-1",
        "transport": "inline",
        "status": "complete",
        "dataset": {"urn": "urn", "platform": "s3", "path": None, "format": None},
        "inline": {"media_type": "application/json", "data": [{"a": 1}], "row_count": 1, "metadata": {}},
    }
    responses = {("post", "/retrieval/retrieval/jobs"): job_payload}
    service = RetrievalService(dummy_client(responses))

    result = service.materialize(RetrievalRequest(dataset_urn="urn"))

    assert isinstance(result, RetrievalResult)
    assert result.inline is not None
    assert result.inline.row_count == 1


def test_materialize_sse_returns_stream(dummy_client):
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
    service = RetrievalService(dummy_client(responses))

    result = service.materialize(RetrievalRequest(dataset_urn="urn", transport="sse"))

    assert result.stream is not None
    events = list(result.stream)
    assert events[0].event == "chunk"


def test_materialize_grpc_returns_handshake(dummy_client):
    job_payload = {
        "job_id": "job-3",
        "transport": "grpc",
        "status": "queued",
        "dataset": {"urn": "urn", "platform": "s3", "path": None, "format": None},
        "grpc": {
            "endpoint": "grpc://localhost:50051",
            "token": "secret",
            "expires_at": "2025-01-01T00:00:00Z",
            "protocol": "kamiwaza.retrieval.v1",
        },
    }
    responses = {("post", "/retrieval/retrieval/jobs"): job_payload}
    service = RetrievalService(dummy_client(responses))

    result = service.materialize(RetrievalRequest(dataset_urn="urn", transport="grpc"))

    assert result.grpc is not None
    assert result.grpc.endpoint == "grpc://localhost:50051"


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


def test_create_job_unwraps_secret_fields(dummy_client):
    job_payload = {
        "job_id": "job-secret",
        "transport": "inline",
        "status": "queued",
        "dataset": {"urn": "urn", "platform": "s3", "path": None, "format": None},
    }
    client = dummy_client({("post", "/retrieval/retrieval/jobs"): job_payload})
    service = RetrievalService(client)

    service.create_job(
        RetrievalRequest(dataset_urn="urn", credential_override="{\"token\":\"secret\"}"),
    )

    method, path, kwargs = client.calls[0]
    assert method == "post"
    assert path == "/retrieval/retrieval/jobs"
    assert kwargs["json"]["credential_override"] == '{"token":"secret"}'


def test_create_job_rejects_kafka_datasets(dummy_client):
    service = RetrievalService(dummy_client({}))
    request = RetrievalRequest(dataset_urn="urn:li:dataset:(urn:li:dataPlatform:kafka,topic,PROD)")

    with pytest.raises(TransportNotSupportedError) as excinfo:
        service.create_job(request)

    assert "Kafka datasets" in str(excinfo.value)


def test_slack_messages_builds_request(dummy_client):
    job_payload = {
        "job_id": "job-slack",
        "transport": "inline",
        "status": "complete",
        "dataset": {"urn": "urn", "platform": "slack", "path": None, "format": None},
        "inline": {"media_type": "application/json", "data": [{"ts": "1"}], "row_count": 1, "metadata": {}},
    }
    responses = {("post", "/retrieval/retrieval/jobs"): job_payload}
    client = dummy_client(responses)
    service = RetrievalService(client)

    rows = service.slack_messages(
        "urn:li:dataset:(urn:li:dataPlatform:slack,C1,PROD)",
        channels=["C1", "C2"],
        include_replies=True,
        max_messages=10,
        since_ts="2024-10-01T00:00:00Z",
        until_ts=datetime(2024, 10, 10, 0, 0, 0),
        credential_override="token",
    )

    assert rows == [{"ts": "1"}]
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/retrieval/retrieval/jobs")
    payload = kwargs["json"]
    assert payload["dataset_urn"].startswith("urn:li:dataset")
    assert payload["transport"] == "inline"
    options = payload["options"]
    assert options["channels"] == ["C1", "C2"]
    assert options["include_replies"] is True
    assert options["max_messages"] == 10
    assert options["since_ts"] == "2024-10-01T00:00:00Z"
    assert options["until_ts"].startswith("2024-10-10")
    assert payload["credential_override"] == "token"


def test_slack_messages_requires_inline(dummy_client):
    job_payload = {
        "job_id": "job-slack",
        "transport": "sse",
        "status": "queued",
        "dataset": {"urn": "urn", "platform": "slack", "path": None, "format": None},
    }
    stream_response = DummyResponse(["event: complete", "data: {}", ""])
    responses = {
        ("post", "/retrieval/retrieval/jobs"): job_payload,
        ("get", "/retrieval/retrieval/jobs/job-slack/stream"): stream_response,
    }
    service = RetrievalService(dummy_client(responses))

    with pytest.raises(TransportNotSupportedError):
        service.slack_messages("urn:li:dataset:(urn:li:dataPlatform:slack,C1,PROD)", transport="sse")
