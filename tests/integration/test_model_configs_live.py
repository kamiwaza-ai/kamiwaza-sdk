"""Integration tests for TS10 MODEL_CONFIGS endpoints.

Tests cover:
- TS10.001: GET /model_configs/ - List model configs
- TS10.002: POST /model_configs/ - Create model config
- TS10.003: DELETE /model_configs/{model_config_id} - Delete model config
- TS10.004: GET /model_configs/{model_config_id} - Get model config by ID
- TS10.005: PUT /model_configs/{model_config_id} - Update model config
"""
from __future__ import annotations

import pytest
from uuid import UUID

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.models.model import CreateModelConfig, ModelConfig

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]

CANONICAL_REPO = "mlx-community/Qwen3-4B-4bit"


class TestModelConfigReadOperations:
    """Tests for read-only model config operations."""

    def test_get_model_configs_for_model(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS10.001: GET /model_configs/ - List configs for a specific model."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        configs = live_kamiwaza_client.models.get_model_configs(model.id)
        assert isinstance(configs, list)
        # Model should have at least a default config
        for config in configs:
            assert isinstance(config, ModelConfig)
            assert config.m_id == model.id

    def test_get_model_configs_for_model_via_models_endpoint(
        self, live_kamiwaza_client, ensure_repo_ready
    ) -> None:
        """TS10.001: GET /models/{model_id}/configs - List configs via model endpoint."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        configs = live_kamiwaza_client.models.get_model_configs_for_model(model.id)
        assert isinstance(configs, list)
        for config in configs:
            assert isinstance(config, ModelConfig)

    def test_get_model_config_by_id(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS10.004: GET /model_configs/{model_config_id} - Get config by ID."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        configs = live_kamiwaza_client.models.get_model_configs(model.id)
        if not configs:
            pytest.skip("No configs available to test get by ID")

        config_id = configs[0].id
        retrieved = live_kamiwaza_client.models.get_model_config(config_id)
        assert retrieved is not None
        assert isinstance(retrieved, ModelConfig)
        assert retrieved.id == config_id


class TestModelConfigLifecycle:
    """Tests for model config CRUD lifecycle."""

    def test_create_and_delete_model_config(
        self, live_kamiwaza_client, ensure_repo_ready
    ) -> None:
        """TS10.002/003: Create and delete a model config."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        # Create a non-default config for testing
        create_payload = CreateModelConfig(
            m_id=model.id,
            name="sdk-test-config",
            default=False,
            description="Integration test config",
            config={"temperature": 0.7},
            system_config={"max_tokens": 1024}
        )

        created = None
        try:
            created = live_kamiwaza_client.models.create_model_config(create_payload)
            assert created is not None
            assert isinstance(created, ModelConfig)
            assert created.name == "sdk-test-config"
            assert created.m_id == model.id
            assert created.default is False
            # Note: API may serialize numeric values as strings
            assert created.config.get("temperature") in (0.7, "0.7")

            # Verify we can retrieve it
            retrieved = live_kamiwaza_client.models.get_model_config(created.id)
            assert retrieved.id == created.id

        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for model config operations")
            raise
        finally:
            # Cleanup
            if created:
                try:
                    live_kamiwaza_client.models.delete_model_config(created.id)
                except APIError:
                    pass  # Best effort cleanup

    def test_update_model_config(self, live_kamiwaza_client, ensure_repo_ready) -> None:
        """TS10.005: PUT /model_configs/{model_config_id} - Update config."""
        model = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

        # Create a config to update
        create_payload = CreateModelConfig(
            m_id=model.id,
            name="sdk-test-config-update",
            default=False,
            config={"temperature": 0.5}
        )

        created = None
        try:
            created = live_kamiwaza_client.models.create_model_config(create_payload)

            # Update the config
            update_payload = CreateModelConfig(
                m_id=model.id,
                name="sdk-test-config-updated",
                default=False,
                description="Updated description",
                config={"temperature": 0.9, "top_p": 0.95}
            )

            updated = live_kamiwaza_client.models.update_model_config(
                created.id, update_payload
            )
            assert updated is not None
            assert updated.name == "sdk-test-config-updated"
            # Note: API may serialize numeric values as strings
            assert updated.config.get("temperature") in (0.9, "0.9")
            assert updated.config.get("top_p") in (0.95, "0.95")

        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for model config operations")
            raise
        finally:
            # Cleanup
            if created:
                try:
                    live_kamiwaza_client.models.delete_model_config(created.id)
                except APIError:
                    pass


class TestModelConfigValidation:
    """Tests for model config validation behavior."""

    def test_create_config_requires_model_id(self, live_kamiwaza_client) -> None:
        """Test that creating config without valid model_id fails appropriately."""
        from uuid import uuid4

        fake_model_id = uuid4()
        create_payload = CreateModelConfig(
            m_id=fake_model_id,
            name="should-fail",
            default=False
        )

        with pytest.raises(APIError) as exc_info:
            live_kamiwaza_client.models.create_model_config(create_payload)

        # Should get an error for non-existent model
        # Note: Server returns 500 instead of 400/404/422 (see server-defects.md)
        assert exc_info.value.status_code in (400, 404, 422, 500)

    def test_get_nonexistent_config(self, live_kamiwaza_client) -> None:
        """Test that getting a non-existent config returns appropriate error."""
        from uuid import uuid4

        fake_config_id = uuid4()

        with pytest.raises(APIError) as exc_info:
            live_kamiwaza_client.models.get_model_config(fake_config_id)

        assert exc_info.value.status_code in (404, 422)
