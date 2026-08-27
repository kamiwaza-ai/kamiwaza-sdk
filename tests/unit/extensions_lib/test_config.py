"""Tests for kamiwaza_extensions_lib.config."""

import json
import ssl
from pathlib import Path
from unittest.mock import patch

import pytest

from kamiwaza_extensions_lib.config import AuthConfig
from kamiwaza_extensions_lib.errors import UnexpectedContextError

REPO_ROOT = Path(__file__).resolve().parents[3]
ROUTING_VECTORS = json.loads(
    (
        REPO_ROOT / "docs" / "extensions" / "runtime-path" / "routing-vectors.json"
    ).read_text()
)["routing"]
AUTH_ROUTING_VECTORS = [
    vector for vector in ROUTING_VECTORS if not vector.get("expect_error")
]
AUTH_ERROR_ROUTING_VECTORS = [
    vector for vector in ROUTING_VECTORS if vector.get("expect_error")
]


@pytest.mark.unit
class TestAuthConfig:
    @pytest.mark.parametrize(
        "vector",
        AUTH_ROUTING_VECTORS,
        ids=[vector["name"] for vector in AUTH_ROUTING_VECTORS],
    )
    def test_app_url_follows_canonical_routing_vectors(self, monkeypatch, vector):
        for name in (
            "KAMIWAZA_ROUTING_MODE",
            "KAMIWAZA_APP_PATH",
            "KAMIWAZA_APP_PATH_URL",
            "KAMIWAZA_APP_URL",
            "KAMIWAZA_ORIGIN",
        ):
            monkeypatch.delenv(name, raising=False)
        for name, value in vector["env"].items():
            monkeypatch.setenv(name, value)

        config = AuthConfig.from_env()

        assert config.app_url == vector["expect"]["app_url"]
        assert config.app_path == vector["expect"]["app_path"]

    @pytest.mark.parametrize(
        "vector",
        AUTH_ERROR_ROUTING_VECTORS,
        ids=[vector["name"] for vector in AUTH_ERROR_ROUTING_VECTORS],
    )
    def test_invalid_canonical_routing_vectors_fail_closed_without_raising(
        self, monkeypatch, caplog, vector
    ):
        for name in (
            "KAMIWAZA_ROUTING_MODE",
            "KAMIWAZA_APP_PATH",
            "KAMIWAZA_APP_PATH_URL",
            "KAMIWAZA_APP_URL",
            "KAMIWAZA_ORIGIN",
        ):
            monkeypatch.delenv(name, raising=False)
        for name, value in vector["env"].items():
            monkeypatch.setenv(name, value)

        config = AuthConfig.from_env()

        assert config.app_url == ""
        assert config.app_path == ""
        assert "invalid runtime routing" in caplog.text

    def test_from_env_all_set(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://api:7777/api")
        monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "https://cluster.test/api")
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "http://model:8080/v1")
        monkeypatch.setenv(
            "KAMIWAZA_APP_URL", "https://cluster.test/runtime/apps/my-app"
        )
        monkeypatch.setenv("KAMIWAZA_APP_PATH", "/runtime/apps/my-app")
        monkeypatch.setenv("KAMIWAZA_APP_NAME", "my-app")
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        monkeypatch.setenv("KAMIWAZA_ORIGIN", "https://cluster.test")
        monkeypatch.setenv("KAMIWAZA_API_KEY", "pat-abc123")
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "true")
        monkeypatch.setenv(
            "KAMIWAZA_CA_BUNDLE",
            " /etc/kamiwaza/private-ca.pem\n",
        )

        config = AuthConfig.from_env()

        assert config.api_url == "http://api:7777/api"
        assert config.public_api_url == "https://cluster.test/api"
        assert config.openai_base == "http://model:8080/v1"
        assert config.app_url == "https://cluster.test/runtime/apps/my-app"
        assert config.app_path == "/runtime/apps/my-app"
        assert config.app_name == "my-app"
        assert config.use_auth is True
        assert config.origin == "https://cluster.test"
        assert config.api_key == "pat-abc123"
        assert config.verify_ssl is True
        assert config.ca_bundle == "/etc/kamiwaza/private-ca.pem"

    def test_from_env_defaults(self, monkeypatch):
        # Clear any KAMIWAZA_ vars that might be set
        for key in list(
            monkeypatch._env_patches if hasattr(monkeypatch, "_env_patches") else []
        ):
            pass
        monkeypatch.delenv("KAMIWAZA_API_URL", raising=False)
        monkeypatch.delenv("KAMIWAZA_PUBLIC_API_URL", raising=False)
        monkeypatch.delenv("KAMIWAZA_ENDPOINT", raising=False)
        monkeypatch.delenv("KAMIWAZA_MODEL_URL", raising=False)
        monkeypatch.delenv("KAMIWAZA_APP_URL", raising=False)
        monkeypatch.delenv("KAMIWAZA_APP_PATH_URL", raising=False)
        monkeypatch.delenv("KAMIWAZA_APP_PATH", raising=False)
        monkeypatch.delenv("KAMIWAZA_ROUTING_MODE", raising=False)
        monkeypatch.delenv("KAMIWAZA_APP_NAME", raising=False)
        monkeypatch.delenv("KAMIWAZA_USE_AUTH", raising=False)
        monkeypatch.delenv("KAMIWAZA_ORIGIN", raising=False)
        monkeypatch.delenv("KAMIWAZA_API_KEY", raising=False)
        monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
        monkeypatch.delenv("KAMIWAZA_CA_BUNDLE", raising=False)

        config = AuthConfig.from_env()

        assert config.api_url == ""
        assert config.openai_base == ""
        assert config.use_auth is True  # secure default
        assert config.api_key == ""
        assert config.verify_ssl is True
        assert config.ca_bundle == ""

    def test_path_url_precedes_legacy_app_url(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_ROUTING_MODE", "path")
        monkeypatch.setenv("KAMIWAZA_APP_PATH", "/runtime/apps/my-app")
        monkeypatch.setenv(
            "KAMIWAZA_APP_PATH_URL",
            "https://path.example/runtime/apps/my-app/",
        )
        monkeypatch.setenv("KAMIWAZA_APP_URL", "https://legacy.example/my-app")

        config = AuthConfig.from_env()

        assert config.app_url == "https://path.example/runtime/apps/my-app"
        assert config.app_path == "/runtime/apps/my-app"

    def test_path_mode_adds_app_path_to_legacy_app_url_origin(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_ROUTING_MODE", "path")
        monkeypatch.setenv("KAMIWAZA_APP_PATH", "/runtime/apps/my-app")
        monkeypatch.delenv("KAMIWAZA_APP_PATH_URL", raising=False)
        monkeypatch.setenv("KAMIWAZA_APP_URL", "https://legacy.example:8443/")

        config = AuthConfig.from_env()

        assert config.app_url == "https://legacy.example:8443/runtime/apps/my-app"

    @pytest.mark.parametrize("app_path", ["/", " /// ", "   "])
    def test_unset_mode_normalizes_emptyish_path_to_port_mode(
        self, monkeypatch, app_path
    ):
        monkeypatch.delenv("KAMIWAZA_ROUTING_MODE", raising=False)
        monkeypatch.setenv("KAMIWAZA_APP_PATH", app_path)
        monkeypatch.delenv("KAMIWAZA_APP_PATH_URL", raising=False)
        monkeypatch.setenv("KAMIWAZA_APP_URL", "https://public.example:8443/")

        config = AuthConfig.from_env()

        assert config.app_url == "https://public.example:8443"

    def test_invalid_routing_env_does_not_raise_on_request_config(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv("KAMIWAZA_ROUTING_MODE", "path")
        monkeypatch.setenv("KAMIWAZA_APP_PATH", "/runtime/../etc")
        monkeypatch.setenv("KAMIWAZA_APP_URL", "https://legacy.example/my-app")
        monkeypatch.setenv("KAMIWAZA_APP_PATH_URL", "https://path.example/bad")

        config = AuthConfig.from_env()

        assert config.app_url == ""
        assert config.app_path == ""
        assert "invalid runtime routing" in caplog.text

    def test_app_path_is_exposed_in_canonical_form(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_ROUTING_MODE", "path")
        monkeypatch.setenv("KAMIWAZA_APP_PATH", "runtime/apps/my-app/")

        config = AuthConfig.from_env()

        assert config.app_path == "/runtime/apps/my-app"

    def test_use_auth_false(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "false")
        config = AuthConfig.from_env()
        assert config.use_auth is False

    def test_use_auth_zero(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "0")
        config = AuthConfig.from_env()
        assert config.use_auth is False

    def test_use_auth_no(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "no")
        config = AuthConfig.from_env()
        assert config.use_auth is False

    def test_use_auth_False_uppercase(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "False")
        config = AuthConfig.from_env()
        assert config.use_auth is False

    def test_verify_ssl_false(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
        config = AuthConfig.from_env()
        assert config.verify_ssl is False

    def test_httpx_verify_uses_explicit_ca_bundle(self):
        config = AuthConfig(ca_bundle="/etc/kamiwaza/private-ca.pem")
        context = object()

        with patch(
            "kamiwaza_extensions_lib.config.ssl.create_default_context",
            return_value=context,
        ) as create_context:
            assert config.httpx_verify() is context

        create_context.assert_called_once_with(cafile="/etc/kamiwaza/private-ca.pem")

    def test_httpx_verify_false_overrides_ca_bundle(self):
        config = AuthConfig(
            verify_ssl=False,
            ca_bundle="/etc/kamiwaza/private-ca.pem",
        )

        assert config.httpx_verify() is False

    @pytest.mark.parametrize(
        "error",
        [
            FileNotFoundError("missing bundle"),
            PermissionError("unreadable bundle"),
            ssl.SSLError("malformed bundle"),
        ],
    )
    def test_httpx_verify_maps_invalid_ca_bundle_to_typed_context_error(self, error):
        config = AuthConfig(ca_bundle="/etc/kamiwaza/private-ca.pem")

        with (
            patch(
                "kamiwaza_extensions_lib.config.ssl.create_default_context",
                side_effect=error,
            ),
            pytest.raises(
                UnexpectedContextError,
                match="KAMIWAZA_CA_BUNDLE",
            ),
        ):
            config.httpx_verify()

    def test_openai_base_falls_back_to_model_url(self, monkeypatch):
        monkeypatch.delenv("KAMIWAZA_ENDPOINT", raising=False)
        monkeypatch.setenv("KAMIWAZA_MODEL_URL", "http://model:8080/v1")
        config = AuthConfig.from_env()
        assert config.openai_base == "http://model:8080/v1"

    def test_endpoint_takes_precedence_over_model_url(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "http://endpoint:8080/v1")
        monkeypatch.setenv("KAMIWAZA_MODEL_URL", "http://model:8080/v1")
        config = AuthConfig.from_env()
        assert config.openai_base == "http://endpoint:8080/v1"
