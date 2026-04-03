"""Tests for kamiwaza_extensions_lib.config."""

import pytest
from kamiwaza_extensions_lib.config import AuthConfig


@pytest.mark.unit
class TestAuthConfig:
    def test_from_env_all_set(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_API_URL", "http://api:7777/api")
        monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "https://cluster.test/api")
        monkeypatch.setenv("KAMIWAZA_ENDPOINT", "http://model:8080/v1")
        monkeypatch.setenv("KAMIWAZA_APP_URL", "https://cluster.test/runtime/apps/my-app")
        monkeypatch.setenv("KAMIWAZA_APP_PATH", "/runtime/apps/my-app")
        monkeypatch.setenv("KAMIWAZA_APP_NAME", "my-app")
        monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
        monkeypatch.setenv("KAMIWAZA_ORIGIN", "https://cluster.test")
        monkeypatch.setenv("KAMIWAZA_API_KEY", "pat-abc123")
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "true")

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

    def test_from_env_defaults(self, monkeypatch):
        # Clear any KAMIWAZA_ vars that might be set
        for key in list(monkeypatch._env_patches if hasattr(monkeypatch, '_env_patches') else []):
            pass
        monkeypatch.delenv("KAMIWAZA_API_URL", raising=False)
        monkeypatch.delenv("KAMIWAZA_PUBLIC_API_URL", raising=False)
        monkeypatch.delenv("KAMIWAZA_ENDPOINT", raising=False)
        monkeypatch.delenv("KAMIWAZA_MODEL_URL", raising=False)
        monkeypatch.delenv("KAMIWAZA_APP_URL", raising=False)
        monkeypatch.delenv("KAMIWAZA_APP_PATH", raising=False)
        monkeypatch.delenv("KAMIWAZA_APP_NAME", raising=False)
        monkeypatch.delenv("KAMIWAZA_USE_AUTH", raising=False)
        monkeypatch.delenv("KAMIWAZA_ORIGIN", raising=False)
        monkeypatch.delenv("KAMIWAZA_API_KEY", raising=False)
        monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)

        config = AuthConfig.from_env()

        assert config.api_url == ""
        assert config.openai_base == ""
        assert config.use_auth is True  # secure default
        assert config.api_key == ""
        assert config.verify_ssl is True

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
