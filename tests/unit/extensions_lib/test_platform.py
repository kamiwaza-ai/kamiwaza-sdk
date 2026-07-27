"""Tests for safe extension-backend calls to the Kamiwaza platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.datastructures import Headers

from kamiwaza_extensions_lib.errors import (
    PlatformOutageError,
    PlatformRedirectError,
    UnexpectedContextError,
)
from kamiwaza_extensions_lib.platform import platform_request


def _request(headers: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(headers=Headers(headers or {}))


@pytest.mark.unit
class TestPlatformRequest:
    @pytest.mark.asyncio
    async def test_uses_container_base_and_forwards_current_envelope(
        self, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/gateway/api")
        monkeypatch.setenv(
            "KAMIWAZA_PUBLIC_API_URL", "https://browser.example.test/api"
        )
        expected_url = "http://core-api:7777/gateway/api/catalog/datasets/?limit=10"
        httpx_mock.add_response(method="GET", url=expected_url, json=[])
        envelope = {
            "Cookie": "session=opaque",
            "X-Auth-Token": "runtime-token",
            "X-Auth-Azp": "chat-with-docs",
            "X-User-Id": "usr-1",
            "X-User-Groups": "engineering,search",
            "X-User-Attributes-Hash": "sha256:attributes",
            "X-Workroom-Id": "wrk-1",
            "X-User-Workroom-Id": "wrk-1",
            "X-User-Signature": "legacy-signature",
            "X-User-Signature-Stable": "stable-signature",
            "X-User-Signature-Ts": "1784390400",
        }

        response = await platform_request(
            _request(envelope),
            "get",
            "/api/catalog/datasets/",
            params={"limit": 10},
            headers={"Accept": "application/json"},
        )

        assert response.status_code == 200
        outbound = httpx_mock.get_request()
        assert outbound is not None
        assert str(outbound.url) == expected_url
        assert outbound.headers["accept"] == "application/json"
        for name, value in envelope.items():
            assert outbound.headers[name] == value

    @pytest.mark.asyncio
    async def test_container_base_without_api_suffix_uses_explicit_api_path(
        self, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777")
        expected_url = "http://core-api:7777/api/catalog/datasets/"
        httpx_mock.add_response(method="GET", url=expected_url, json=[])

        await platform_request(
            _request(),
            "GET",
            "/api/catalog/datasets/",
        )

        outbound = httpx_mock.get_request()
        assert outbound is not None
        assert str(outbound.url) == expected_url

    @pytest.mark.asyncio
    async def test_strips_container_base_whitespace_before_api_suffix(
        self, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv(
            "KAMIWAZA_API_URL",
            "  http://core-api:7777/api\n",
        )
        expected_url = "http://core-api:7777/api/catalog/datasets/"
        httpx_mock.add_response(method="GET", url=expected_url, json=[])

        await platform_request(
            _request(),
            "GET",
            "/api/catalog/datasets/",
        )

        outbound = httpx_mock.get_request()
        assert outbound is not None
        assert str(outbound.url) == expected_url

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [301, 302, 303, 307, 308])
    async def test_raises_typed_error_on_redirect_without_following_it(
        self, monkeypatch, httpx_mock, status_code
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")
        httpx_mock.add_response(
            method="GET",
            url="http://core-api:7777/api/catalog/datasets",
            status_code=status_code,
            headers={"Location": "/api/catalog/datasets/"},
        )

        with pytest.raises(PlatformRedirectError) as exc_info:
            await platform_request(
                _request({"Cookie": "session=opaque"}),
                "GET",
                "/api/catalog/datasets",
            )

        assert exc_info.value.status_code == status_code
        assert exc_info.value.location == "/api/catalog/datasets/"
        assert len(httpx_mock.get_requests()) == 1

    @pytest.mark.asyncio
    async def test_returns_not_modified_response_unchanged(
        self, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")
        httpx_mock.add_response(
            method="GET",
            url="http://core-api:7777/api/catalog/datasets/",
            status_code=304,
        )

        response = await platform_request(
            _request(),
            "GET",
            "/api/catalog/datasets/",
            headers={"If-None-Match": '"catalog-v1"'},
        )

        assert response.status_code == 304

    @pytest.mark.asyncio
    async def test_raises_typed_error_on_redirect_without_location(
        self, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")
        httpx_mock.add_response(
            method="GET",
            url="http://core-api:7777/api/catalog/datasets",
            status_code=302,
        )

        with pytest.raises(PlatformRedirectError) as exc_info:
            await platform_request(
                _request(),
                "GET",
                "/api/catalog/datasets",
            )

        assert exc_info.value.status_code == 302
        assert exc_info.value.location is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        [
            "https://attacker.example/api/catalog/datasets/",
            "//attacker.example/api/catalog/datasets/",
            "//[::1",
            "api/catalog/datasets/",
            "/catalog/datasets/",
            "/api/catalog/datasets/#fragment",
            "/api\\catalog\\datasets",
            "/api/catalog/../admin/users",
            "/api/catalog/%2e%2e/admin/users",
            "/api/catalog/%252e%252e/admin/users",
            "/%2f/attacker.example/api",
            "/api%5ccatalog%5cdatasets",
            "/api/catalog/search?tenant=acme",
            "/api/catalog/x\ny",
            "/api/catalog/x%0dy",
        ],
    )
    async def test_rejects_non_root_relative_or_ambiguous_paths(
        self, monkeypatch, path
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")

        with pytest.raises(ValueError, match="root-relative"):
            await platform_request(_request(), "GET", path)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["", "GET\r\nX-Injected: true", "GET SP"])
    async def test_rejects_invalid_method_tokens(self, monkeypatch, method):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")

        with pytest.raises(ValueError, match="HTTP method token"):
            await platform_request(
                _request(),
                method,
                "/api/catalog/datasets/",
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "headers",
        [
            {"X-Client-Name": "safe\r\nX-Injected: true"},
            {"X-Client-\nName": "unsafe"},
            {"X Client Name": "unsafe"},
            {"": "unsafe"},
        ],
    )
    async def test_rejects_control_characters_in_caller_headers(
        self, monkeypatch, headers
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")

        with pytest.raises(ValueError, match="invalid header"):
            await platform_request(
                _request(),
                "GET",
                "/api/catalog/datasets/",
                headers=headers,
            )

    @pytest.mark.asyncio
    async def test_rejects_control_characters_in_forwarded_headers(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")

        with (
            patch(
                "kamiwaza_extensions_lib.platform.forward_auth_headers",
                return_value={"X-User-Id": "usr-1\r\nX-Injected: true"},
            ),
            pytest.raises(ValueError, match="invalid header"),
        ):
            await platform_request(
                _request(),
                "GET",
                "/api/catalog/datasets/",
            )

    @pytest.mark.asyncio
    async def test_combines_duplicate_inbound_cookie_fields(
        self, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")
        url = "http://core-api:7777/api/catalog/datasets/"
        httpx_mock.add_response(method="GET", url=url, json=[])
        incoming = Headers(
            raw=[
                (b"cookie", b"session=opaque"),
                (b"cookie", b"csrf=bound"),
                (b"x-user-id", b"usr-1"),
            ]
        )

        await platform_request(
            SimpleNamespace(headers=incoming),
            "GET",
            "/api/catalog/datasets/",
        )

        outbound = httpx_mock.get_request()
        assert outbound is not None
        assert outbound.headers["cookie"] == "session=opaque; csrf=bound"

    @pytest.mark.asyncio
    async def test_preserves_non_ascii_forwarded_header_bytes(
        self, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")
        url = "http://core-api:7777/api/catalog/datasets/"
        httpx_mock.add_response(method="GET", url=url, json=[])
        incoming = Headers(
            raw=[
                (b"x-user-name", "José".encode()),
                (b"x-user-id", b"usr-1"),
            ]
        )

        await platform_request(
            SimpleNamespace(headers=incoming),
            "GET",
            "/api/catalog/datasets/",
        )

        outbound = httpx_mock.get_request()
        assert outbound is not None
        assert outbound.headers["x-user-name"] == "José"
        assert (b"x-user-name", "José".encode()) in outbound.headers.raw

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "headers",
        [
            {"Authorization": "Bearer attacker-controlled"},
            {"Cookie": "session=attacker-controlled"},
            {"X-Workroom-Id": "another-workroom"},
            {"Host": "attacker.example"},
            {"Content-Length": "0"},
            {"Transfer-Encoding": "chunked"},
            {"Connection": "keep-alive"},
            {"Keep-Alive": "timeout=5"},
            {"Proxy-Authenticate": "Basic"},
            {"Proxy-Authorization": "Basic attacker-controlled"},
            {"TE": "trailers"},
            {"Trailer": "X-Checksum"},
            {"Upgrade": "websocket"},
            {"Expect": "100-continue"},
            {"X-HTTP-Method-Override": "DELETE"},
            {"X-Forwarded-Host": "attacker.example"},
            {"X-Forwarded-Prefix": "/admin"},
            {"X-Original-URL": "/admin/users"},
            {"X-Rewrite-URL": "/admin/users"},
            {"X-Real-IP": "127.0.0.1"},
            {"X-User-Future-Claim": "attacker-controlled"},
            {"X-Auth-Future-Claim": "attacker-controlled"},
            {"X-Workroom-Future-Claim": "attacker-controlled"},
        ],
    )
    async def test_rejects_manually_supplied_auth_or_routing_headers(
        self, monkeypatch, headers
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")

        with pytest.raises(ValueError, match="manages authentication and routing"):
            await platform_request(
                _request({"X-Workroom-Id": "wrk-1"}),
                "GET",
                "/api/catalog/datasets/",
                headers=headers,
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "keyword", ["auth", "cookies", "extensions", "follow_redirects"]
    )
    async def test_rejects_httpx_auth_override_kwargs(self, monkeypatch, keyword):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")

        with pytest.raises(ValueError, match=keyword):
            await platform_request(
                _request(),
                "GET",
                "/api/catalog/datasets/",
                **{keyword: object()},
            )

    @pytest.mark.asyncio
    async def test_disables_environment_proxy_inheritance(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")
        response = httpx.Response(
            200,
            request=httpx.Request(
                "GET", "http://core-api:7777/api/catalog/datasets/"
            ),
        )

        with patch(
            "kamiwaza_extensions_lib.platform.httpx.AsyncClient"
        ) as client_cls:
            client = AsyncMock()
            client.request = AsyncMock(return_value=response)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client_cls.return_value = client

            await platform_request(
                _request({"Cookie": "session=opaque"}),
                "GET",
                "/api/catalog/datasets/",
                timeout=12.5,
            )

        assert client_cls.call_args.kwargs["trust_env"] is False
        assert client_cls.call_args.kwargs["timeout"] == 12.5

    @pytest.mark.asyncio
    async def test_missing_container_base_is_typed_context_error(self, monkeypatch):
        monkeypatch.delenv("KAMIWAZA_API_URL", raising=False)
        monkeypatch.delenv("KAMIWAZA_PUBLIC_API_URL", raising=False)

        with pytest.raises(UnexpectedContextError, match="KAMIWAZA_API_URL"):
            await platform_request(
                _request(),
                "GET",
                "/api/catalog/datasets/",
            )

    @pytest.mark.asyncio
    async def test_public_url_is_not_used_for_request_bound_credentials(
        self, monkeypatch
    ):
        monkeypatch.delenv("KAMIWAZA_API_URL", raising=False)
        monkeypatch.setenv(
            "KAMIWAZA_PUBLIC_API_URL", "https://browser.example.test/api"
        )

        with pytest.raises(UnexpectedContextError, match="KAMIWAZA_API_URL"):
            await platform_request(
                _request({"Cookie": "session=opaque"}),
                "GET",
                "/api/catalog/datasets/",
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "base",
        [
            "ftp://core-api:7777/api",
            "core-api:7777/api",
            "http://core-api:7777/api?tenant=acme",
            "http://core-api:7777/api#fragment",
            "http://core-api:bad/api",
            "http://[::1/api",
            "http://:80/api",
            "http://core-api:99999/api",
            "http://user:secret@core-api:7777/api",
        ],
    )
    async def test_invalid_container_base_is_typed_context_error(
        self, monkeypatch, base
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", base)

        with pytest.raises(UnexpectedContextError, match="not a valid"):
            await platform_request(
                _request(),
                "GET",
                "/api/catalog/datasets/",
            )

    @pytest.mark.asyncio
    async def test_wraps_transport_failures_in_typed_outage(
        self, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))

        with pytest.raises(
            PlatformOutageError,
            match="platform request failed",
        ) as exc_info:
            await platform_request(
                _request(),
                "GET",
                "/api/catalog/datasets/",
            )

        assert isinstance(exc_info.value.__cause__, httpx.ConnectError)

    @pytest.mark.asyncio
    async def test_maps_invalid_httpx_url_to_caller_error(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")

        with patch(
            "kamiwaza_extensions_lib.platform.httpx.AsyncClient"
        ) as client_cls:
            client = AsyncMock()
            client.request = AsyncMock(side_effect=httpx.InvalidURL("invalid URL"))
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client_cls.return_value = client

            with pytest.raises(ValueError, match="invalid platform path") as exc_info:
                await platform_request(
                    _request(),
                    "GET",
                    "/api/catalog/datasets/",
                )

        assert isinstance(exc_info.value.__cause__, httpx.InvalidURL)

    @pytest.mark.asyncio
    async def test_forwards_non_get_method_and_json_body(
        self, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")
        url = "http://core-api:7777/api/catalog/datasets/"
        httpx_mock.add_response(method="POST", url=url, status_code=201)

        response = await platform_request(
            _request(),
            "POST",
            "/api/catalog/datasets/",
            json={"name": "example"},
        )

        outbound = httpx_mock.get_request()
        assert response.status_code == 201
        assert outbound is not None
        assert outbound.method == "POST"
        assert outbound.read() == b'{"name":"example"}'

    @pytest.mark.asyncio
    async def test_returns_platform_error_response_unchanged(
        self, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://core-api:7777/api")
        httpx_mock.add_response(
            method="GET",
            url="http://core-api:7777/api/catalog/datasets/",
            status_code=403,
            json={"detail": "not allowed"},
        )

        response = await platform_request(
            _request(),
            "GET",
            "/api/catalog/datasets/",
        )

        assert response.status_code == 403
        assert response.json() == {"detail": "not allowed"}
