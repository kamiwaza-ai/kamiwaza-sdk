"""Tests for CredentialProvider (extensibility)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kamiwaza_extensions.client.auth.credentials import Credentials
from kamiwaza_extensions.client.auth.provider import CredentialProvider, _parse_expiry
from kamiwaza_extensions.client.config import (
    AuthConfig,
    Config,
    DefaultsConfig,
    R2Config,
)
from kamiwaza_extensions.client.options import ExplicitCredentials


@pytest.fixture
def minimal_config() -> Config:
    """Minimal config for testing."""
    return Config(
        r2=R2Config(
            endpoint_url="https://test.r2.cloudflarestorage.com",
            broker_url="https://broker.example.com",
        ),
        auth=AuthConfig(token_cache_path="/tmp/test-token.json"),
        defaults=DefaultsConfig(region="auto"),
    )


def test_credential_provider_static_returns_credentials(minimal_config: Config) -> None:
    """CredentialProvider returns Credentials for static creds."""
    provider = CredentialProvider(
        config=minimal_config,
        explicit=ExplicitCredentials(
            access_key_id="key", secret_access_key="secret"
        ),
    )
    creds = provider.get_credentials()
    assert isinstance(creds, Credentials)
    assert creds.access_key_id == "key"
    assert creds.secret_access_key == "secret"
    assert creds.expiry is None


def test_credential_provider_env_creds(
    monkeypatch: pytest.MonkeyPatch, minimal_config: Config
) -> None:
    """CredentialProvider uses env vars when no explicit creds."""
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "env-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "env-secret")

    provider = CredentialProvider(config=minimal_config)
    creds = provider.get_credentials()
    assert creds.access_key_id == "env-key"
    assert creds.secret_access_key == "env-secret"


def test_boto3_adapter_get_credentials(minimal_config: Config) -> None:
    """Boto3Adapter.get_credentials() returns raw Credentials for extensibility."""
    from kamiwaza_extensions.client.adapters.boto3 import Boto3Adapter

    adapter = Boto3Adapter(
        config=minimal_config,
        explicit=ExplicitCredentials(access_key_id="k", secret_access_key="s"),
    )
    creds = adapter.get_credentials()
    assert isinstance(creds, Credentials)
    assert creds.access_key_id == "k"


def test_forced_sso_ignores_ambient_static_credentials(
    monkeypatch: pytest.MonkeyPatch, minimal_config: Config
) -> None:
    """Explicit SSO mode always obtains bucket-scoped broker credentials."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret")
    monkeypatch.setattr(
        "kamiwaza_extensions.client.auth.provider.get_cloudflare_token",
        lambda config, bucket=None: "token",
    )
    monkeypatch.setattr(
        "kamiwaza_extensions.client.auth.provider.exchange_token_for_credentials",
        lambda token, broker_url, bucket=None: {
            "access_key_id": "broker-key",
            "secret_access_key": "broker-secret",
            "session_token": "broker-session",
            "expiration": "2035-01-01T00:00:00Z",
        },
    )
    provider = CredentialProvider(
        config=minimal_config,
        bucket="catalog-dev",
        auth_mode="sso",
    )

    creds = provider.get_credentials()

    assert creds.access_key_id == "broker-key"
    assert creds.session_token == "broker-session"


def test_refreshable_credentials_return_botocore_metadata(
    monkeypatch: pytest.MonkeyPatch, minimal_config: Config
) -> None:
    monkeypatch.setattr(
        "kamiwaza_extensions.client.auth.provider.get_cloudflare_token",
        lambda config, bucket=None: "token",
    )
    monkeypatch.setattr(
        "kamiwaza_extensions.client.auth.provider.exchange_token_for_credentials",
        lambda token, broker_url, bucket=None: {
            "access_key_id": "broker-key",
            "secret_access_key": "broker-secret",
            "expiration": "2035-01-01T00:00:00Z",
        },
    )
    provider = CredentialProvider(config=minimal_config, auth_mode="sso")

    refreshable = provider.to_refreshable_credentials()
    refreshed = refreshable._refresh_using()

    assert refreshable._expiry_time == datetime(2035, 1, 1, tzinfo=timezone.utc)
    assert refreshed["expiry_time"] == "2035-01-01T00:00:00+00:00"


def test_static_credentials_are_not_refreshable(minimal_config: Config) -> None:
    provider = CredentialProvider(
        config=minimal_config,
        explicit=ExplicitCredentials(
            access_key_id="key", secret_access_key="secret"
        ),
        auth_mode="static",
    )

    with pytest.raises(RuntimeError, match="Static credentials"):
        provider.to_refreshable_credentials()


def test_parse_expiry_normalizes_naive_timestamp_to_utc() -> None:
    assert _parse_expiry("2035-01-01T00:00:00") == datetime(
        2035, 1, 1, tzinfo=timezone.utc
    )


def test_provider_rejects_malformed_broker_expiration(
    monkeypatch: pytest.MonkeyPatch, minimal_config: Config
) -> None:
    monkeypatch.setattr(
        "kamiwaza_extensions.client.auth.provider.get_cloudflare_token",
        lambda config, bucket=None: "token",
    )
    monkeypatch.setattr(
        "kamiwaza_extensions.client.auth.provider.exchange_token_for_credentials",
        lambda token, broker_url, bucket=None: {
            "access_key_id": "broker-key",
            "secret_access_key": "broker-secret",
            "expiration": "not-a-timestamp",
        },
    )

    provider = CredentialProvider(config=minimal_config, auth_mode="sso")

    with pytest.raises(RuntimeError, match="invalid expiration"):
        provider.get_credentials()
