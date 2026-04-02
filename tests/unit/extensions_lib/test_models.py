"""Tests for kamiwaza_extensions_lib.models."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from kamiwaza_extensions_lib.models import AvailableModel, get_model_client, list_available_models


@pytest.mark.unit
class TestAvailableModel:
    def test_from_dict_standard_fields(self):
        data = {
            "id": "dep-123",
            "name": "llama-3",
            "repo_id": "meta-llama/Llama-3-8B",
            "type": "chat",
            "capabilities": ["function_calling"],
            "status": "running",
        }
        model = AvailableModel.from_dict(data)

        assert model.id == "dep-123"
        assert model.name == "llama-3"
        assert model.repo_id == "meta-llama/Llama-3-8B"
        assert model.type == "chat"
        assert model.capabilities == ["function_calling"]
        assert model.status == "running"

    def test_from_dict_deployment_format(self):
        """Platform API returns deployment_id and model_name."""
        data = {
            "deployment_id": "dep-456",
            "model_name": "gpt-4",
            "phase": "Running",
        }
        model = AvailableModel.from_dict(data)

        assert model.id == "dep-456"
        assert model.name == "gpt-4"
        assert model.status == "Running"

    def test_from_dict_empty(self):
        model = AvailableModel.from_dict({})

        assert model.id == ""
        assert model.name == ""
        assert model.status == "unknown"

    def test_from_dict_extra_fields_preserved(self):
        data = {
            "id": "dep-123",
            "name": "llama",
            "gpu_count": 2,
            "endpoint_url": "http://model:8080",
        }
        model = AvailableModel.from_dict(data)

        assert model._extra["gpu_count"] == 2
        assert model._extra["endpoint_url"] == "http://model:8080"


@pytest.mark.unit
class TestGetModelClient:
    @pytest.mark.asyncio
    async def test_returns_async_openai_client(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "http://model:8080/v1")

        request = MagicMock()
        request.headers = {
            "x-user-id": "usr-123",
            "x-auth-token": "jwt-abc",
        }

        client = await get_model_client(request)

        assert client.base_url is not None
        assert str(client.base_url).rstrip("/").endswith("/v1")

    @pytest.mark.asyncio
    async def test_raises_without_endpoint(self, monkeypatch):
        monkeypatch.delenv("KAMIWAZA_ENDPOINT", raising=False)
        monkeypatch.delenv("KAMIWAZA_MODEL_URL", raising=False)

        request = MagicMock()
        request.headers = {}

        with pytest.raises(RuntimeError, match="KAMIWAZA_ENDPOINT"):
            await get_model_client(request)


@pytest.mark.unit
class TestListAvailableModels:
    @pytest.mark.asyncio
    async def test_returns_typed_models(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://api:7777/api")

        request = MagicMock()
        request.headers = {"x-user-id": "usr-123"}

        mock_deployments = [
            {"id": "dep-1", "name": "llama-3", "status": "running"},
            {"id": "dep-2", "name": "gpt-4", "status": "starting"},
        ]

        with patch.object(
            __import__("kamiwaza_extensions_lib.client", fromlist=["KamiwazaExtClient"]).KamiwazaExtClient,
            "from_env",
        ) as mock_from_env:
            mock_client = AsyncMock()
            mock_client.get_models = AsyncMock(return_value=mock_deployments)
            mock_from_env.return_value = mock_client

            models = await list_available_models(request)

        assert len(models) == 2
        assert models[0].id == "dep-1"
        assert models[0].name == "llama-3"
        assert models[1].status == "starting"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_api_url(self, monkeypatch):
        monkeypatch.delenv("KAMIWAZA_API_URL", raising=False)

        request = MagicMock()
        request.headers = {}

        models = await list_available_models(request)
        assert models == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_api_error(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://api:7777/api")

        request = MagicMock()
        request.headers = {}

        with patch.object(
            __import__("kamiwaza_extensions_lib.client", fromlist=["KamiwazaExtClient"]).KamiwazaExtClient,
            "from_env",
        ) as mock_from_env:
            mock_client = AsyncMock()
            mock_client.get_models = AsyncMock(side_effect=Exception("connection refused"))
            mock_from_env.return_value = mock_client

            models = await list_available_models(request)

        assert models == []
