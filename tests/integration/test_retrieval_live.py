"""Integration tests for TS17 RETRIEVAL endpoints.

Tests cover:
- TS17.001: POST /retrieval/jobs - Create retrieval job (covered by existing tests)
- TS17.002: GET /retrieval/jobs/{job_id} - Get job status
- TS17.003: GET /retrieval/jobs/{job_id}/stream - Stream job events (covered by existing tests)

Note: TS17.001 and TS17.003 are already tested in test_catalog_ingest_retrieval.py
and test_catalog_multi_source.py. This file focuses on direct SDK method testing
and TS17.002 coverage.
"""
from __future__ import annotations

import json
import pytest
from uuid import uuid4

from kamiwaza_sdk.exceptions import APIError, DatasetNotFoundError
from kamiwaza_sdk.services.retrieval import RetrievalService
from kamiwaza_sdk.schemas.retrieval import RetrievalJobStatus

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _ingest_sample_dataset(client, ingestion_environment: dict[str, str]) -> str:
    bucket = ingestion_environment["bucket"]
    prefix = ingestion_environment["prefix"]
    endpoint = ingestion_environment["endpoint"]

    ingest_response = client.ingestion.run_active(
        "s3",
        bucket=bucket,
        prefix=prefix,
        recursive=True,
        endpoint_url=endpoint,
        region="us-east-1",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
    )
    urns = ingest_response.urns
    assert urns, "ingestion did not return dataset URNs"
    return urns[0]


def _inline_payload(dataset_urn: str, endpoint: str) -> dict[str, str]:
    return {
        "dataset_urn": dataset_urn,
        "transport": "inline",
        "format_hint": "parquet",
        "credential_override": json.dumps(
            {
                "aws_access_key_id": "minioadmin",
                "aws_secret_access_key": "minioadmin",
                "endpoint": endpoint,
                "endpoint_override": endpoint,
                "endpoint_url": endpoint,
                "region": "us-east-1",
            }
        ),
    }


class TestRetrievalServiceAvailability:
    """Tests for retrieval service availability."""

    def test_retrieval_service_accessible(self, live_kamiwaza_client) -> None:
        """Test that the retrieval service is accessible."""
        service = live_kamiwaza_client.retrieval
        assert service is not None
        assert isinstance(service, RetrievalService)


class TestRetrievalJobStatus:
    """Tests for retrieval job status endpoint."""

    def test_get_nonexistent_job_status(self, live_kamiwaza_client) -> None:
        """TS17.002: GET /retrieval/jobs/{job_id} - Get status for non-existent job.

        Verifies that requesting status for a non-existent job returns
        an appropriate error response.
        """
        fake_job_id = str(uuid4())

        with pytest.raises((DatasetNotFoundError, APIError)):
            live_kamiwaza_client.retrieval.get_job(fake_job_id)

    def test_get_job_via_direct_api(self, live_kamiwaza_client) -> None:
        """TS17.002: GET /retrieval/jobs/{job_id} - Direct API test.

        Tests the endpoint directly to verify it exists and responds correctly.
        """
        fake_job_id = str(uuid4())

        try:
            response = live_kamiwaza_client.get(f"/retrieval/jobs/{fake_job_id}")
            # If we get here, the job exists (unlikely with random UUID)
            assert response is not None
        except APIError as exc:
            # 404 is expected for non-existent job
            if exc.status_code == 404:
                # This confirms the endpoint exists and works
                pass
            elif exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for retrieval endpoint")
            else:
                raise


class TestRetrievalJobOperations:
    """Tests for retrieval job operations.

    Note: Full job creation and streaming tests require a registered dataset
    which is covered by test_catalog_ingest_retrieval.py and test_catalog_multi_source.py.
    """

    def test_create_and_get_job(
        self,
        live_kamiwaza_client,
        ingestion_environment: dict[str, str],
    ) -> None:
        """TS17.001 + TS17.002: Create a retrieval job and fetch its status."""
        client = live_kamiwaza_client
        endpoint = ingestion_environment["endpoint"]
        dataset_urn: str | None = None

        try:
            dataset_urn = _ingest_sample_dataset(client, ingestion_environment)
            job = client.post("/retrieval/jobs", json=_inline_payload(dataset_urn, endpoint))

            job_id = str(job["job_id"])
            assert job_id
            assert job["transport"] == "inline"

            direct_status = client.get(f"/retrieval/jobs/{job_id}")
            assert str(direct_status.get("job_id")) == job_id
            assert direct_status.get("transport") == "inline"
            assert direct_status.get("dataset", {}).get("urn") == dataset_urn
            assert isinstance(direct_status.get("status"), str)
            assert direct_status["status"]

            typed_status = client.retrieval.get_job(job_id)
            assert isinstance(typed_status, RetrievalJobStatus)
            assert str(typed_status.job_id) == job_id
            assert typed_status.transport.value == "inline"
            assert typed_status.dataset.urn == dataset_urn
        finally:
            if dataset_urn:
                client.delete("/catalog/datasets/by-urn", params={"urn": dataset_urn})
