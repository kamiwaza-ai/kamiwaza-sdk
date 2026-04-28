"""Tests for kamiwaza_extensions_lib.client."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from kamiwaza_extensions_lib.client import KamiwazaExtClient


@pytest.mark.unit
class TestKamiwazaExtClientInit:
    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://api:7777/api")
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "http://model:8080/v1")
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "true")

        client = KamiwazaExtClient.from_env()

        assert client.api_base == "http://api:7777/api"
        assert client.openai_base == "http://model:8080/v1"
        assert client._verify_ssl is True

    def test_from_env_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://api:7777/api/")
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "http://model:8080/v1/")

        client = KamiwazaExtClient.from_env()

        assert client.api_base == "http://api:7777/api"
        assert client.openai_base == "http://model:8080/v1"

    def test_service_account(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://api:7777/api")
        monkeypatch.setenv("KAMIWAZA_API_KEY", "pat-secret")

        client = KamiwazaExtClient.service_account()

        assert client._default_headers["Authorization"] == "Bearer pat-secret"

    def test_service_account_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("KAMIWAZA_API_KEY", raising=False)
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://api:7777/api")

        with pytest.raises(RuntimeError, match="KAMIWAZA_API_KEY"):
            KamiwazaExtClient.service_account()

    def test_direct_init(self):
        client = KamiwazaExtClient(
            api_base="http://api:7777/api",
            openai_base="http://model:8080/v1",
            headers={"Authorization": "Bearer test"},
        )

        assert client.api_base == "http://api:7777/api"
        assert client.openai_base == "http://model:8080/v1"
        assert client._default_headers["Authorization"] == "Bearer test"

    def test_default_timeout(self):
        client = KamiwazaExtClient(api_base="http://api:7777")
        assert client._timeout == httpx.Timeout(30.0)

    def test_custom_timeout(self):
        client = KamiwazaExtClient(api_base="http://api:7777", timeout=60.0)
        assert client._timeout == httpx.Timeout(60.0)

    def test_client_includes_timeout(self):
        client = KamiwazaExtClient(api_base="http://api:7777", timeout=15.0)
        async_client = client._client()
        assert async_client.timeout == httpx.Timeout(15.0)
        # Clean up
        asyncio.run(async_client.aclose())


@pytest.mark.unit
class TestKamiwazaExtClientMethods:
    @pytest.mark.asyncio
    async def test_chat_completions_raises_without_endpoint(self):
        client = KamiwazaExtClient(api_base="http://api:7777", openai_base="")

        with pytest.raises(RuntimeError, match="KAMIWAZA_ENDPOINT"):
            await client.chat_completions({"model": "gpt-4", "messages": []})

    @pytest.mark.asyncio
    async def test_get_models_raises_without_api_base(self):
        client = KamiwazaExtClient(api_base="", openai_base="http://model:8080")

        with pytest.raises(RuntimeError, match="KAMIWAZA_API_URL"):
            await client.get_models()

    @pytest.mark.asyncio
    async def test_chat_completions_calls_correct_url(self):
        client = KamiwazaExtClient(
            api_base="http://api:7777",
            openai_base="http://model:8080/v1",
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("kamiwaza_extensions_lib.client.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            await client.chat_completions(
                {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
            )

            mock_instance.post.assert_called_once_with(
                "http://model:8080/v1/chat/completions",
                json={
                    "model": "gpt-4",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )

    @pytest.mark.asyncio
    async def test_get_models_calls_correct_url(self):
        client = KamiwazaExtClient(
            api_base="http://api:7777/api",
            openai_base="",
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [{"id": "d1", "model_name": "llama"}]

        with patch("kamiwaza_extensions_lib.client.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            result = await client.get_models()

            mock_instance.get.assert_called_once_with(
                "http://api:7777/api/serving/deployments"
            )
            assert result == [{"id": "d1", "model_name": "llama"}]

    @pytest.mark.asyncio
    async def test_get_models_strips_forwarded_user_headers_and_promotes_auth_token(
        self,
    ):
        client = KamiwazaExtClient(
            api_base="http://api:7777/api",
            openai_base="",
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []

        with patch("kamiwaza_extensions_lib.client.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            await client.get_models(
                headers={
                    "X-User-Id": "usr-123",
                    "X-User-Roles": "admin,user",
                    "X-Auth-Token": "jwt-abc",
                    "X-Workroom-Id": "wrk-123",
                }
            )

            forwarded_headers = MockClient.call_args.kwargs["headers"]
            assert forwarded_headers["Authorization"] == "Bearer jwt-abc"
            assert forwarded_headers["X-Auth-Token"] == "jwt-abc"
            assert forwarded_headers["X-Workroom-Id"] == "wrk-123"
            assert "X-User-Id" not in forwarded_headers
            assert "X-User-Roles" not in forwarded_headers

    @pytest.mark.asyncio
    async def test_get_models_filters_out_stopped_deployments(self):
        client = KamiwazaExtClient(
            api_base="http://api:7777/api",
            openai_base="",
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"id": "dep-1", "status": "DEPLOYED"},
            {"id": "dep-2", "status": "STOPPED"},
            {"id": "dep-3", "status": "running"},
        ]

        with patch("kamiwaza_extensions_lib.client.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            result = await client.get_models()

        assert result == [
            {"id": "dep-1", "status": "DEPLOYED"},
            {"id": "dep-3", "status": "running"},
        ]

    @pytest.mark.asyncio
    async def test_get_models_falls_back_to_legacy_active_endpoint(self):
        client = KamiwazaExtClient(
            api_base="http://api:7777/api",
            openai_base="",
        )

        not_found_request = httpx.Request(
            "GET", "http://api:7777/api/serving/deployments"
        )
        not_found_response = httpx.Response(404, request=not_found_request)
        fallback_response = MagicMock()
        fallback_response.raise_for_status = MagicMock()
        fallback_response.json.return_value = [{"id": "d1", "model_name": "llama"}]

        with patch("kamiwaza_extensions_lib.client.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(
                side_effect=[
                    httpx.HTTPStatusError(
                        "Not Found",
                        request=not_found_request,
                        response=not_found_response,
                    ),
                    fallback_response,
                ]
            )
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            result = await client.get_models()

            assert mock_instance.get.await_args_list[0].args == (
                "http://api:7777/api/serving/deployments",
            )
            assert mock_instance.get.await_args_list[1].args == (
                "http://api:7777/api/serving/deployments/active",
            )
            assert result == [{"id": "d1", "model_name": "llama"}]
