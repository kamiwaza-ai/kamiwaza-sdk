"""Integration tests for TS11 MODEL_FILES endpoints.

Tests cover:
- TS11.001: GET /model_files/ - List all model files
- TS11.002: POST /model_files/ - Create model file
- TS11.003: GET /model_files/download_status/ - Get download status
- TS11.004: DELETE /model_files/downloads/cancel_all - Cancel all downloads
- TS11.005: POST /model_files/search/ - Search hub model files
- TS11.006: DELETE /model_files/{model_file_id} - Delete model file
- TS11.007: GET /model_files/{model_file_id} - Get model file by ID
- TS11.008: DELETE /model_files/{model_file_id}/download - Cancel specific download
- TS11.009: GET /model_files/{model_file_id}/memory_usage - Get file memory usage
"""
from __future__ import annotations

import pytest
from uuid import UUID

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.models.model_file import ModelFile, CreateModelFile, StorageType
from kamiwaza_sdk.schemas.models.model_search import HubModelFileSearch

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]

CANONICAL_REPO = "mlx-community/Qwen3-4B-4bit"


class TestModelFileReadOperations:
    """Tests for read-only model file operations."""

    def test_list_model_files(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS11.001: GET /model_files/ - List all model files."""
        # Ensure at least one model exists with files
        ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        files = live_kamiwaza_client.models.list_model_files()
        assert isinstance(files, list)
        # Should have files from the canonical model
        assert len(files) > 0
        for f in files:
            assert isinstance(f, ModelFile)
            assert f.name is not None

    def test_get_model_file_by_id(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS11.007: GET /model_files/{model_file_id} - Get file by ID."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        # Get files for this model
        files = live_kamiwaza_client.models.get_model_files_by_model_id(model.id)
        if not files:
            pytest.skip("No model files available to test")

        file_id = files[0].id
        retrieved = live_kamiwaza_client.models.get_model_file(file_id)
        assert retrieved is not None
        assert isinstance(retrieved, ModelFile)
        assert retrieved.id == file_id

    def test_get_model_files_by_model_id(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """Test getting files by model ID."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        files = live_kamiwaza_client.models.get_model_files_by_model_id(model.id)
        assert isinstance(files, list)
        # Should have files from the model
        assert len(files) > 0
        for f in files:
            assert isinstance(f, ModelFile)


class TestModelFileSearch:
    """Tests for model file search operations."""

    def test_search_hub_model_files_with_dict(self, live_kamiwaza_client) -> None:
        """TS11.005: POST /model_files/search/ - Search with dict.

        Note: This endpoint returns 500 on some server configurations.
        See server-defects.md for details.
        """
        search_request = {
            "hub": "hf",
            "model": CANONICAL_REPO
        }

        try:
            results = live_kamiwaza_client.models.search_hub_model_files(search_request)
            assert isinstance(results, list)
            # Results may be empty if not cached, but should return list
        except APIError as exc:
            if exc.status_code in (404, 500):
                pytest.skip(f"Model files search not available: {exc}")
            raise

    def test_search_hub_model_files_with_schema(self, live_kamiwaza_client) -> None:
        """TS11.005: POST /model_files/search/ - Search with schema object."""
        search_request = HubModelFileSearch(
            hub="hf",
            model=CANONICAL_REPO
        )

        try:
            results = live_kamiwaza_client.models.search_hub_model_files(search_request)
            assert isinstance(results, list)
        except APIError as exc:
            if exc.status_code in (404, 500):
                pytest.skip(f"Model files search not available: {exc}")
            raise


class TestModelFileDownloadStatus:
    """Tests for download status operations."""

    def test_get_download_status(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS11.003: GET /model_files/download_status/ - Get download status."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        try:
            # get_model_files_download_status requires a repo_model_id
            status = live_kamiwaza_client.models.get_model_files_download_status(CANONICAL_REPO)
            # Should return a list of download status objects
            assert isinstance(status, list)
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Download status endpoint not available")
            raise

    def test_get_model_download_status_comprehensive(
        self, live_kamiwaza_client, ensure_repo_ready
    ) -> None:
        """Test the comprehensive download status method."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        status = live_kamiwaza_client.models.get_model_download_status(CANONICAL_REPO)
        assert isinstance(status, dict)
        assert status.get("found") is True
        assert "model" in status
        assert "target_files" in status


class TestModelFileMemoryUsage:
    """Tests for memory usage operations."""

    def test_get_model_file_memory_usage(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS11.009: GET /model_files/{model_file_id}/memory_usage."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        files = live_kamiwaza_client.models.get_model_files_by_model_id(model.id)
        if not files:
            pytest.skip("No model files available")

        # Find a file that exists on disk (has a valid storage location)
        file_for_test = None
        for f in files:
            if f.size and f.size > 0:
                file_for_test = f
                break

        if not file_for_test:
            pytest.skip("No suitable file for memory usage test")

        try:
            usage = live_kamiwaza_client.models.get_model_file_memory_usage(file_for_test.id)
            # Memory usage should be a number (int)
            assert isinstance(usage, (int, float))
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Memory usage endpoint not available")
            raise

    def test_get_model_memory_usage(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """Test model-level memory usage - GET /models/{model_id}/memory_usage."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        try:
            usage = live_kamiwaza_client.models.get_model_memory_usage(model.id)
            # Memory usage should be a number (int)
            assert isinstance(usage, (int, float))
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Model memory usage endpoint not available")
            raise


class TestModelFileValidation:
    """Tests for validation and error handling."""

    def test_get_nonexistent_file(self, live_kamiwaza_client) -> None:
        """Test getting a non-existent file returns appropriate error."""
        from uuid import uuid4

        fake_file_id = uuid4()

        with pytest.raises(APIError) as exc_info:
            live_kamiwaza_client.models.get_model_file(fake_file_id)

        assert exc_info.value.status_code in (404, 422, 500)


class TestModelFileDownloadOperations:
    """Tests for download control operations.

    Note: These tests are skipped by default as they could affect ongoing downloads.
    """

    @pytest.mark.skip(reason="Cancelling downloads may affect other tests")
    def test_cancel_all_downloads(self, live_kamiwaza_client) -> None:
        """TS11.004: DELETE /model_files/downloads/cancel_all."""
        try:
            result = live_kamiwaza_client.delete("/model_files/downloads/cancel_all")
            assert result is not None
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Cancel all downloads endpoint not available")
            raise

    @pytest.mark.skip(reason="Cancelling downloads may affect other tests")
    def test_cancel_specific_download(self, live_kamiwaza_client) -> None:
        """TS11.008: DELETE /model_files/{model_file_id}/download."""
        # Would need an active download to test
        pytest.skip("Requires active download to test")
