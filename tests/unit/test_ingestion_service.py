from __future__ import annotations

import pytest

from kamiwaza_sdk.schemas.ingestion import IngestJobCreate
from kamiwaza_sdk.services.ingestion import IngestionService

pytestmark = pytest.mark.unit


def test_run_active_returns_ingest_response(dummy_client):
    responses = {
        ("post", "/ingestion/ingest/run"): {"urns": ["urn:li:dataset:(s3,my,PROD)"], "status": "success", "errors": []}
    }
    client = dummy_client(responses)
    service = IngestionService(client)

    result = service.run_active("s3", bucket="bucket", prefix="data/")

    assert result.urns == ["urn:li:dataset:(s3,my,PROD)"]
    method, path, payload = client.calls[0]
    assert path == "/ingestion/ingest/run"
    assert payload["json"]["source_type"] == "s3"
    assert payload["json"]["kwargs"]["bucket"] == "bucket"


def test_emit_mcp_posts_payload(dummy_client):
    responses = {("post", "/ingestion/ingest/emit"): {"status": "ok"}}
    client = dummy_client(responses)
    service = IngestionService(client)

    status = service.emit_mcp({"entityUrn": "urn"})

    assert client.calls == [
        ("post", "/ingestion/ingest/emit", {"json": {"mcp": {"entityUrn": "urn"}}})
    ]
    assert status.status == "ok"


def test_schedule_job_and_status_round_trip(dummy_client):
    job = IngestJobCreate(job_id="nightly", schedule="0 0 * * *", source_type="s3")
    responses = {
        ("post", "/ingestion/ingest/jobs"): {"status": "scheduled"},
        ("get", "/ingestion/ingest/status/nightly"): {
            "job_id": "nightly",
            "status": "running",
            "error_count": 0,
            "created_urns": [],
            "last_run": "2025-01-01T00:00:00Z",
        },
    }
    client = dummy_client(responses)
    service = IngestionService(client)

    status_response = service.schedule_job(job)
    status = service.get_job_status("nightly")

    assert client.calls[0][1] == "/ingestion/ingest/jobs"
    assert status_response.status == "scheduled"
    assert status.job_id == "nightly"
    assert status.status == "running"
