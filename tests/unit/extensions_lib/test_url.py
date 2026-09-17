"""Tests for registered URL and callback transport helpers."""

from __future__ import annotations

import pytest

from kamiwaza_extensions_lib.config import AuthConfig
from kamiwaza_extensions_lib.url import (
    _strip_api_suffix,
    backend_runtime_base,
    callback_target,
    public_base_url,
)


class TestStripApiSuffix:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("", ""),
            ("https://example.com/api", "https://example.com"),
            ("https://example.com/api/", "https://example.com"),
            ("https://example.com/api///", "https://example.com"),
            ("https://example.com", "https://example.com"),
            ("https://example.com/", "https://example.com"),
            ("https://example.com/api/v1", "https://example.com/api/v1"),
            ("https://example.com/foo/api", "https://example.com/foo"),
            ("localhost:8000/api", "localhost:8000"),
        ],
    )
    def test_normalizes_known_inputs(self, url, expected):
        assert _strip_api_suffix(url) == expected


class TestPublicBaseUrl:
    def test_prefers_registered_public_url(self):
        config = AuthConfig(
            api_url="http://core-api:7777/api",
            public_api_url="https://public.example.test/gateway/api",
        )

        assert public_base_url(config) == "https://public.example.test/gateway"

    def test_uses_registered_origin_when_public_api_is_unset(self):
        config = AuthConfig(
            api_url="http://core-api:7777/api",
            origin="https://public.example.test",
        )

        assert public_base_url(config) == "https://public.example.test"

    def test_keeps_legacy_api_fallback(self):
        config = AuthConfig(api_url="http://core-api:7777/api")

        assert public_base_url(config) == "http://core-api:7777"

    def test_keeps_legacy_backend_base(self):
        config = AuthConfig(
            api_url="http://core-api:7777/api",
            public_api_url="https://public.example.test/api",
        )

        assert backend_runtime_base(config) == "http://core-api:7777"


class TestCallbackTarget:
    def test_changes_only_connection_origin(self):
        target = callback_target(
            "https://public.example.test:8443/gateway/runtime/models/dep-1/v1",
            "http://platform-gateway.platform.svc.cluster.local:8080",
        )

        assert (
            target.url == "http://platform-gateway.platform.svc.cluster.local:8080"
            "/gateway/runtime/models/dep-1/v1"
        )
        assert target.host == "public.example.test:8443"
        assert target.server_name == "public.example.test"

    def test_uses_registered_origin_when_transport_is_unset(self):
        target = callback_target("https://public.example.test/api")

        assert target.url == "https://public.example.test/api"
        assert target.host == "public.example.test"
        assert target.server_name == "public.example.test"

    @pytest.mark.parametrize(
        "transport",
        [
            "ftp://gateway.internal",
            "gateway.internal",
            "http://gateway.internal/prefix",
            "http://gateway.internal?tenant=acme",
            "http://user:secret@gateway.internal",
        ],
    )
    def test_rejects_non_origin_transport(self, transport):
        with pytest.raises(ValueError, match="invalid HTTP URL"):
            callback_target("https://public.example.test/api", transport)
