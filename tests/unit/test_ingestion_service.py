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


def test_ingestion_health_hits_endpoint(dummy_client):
    responses = {("get", "/ingestion/health"): {"status": "ok"}}
    client = dummy_client(responses)
    service = IngestionService(client)

    health = service.health()

    assert health["status"] == "ok"
    assert client.calls[0][:2] == ("get", "/ingestion/health")


def test_run_slack_ingest_builds_payload(dummy_client):
    responses = {
        ("post", "/ingestion/ingest/run"): {"urns": ["urn:li:dataset:(slack,C1,PROD)"], "status": "success", "errors": []}
    }
    client = dummy_client(responses)
    service = IngestionService(client)

    result = service.run_slack_ingest(
        channels=["C1", "#general"],
        channel_selector="all",
        include_dm=True,
        token="xoxb-123",
        token_secret_name="slack_bot",
        team_id="T1",
        max_messages=25,
        since_ts="2024-10-01T00:00:00Z",
        until_ts="2024-10-10T00:00:00Z",
        extra_kwargs={"force_refresh": True},
    )

    assert result.urns
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/ingestion/ingest/run")
    body = kwargs["json"]
    assert body["source_type"] == "slack"
    slack_kwargs = body["kwargs"]
    assert slack_kwargs["channels"] == ["C1", "#general"]
    assert slack_kwargs["channel_selector"] == "all"
    assert slack_kwargs["include_dm"] is True
    assert slack_kwargs["token"] == "xoxb-123"
    assert slack_kwargs["token_secret_name"] == "slack_bot"
    assert slack_kwargs["team_id"] == "T1"
    assert slack_kwargs["max_messages"] == 25
    assert slack_kwargs["since_ts"] == "2024-10-01T00:00:00Z"
    assert slack_kwargs["force_refresh"] is True
