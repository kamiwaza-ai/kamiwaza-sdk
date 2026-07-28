"""Tests for kamiwaza_extensions_lib.client."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from starlette.datastructures import Headers

from kamiwaza_extensions_lib.auth import forward_auth_headers
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
        assert async_client._trust_env is False
        # Clean up
        asyncio.run(async_client.aclose())

    def test_client_preserves_non_ascii_header_wire_bytes(self):
        incoming = Headers(
            raw=[
                (b"X-User-Name", "José".encode()),
                (b"X-User-Groups", "Ingénierie".encode()),
            ]
        )
        forwarded = forward_auth_headers(incoming)
        client = KamiwazaExtClient(
            api_base="http://api:7777",
            headers={"X-User-Name": forwarded["X-User-Name"]},
        )

        async_client = client._client({"X-User-Groups": forwarded["X-User-Groups"]})

        assert (b"X-User-Name", "José".encode()) in async_client.headers.raw
        assert (b"X-User-Groups", "Ingénierie".encode()) in async_client.headers.raw
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
    async def test_get_models_preserves_complete_envelope_and_promotes_auth_token(
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
                    "X-User-Groups": "engineering,search",
                    "X-User-Attributes-Hash": "sha256:attributes",
                    "X-Auth-Token": "jwt-abc",
                    "X-Auth-Azp": "chatbot-app",
                    "X-Workroom-Id": "wrk-123",
                    "X-User-Signature-Stable": "stable-signature",
                }
            )

            forwarded_headers = MockClient.call_args.kwargs["headers"]
            assert forwarded_headers["Authorization"] == "Bearer jwt-abc"
            assert forwarded_headers["X-Auth-Token"] == "jwt-abc"
            assert forwarded_headers["X-Workroom-Id"] == "wrk-123"
            assert forwarded_headers["X-User-Id"] == "usr-123"
            assert forwarded_headers["X-User-Roles"] == "admin,user"
            assert forwarded_headers["X-User-Groups"] == "engineering,search"
            assert forwarded_headers["X-User-Attributes-Hash"] == "sha256:attributes"
            assert forwarded_headers["X-Auth-Azp"] == "chatbot-app"
            assert forwarded_headers["X-User-Signature-Stable"] == "stable-signature"

    @pytest.mark.asyncio
    async def test_get_models_preserves_non_ascii_envelope_bytes(self):
        client = KamiwazaExtClient(
            api_base="http://api:7777/api",
            openai_base="",
        )
        incoming = Headers(
            raw=[
                (b"x-user-name", "José".encode()),
                (b"x-user-id", b"usr-123"),
            ]
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

            await client.get_models(headers=incoming)

        forwarded_headers = MockClient.call_args.kwargs["headers"]
        assert forwarded_headers["x-user-name"] == "José"
        assert (b"x-user-name", "José".encode()) in forwarded_headers.raw

    @pytest.mark.asyncio
    async def test_get_models_filters_httpx_headers_and_joins_duplicate_cookies(self):
        client = KamiwazaExtClient(
            api_base="http://api:7777/api",
            openai_base="",
        )
        incoming = httpx.Headers(
            [
                (b"host", b"internal-victim"),
                (b"x-forwarded-for", b"198.51.100.10"),
                (b"x-envoy-original-path", b"/admin"),
                (b"x-original-uri", b"/admin"),
                (b"x-user-id", b"usr-123"),
                (b"cookie", b"session=opaque"),
                (b"cookie", b"workroom=wrk-123"),
            ]
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

            await client.get_models(headers=incoming)

        forwarded_headers = MockClient.call_args.kwargs["headers"]
        assert forwarded_headers["x-user-id"] == "usr-123"
        assert forwarded_headers["cookie"] == "session=opaque; workroom=wrk-123"
        assert "host" not in forwarded_headers
        assert "x-forwarded-for" not in forwarded_headers
        assert "x-envoy-original-path" not in forwarded_headers
        assert "x-original-uri" not in forwarded_headers

    def test_platform_auth_headers_encode_manual_unicode_as_utf8(self):
        forwarded_headers = KamiwazaExtClient._platform_auth_headers(
            {
                "X-User-Id": "usr-123",
                "X-User-Name": "山田",
            }
        )

        assert (b"X-User-Name", "山田".encode()) in forwarded_headers.raw

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
