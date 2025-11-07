from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kamiwaza_sdk.schemas.ingestion import IngestJobCreate
from kamiwaza_sdk.services.ingestion import IngestionService


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


def test_run_active_returns_ingest_response():
    responses = {
        ("post", "/ingestion/ingest/run"): {"urns": ["urn:li:dataset:(s3,my,PROD)"], "status": "success", "errors": []}
    }
    client = DummyClient(responses)
    service = IngestionService(client)

    result = service.run_active("s3", bucket="bucket", prefix="data/")

    assert result.urns == ["urn:li:dataset:(s3,my,PROD)"]
    method, path, payload = client.calls[0]
    assert path == "/ingestion/ingest/run"
    assert payload["json"]["source_type"] == "s3"
    assert payload["json"]["kwargs"]["bucket"] == "bucket"


def test_emit_mcp_posts_payload():
    responses = {("post", "/ingestion/ingest/emit"): {"status": "ok"}}
    client = DummyClient(responses)
    service = IngestionService(client)

    service.emit_mcp({"entityUrn": "urn"})

    assert client.calls == [
        ("post", "/ingestion/ingest/emit", {"json": {"mcp": {"entityUrn": "urn"}}})
    ]


def test_schedule_job_and_status_round_trip():
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
    client = DummyClient(responses)
    service = IngestionService(client)

    service.schedule_job(job)
    status = service.get_job_status("nightly")

    assert client.calls[0][1] == "/ingestion/ingest/jobs"
    assert status.job_id == "nightly"
    assert status.status == "running"
