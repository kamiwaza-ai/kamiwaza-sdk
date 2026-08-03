"""Tests for config module."""

from __future__ import annotations

import pytest

from kamiwaza_extensions.client.config import (
    ENV_ACCOUNT_ID,
    ENV_AUTH_CALLBACK_TIMEOUT,
    ENV_AUTH_NON_INTERACTIVE,
    ENV_BROKER_URL,
    ENV_ENDPOINT_URL,
    ENV_KAMIWAZA_REGISTRY_ACCOUNT_ID,
    ENV_KAMIWAZA_REGISTRY_ENDPOINT,
    Config,
    R2Config,
)


def test_config_get_endpoint_url_from_account_id() -> None:
    """Endpoint URL is constructed from account_id when not set."""
    config = Config(r2=R2Config(account_id="abc123"))
    assert config.get_endpoint_url() == "https://abc123.r2.cloudflarestorage.com"


def test_config_get_endpoint_url_explicit() -> None:
    """Explicit endpoint_url is used when set."""
    config = Config(r2=R2Config(endpoint_url="https://custom.endpoint.com"))
    assert config.get_endpoint_url() == "https://custom.endpoint.com"


def test_config_get_endpoint_url_override() -> None:
    """Override takes precedence."""
    config = Config(r2=R2Config(endpoint_url="https://config.endpoint.com"))
    assert config.get_endpoint_url("https://override.com") == "https://override.com"


def test_config_get_endpoint_url_none_when_empty() -> None:
    """Returns None when no endpoint configured."""
    config = Config()
    assert config.get_endpoint_url() is None


def test_config_load_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables configure client."""
    monkeypatch.setenv(ENV_ACCOUNT_ID, "env-account")
    monkeypatch.setenv(ENV_ENDPOINT_URL, "https://env.endpoint.com")
    monkeypatch.setenv(ENV_BROKER_URL, "https://env-broker.com/creds")

    config = Config.load()
    assert config.r2.account_id == "env-account"
    assert config.r2.endpoint_url == "https://env.endpoint.com"
    assert config.r2.broker_url == "https://env-broker.com/creds"


def test_config_load_kamiwaza_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """KAMIWAZA_REGISTRY_ENDPOINT is used when R2_ENDPOINT_URL not set."""
    monkeypatch.delenv(ENV_ENDPOINT_URL, raising=False)
    monkeypatch.delenv(ENV_ACCOUNT_ID, raising=False)
    monkeypatch.delenv(ENV_KAMIWAZA_REGISTRY_ACCOUNT_ID, raising=False)
    monkeypatch.setenv(
        ENV_KAMIWAZA_REGISTRY_ENDPOINT, "https://kamiwaza.r2.cloudflarestorage.com"
    )
    config = Config.load()
    assert config.r2.endpoint_url == "https://kamiwaza.r2.cloudflarestorage.com"


def test_config_load_kamiwaza_account_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """KAMIWAZA_REGISTRY_ACCOUNT_ID constructs endpoint when others not set."""
    monkeypatch.delenv(ENV_ENDPOINT_URL, raising=False)
    monkeypatch.delenv(ENV_ACCOUNT_ID, raising=False)
    monkeypatch.delenv(ENV_KAMIWAZA_REGISTRY_ENDPOINT, raising=False)
    monkeypatch.setenv(ENV_KAMIWAZA_REGISTRY_ACCOUNT_ID, "deadbeef123")
    config = Config.load()
    assert config.r2.endpoint_url == "https://deadbeef123.r2.cloudflarestorage.com"


def test_config_load_auth_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interactive login controls are configurable for CLI and CI callers."""
    monkeypatch.setenv(ENV_AUTH_NON_INTERACTIVE, "true")
    monkeypatch.setenv(ENV_AUTH_CALLBACK_TIMEOUT, "12.5")

    config = Config.load()

    assert config.auth.non_interactive is True
    assert config.auth.callback_timeout_seconds == 12.5


@pytest.mark.parametrize("value", ["zero", "0", "-1"])
def test_config_rejects_invalid_callback_timeout(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ENV_AUTH_CALLBACK_TIMEOUT, value)

    with pytest.raises(ValueError, match=ENV_AUTH_CALLBACK_TIMEOUT):
        Config.load()
