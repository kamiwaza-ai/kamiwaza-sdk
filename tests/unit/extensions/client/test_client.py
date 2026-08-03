"""Tests for public client API."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.client.client import get_client, get_resource
from kamiwaza_extensions.client.config import (
    AuthConfig,
    Config,
    DefaultsConfig,
    R2Config,
)


@pytest.fixture
def mock_config() -> Config:
    """Config with static credentials for testing."""
    return Config(
        r2=R2Config(endpoint_url="https://test.r2.cloudflarestorage.com"),
        auth=AuthConfig(token_cache_path="/tmp/test-token.json"),
        defaults=DefaultsConfig(region="auto"),
    )


def test_get_client_with_static_creds(mock_config: Config) -> None:
    """get_client returns boto3 client with static credentials."""
    client = get_client(
        access_key_id="test-key",
        secret_access_key="test-secret",
        config=mock_config,
    )
    assert client is not None
    assert hasattr(client, "list_objects_v2")
    assert hasattr(client, "get_object")
    assert hasattr(client, "put_object")
    # Verify it's a real boto3 client
    assert client.meta.service_model.service_name == "s3"


def test_get_client_with_env_creds(
    monkeypatch: pytest.MonkeyPatch, mock_config: Config
) -> None:
    """get_client uses env vars when no explicit creds."""
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "env-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "env-secret")

    client = get_client(config=mock_config)
    assert client is not None
    assert client.meta.service_model.service_name == "s3"


def test_get_resource_with_static_creds(mock_config: Config) -> None:
    """get_resource returns boto3 resource with static credentials."""
    resource = get_resource(
        access_key_id="test-key",
        secret_access_key="test-secret",
        config=mock_config,
    )
    assert resource is not None
    assert hasattr(resource, "Bucket")
    assert resource.meta.service_name == "s3"


def test_get_client_passes_endpoint(mock_config: Config) -> None:
    """Endpoint URL is passed to boto3 client."""
    client = get_client(
        access_key_id="k",
        secret_access_key="s",
        config=mock_config,
    )
    assert client.meta.endpoint_url is not None
    assert "r2.cloudflarestorage.com" in str(client.meta.endpoint_url)


@patch("kamiwaza_extensions.client.client.Boto3Adapter")
def test_public_api_forwards_explicit_auth_mode(mock_adapter) -> None:
    """The public facade exposes deterministic auth selection."""
    get_client(bucket="catalog", auth_mode="sso")

    mock_adapter.assert_called_once_with(
        config=None,
        endpoint_url=None,
        region_name=None,
        access_key_id=None,
        secret_access_key=None,
        session_token=None,
        bucket="catalog",
        auth_mode="sso",
    )


def test_sso_without_bucket_uses_multi_bucket_mode(mock_config: Config) -> None:
    """The canonical client supports lazy per-bucket routing."""
    client = get_client(config=mock_config, auth_mode="sso")

    assert client.__class__.__name__ == "_MultiBucketClient"


@patch("kamiwaza_extensions.client.adapters.boto3._refreshable_session")
def test_sso_with_bucket_returns_refreshable_boto3_client(
    mock_refreshable_session, mock_config: Config
) -> None:
    expected = mock_refreshable_session.return_value.client.return_value

    actual = get_client(
        config=mock_config,
        bucket="catalog-dev",
        auth_mode="sso",
    )

    assert actual is expected
    mock_refreshable_session.return_value.client.assert_called_once_with(
        service_name="s3",
        region_name="auto",
        endpoint_url="https://test.r2.cloudflarestorage.com",
    )


@patch("kamiwaza_extensions.client.adapters.boto3._refreshable_session")
def test_sso_resource_uses_refreshable_session(
    mock_refreshable_session, mock_config: Config
) -> None:
    expected = mock_refreshable_session.return_value.resource.return_value

    actual = get_resource(
        config=mock_config,
        bucket="catalog-dev",
        auth_mode="sso",
    )

    assert actual is expected
    mock_refreshable_session.return_value.resource.assert_called_once_with(
        service_name="s3",
        region_name="auto",
        endpoint_url="https://test.r2.cloudflarestorage.com",
    )


def test_multi_bucket_client_caches_one_refreshable_client_per_bucket() -> None:
    from kamiwaza_extensions.client.adapters.boto3 import _MultiBucketClient

    provider = MagicMock()
    first_session = MagicMock()
    second_session = MagicMock()
    first_client = first_session.client.return_value
    second_client = second_session.client.return_value
    first_client.get_object.return_value = {"Body": b"first"}
    second_client.put_object.return_value = {"ETag": "second"}

    with patch(
        "kamiwaza_extensions.client.adapters.boto3._refreshable_session",
        side_effect=[first_session, second_session],
    ) as mock_session:
        client = _MultiBucketClient(provider, "https://objects.example", "auto")
        assert client.get_object(Bucket="first", Key="entry") == {"Body": b"first"}
        assert client.get_object(Bucket="first", Key="again") == {"Body": b"first"}
        assert client.put_object(Bucket="second", Key="entry", Body=b"x") == {
            "ETag": "second"
        }

    assert mock_session.call_count == 2
    mock_session.assert_any_call(provider, "first")
    mock_session.assert_any_call(provider, "second")
