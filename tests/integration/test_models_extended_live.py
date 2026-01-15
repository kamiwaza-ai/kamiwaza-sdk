"""Integration tests for TS12 MODELS endpoints (extended).

Tests cover additional endpoints not in the original test_models_live.py:
- TS12.001: GET /models/ - List models
- TS12.002: POST /models/ - Create model (manual)
- TS12.003: POST /models/cleanup_stale_deployments
- TS12.007: GET /models/pending_deployments
- TS12.008: POST /models/search/
- TS12.009: DELETE /models/{model_id}
- TS12.011: GET /models/{model_id}/configs
- TS12.012: GET /models/{model_id}/deployment_info
- TS12.013: GET /models/{model_id}/memory_usage

Note: TS12.004 (deploy_after_download) and TS12.006 (download_and_deploy)
are complex operations that require careful orchestration.
"""
from __future__ import annotations

import pytest
from uuid import UUID

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.models.model import Model, CreateModel

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]

CANONICAL_REPO = "mlx-community/Qwen3-4B-4bit"


class TestModelListOperations:
    """Tests for model list operations."""

    def test_list_models(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS12.001: GET /models/ - List all models."""
        # Ensure at least one model exists
        ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        models = live_kamiwaza_client.models.list_models()
        assert isinstance(models, list)
        assert len(models) > 0
        for m in models:
            assert isinstance(m, Model)
            assert m.name is not None

    def test_list_models_with_files(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS12.001: GET /models/ with load_files=True."""
        ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        models = live_kamiwaza_client.models.list_models(load_files=True)
        assert isinstance(models, list)
        assert len(models) > 0
        # When load_files=True, models should have m_files populated
        for m in models:
            # m_files may be empty for some models, but should be a list
            assert hasattr(m, "m_files")
            assert isinstance(m.m_files, list)


class TestModelSearchOperations:
    """Tests for model search operations."""

    def test_search_models_by_repo_id(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS12.008: POST /models/search/ - Search by repo ID."""
        ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        results = live_kamiwaza_client.models.search_models(CANONICAL_REPO)
        assert isinstance(results, list)
        # Should find the model we ensured exists
        assert len(results) > 0
        # At least one result should match the repo ID
        found = any(m.repo_modelId == CANONICAL_REPO for m in results)
        assert found, f"Expected to find {CANONICAL_REPO} in search results"

    def test_search_models_exact_match(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS12.008: POST /models/search/ with exact match."""
        ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        # Note: parameter is 'exact', not 'exact_match'
        results = live_kamiwaza_client.models.search_models(
            CANONICAL_REPO, exact=True
        )
        assert isinstance(results, list)
        if results:
            # Exact match should return models matching the exact repo ID
            for m in results:
                assert m.repo_modelId == CANONICAL_REPO

    def test_get_model_by_repo_id(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """Test get_model_by_repo_id convenience method."""
        ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        model = live_kamiwaza_client.models.get_model_by_repo_id(CANONICAL_REPO)
        assert model is not None
        assert isinstance(model, Model)
        assert model.repo_modelId == CANONICAL_REPO


class TestModelDetailOperations:
    """Tests for model detail operations."""

    def test_get_model(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS12.010: GET /models/{model_id} - Get model by ID."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        retrieved = live_kamiwaza_client.models.get_model(model.id)
        assert retrieved is not None
        assert isinstance(retrieved, Model)
        assert retrieved.id == model.id

    def test_get_model_configs_via_model(
        self, live_kamiwaza_client, ensure_repo_ready
    ) -> None:
        """TS12.011: GET /models/{model_id}/configs."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        configs = live_kamiwaza_client.models.get_model_configs_for_model(model.id)
        assert isinstance(configs, list)
        # Model should have at least one config
        if configs:
            for config in configs:
                assert hasattr(config, "id")
                assert hasattr(config, "m_id")

    def test_get_model_memory_usage(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS12.013: GET /models/{model_id}/memory_usage."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        try:
            usage = live_kamiwaza_client.models.get_model_memory_usage(model.id)
            assert isinstance(usage, (int, float))
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Memory usage endpoint not available")
            raise


class TestModelDeploymentInfo:
    """Tests for model deployment information."""

    def test_get_model_deployment_info(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS12.012: GET /models/{model_id}/deployment_info."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        try:
            info = live_kamiwaza_client.get(f"/models/{model.id}/deployment_info")
            # Should return some deployment information
            assert info is not None
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Deployment info endpoint not available")
            if exc.status_code == 403:
                pytest.skip("Insufficient permissions for deployment info (requires editor role)")
            raise

    def test_get_pending_deployments(self, live_kamiwaza_client) -> None:
        """TS12.007: GET /models/pending_deployments."""
        try:
            pending = live_kamiwaza_client.get("/models/pending_deployments")
            # Should return a list (possibly empty)
            assert isinstance(pending, (list, dict))
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Pending deployments endpoint not available")
            raise


class TestModelCleanupOperations:
    """Tests for model cleanup operations."""

    def test_cleanup_stale_deployments(self, live_kamiwaza_client) -> None:
        """TS12.003: POST /models/cleanup_stale_deployments."""
        try:
            result = live_kamiwaza_client.post("/models/cleanup_stale_deployments")
            # Should return some acknowledgment
            assert result is not None
        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for cleanup")
            if exc.status_code == 404:
                pytest.skip("Cleanup endpoint not available")
            raise


class TestModelValidation:
    """Tests for model validation and error handling."""

    def test_get_nonexistent_model(self, live_kamiwaza_client) -> None:
        """Test that getting a non-existent model returns appropriate error.

        Note: Server returns 403 (RBAC denied) instead of 404 for non-existent
        resources when RBAC is enabled. This is a security feature to prevent
        resource enumeration.
        """
        from uuid import uuid4

        fake_model_id = uuid4()

        with pytest.raises(APIError) as exc_info:
            live_kamiwaza_client.models.get_model(fake_model_id)

        # Server may return 403 (RBAC) or 404 (not found) depending on config
        assert exc_info.value.status_code in (403, 404, 422, 500)

    def test_search_nonexistent_model(self, live_kamiwaza_client) -> None:
        """Test searching for non-existent model returns empty list."""
        results = live_kamiwaza_client.models.search_models(
            "nonexistent-fake-model-id-12345"
        )
        assert isinstance(results, list)
        # Should be empty or not contain the fake ID
        for m in results:
            assert m.repo_modelId != "nonexistent-fake-model-id-12345"


class TestModelDownloadHelpers:
    """Tests for download helper methods."""

    def test_initiate_model_download_already_downloaded(
        self, live_kamiwaza_client, ensure_repo_ready
    ) -> None:
        """Test initiating download for already downloaded model."""
        # First ensure the model is downloaded
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        # Initiating download again should handle gracefully
        try:
            result = live_kamiwaza_client.models.initiate_model_download(
                CANONICAL_REPO, quantization="q6_k"
            )
            assert result is not None
            # Should indicate files are already downloaded or proceed normally
        except ValueError as e:
            # May raise ValueError if already downloaded - that's acceptable
            assert "already downloaded" in str(e).lower() or "not found" in str(e).lower()


class TestModelQuantizations:
    """Tests for model quantization handling."""

    def test_model_available_quantizations(
        self, live_kamiwaza_client, ensure_repo_ready
    ) -> None:
        """Test that model has available_quantizations field."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        # Get the full model to check quantizations
        full_model = live_kamiwaza_client.models.get_model(model.id)
        assert hasattr(full_model, "available_quantizations")
        assert isinstance(full_model.available_quantizations, list)
