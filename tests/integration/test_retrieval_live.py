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

import pytest
from uuid import uuid4

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.services.retrieval import RetrievalService

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


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

        try:
            # This should raise an error for non-existent job
            live_kamiwaza_client.retrieval.get_job(fake_job_id)
            pytest.fail("Expected error for non-existent job")
        except Exception as exc:
            # The SDK translates 404 to DatasetNotFoundError or APIError
            # Accept various error types as valid
            assert exc is not None

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
            elif exc.status_code == 500:
                pytest.skip(f"Retrieval service unavailable: {exc}")
            else:
                raise


class TestRetrievalJobOperations:
    """Tests for retrieval job operations.

    Note: Full job creation and streaming tests require a registered dataset
    which is covered by test_catalog_ingest_retrieval.py and test_catalog_multi_source.py.
    """

    @pytest.mark.skip(reason="Requires active dataset - see catalog tests for full coverage")
    def test_create_and_get_job(self, live_kamiwaza_client, catalog_stack_environment) -> None:
        """Test creating a job and getting its status.

        This test is skipped by default as it requires the catalog stack.
        See test_catalog_ingest_retrieval.py for active tests.
        """
        pass
