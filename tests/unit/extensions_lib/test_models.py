"""Tests for kamiwaza_extensions_lib.models."""

import ssl

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
    async def test_uses_forwarded_bearer_token_for_authorization(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "http://model:8080/v1")

        request = MagicMock()
        request.headers = {
            "authorization": "Bearer user-access-token",
            "x-user-id": "usr-123",
            "x-auth-token": "jwt-abc",
        }

        client = await get_model_client(request)
        headers = getattr(client, "default_headers", None) or getattr(
            client, "_default_headers", {}
        )

        assert headers["Authorization"] == "Bearer user-access-token"
        assert headers["x-user-id"] == "usr-123"
        assert headers["x-auth-token"] == "jwt-abc"
        assert "authorization" not in headers

    @pytest.mark.asyncio
    async def test_honors_verify_ssl_setting(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "https://model:8080/v1")
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")

        request = MagicMock()
        request.headers = {}

        client = await get_model_client(request)
        ssl_context = client._client._transport._pool._ssl_context

        assert ssl_context.verify_mode == ssl.CERT_NONE
        assert ssl_context.check_hostname is False

    @pytest.mark.asyncio
    async def test_prefers_discovered_deployment_endpoint(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "https://kamiwaza.test/api")
        monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "https://kamiwaza.test")
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "https://kamiwaza.test/api/v1")

        request = MagicMock()
        request.headers = {
            "authorization": "Bearer user-access-token",
            "x-user-id": "usr-123",
        }

        deployments = [
            {
                "id": "dep-audio",
                "status": "DEPLOYED",
                "m_name": "transcribe",
                "engine_name": "aws_transcribe",
                "access_path": "/runtime/models/dep-audio",
            },
            {
                "id": "dep-chat",
                "status": "DEPLOYED",
                "m_name": "Qwen",
                "engine_name": "mlx",
                "access_path": "/runtime/models/dep-chat",
            },
        ]

        with patch.object(
            __import__("kamiwaza_extensions_lib.client", fromlist=["KamiwazaExtClient"]).KamiwazaExtClient,
            "from_env",
        ) as mock_from_env:
            mock_client = AsyncMock()
            mock_client.get_models = AsyncMock(return_value=deployments)
            mock_from_env.return_value = mock_client

            client = await get_model_client(request)

        assert str(client.base_url).rstrip("/") == "https://kamiwaza.test/runtime/models/dep-chat/v1"

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
        monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "https://kamiwaza.test/api")

        request = MagicMock()
        request.headers = {"x-user-id": "usr-123"}

        mock_deployments = [
            {
                "id": "dep-1",
                "m_name": "llama-3",
                "status": "DEPLOYED",
                "engine_name": "mlx",
                "access_path": "/runtime/models/dep-1",
            },
            {
                "id": "dep-2",
                "m_name": "transcribe",
                "status": "DEPLOYED",
                "engine_name": "aws_transcribe",
                "access_path": "/runtime/models/dep-2",
            },
            {"id": "dep-3", "m_name": "old-model", "status": "STOPPED"},
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
        assert models[0].type == "chat"
        assert models[0].capabilities == ["chat.completions"]
        assert models[0]._extra["endpoint"] == "https://kamiwaza.test/runtime/models/dep-1/v1"
        assert models[1].type == "audio"
        assert models[1].capabilities == ["audio.transcriptions"]

    @pytest.mark.asyncio
    async def test_normalizes_endpoint_field_from_platform_payload(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://api:7777/api")

        request = MagicMock()
        request.headers = {"x-user-id": "usr-123"}

        mock_deployments = [
            {
                "id": "dep-1",
                "m_name": "llama-3",
                "status": "DEPLOYED",
                "engine_name": "mlx",
                "endpoint": "https://kamiwaza.test/api/runtime/models/dep-1/v1",
            }
        ]

        with patch.object(
            __import__("kamiwaza_extensions_lib.client", fromlist=["KamiwazaExtClient"]).KamiwazaExtClient,
            "from_env",
        ) as mock_from_env:
            mock_client = AsyncMock()
            mock_client.get_models = AsyncMock(return_value=mock_deployments)
            mock_from_env.return_value = mock_client

            models = await list_available_models(request)

        assert models[0]._extra["endpoint"] == "https://kamiwaza.test/runtime/models/dep-1/v1"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_api_url(self, monkeypatch):
        monkeypatch.delenv("KAMIWAZA_API_URL", raising=False)

        request = MagicMock()
        request.headers = {}

        models = await list_available_models(request)
        assert models == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self, monkeypatch):
        import httpx
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://api:7777/api")

        request = MagicMock()
        request.headers = {}

        with patch.object(
            __import__("kamiwaza_extensions_lib.client", fromlist=["KamiwazaExtClient"]).KamiwazaExtClient,
            "from_env",
        ) as mock_from_env:
            mock_client = AsyncMock()
            mock_client.get_models = AsyncMock(
                side_effect=httpx.ConnectError("connection refused")
            )
            mock_from_env.return_value = mock_client

            models = await list_available_models(request)

        assert models == []

    @pytest.mark.asyncio
    async def test_propagates_programming_errors(self, monkeypatch):
        """Non-network errors (e.g., TypeError) should NOT be silently swallowed."""
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://api:7777/api")

        request = MagicMock()
        request.headers = {}

        with patch.object(
            __import__("kamiwaza_extensions_lib.client", fromlist=["KamiwazaExtClient"]).KamiwazaExtClient,
            "from_env",
        ) as mock_from_env:
            mock_client = AsyncMock()
            mock_client.get_models = AsyncMock(side_effect=TypeError("bad arg"))
            mock_from_env.return_value = mock_client

            with pytest.raises(TypeError):
                await list_available_models(request)
