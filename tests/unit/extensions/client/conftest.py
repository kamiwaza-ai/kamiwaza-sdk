"""Pytest fixtures for object-storage client tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from kamiwaza_extensions.client.config import (
    AuthConfig,
    Config,
    DefaultsConfig,
    R2Config,
)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Temporary config directory."""
    return tmp_path / "config"


@pytest.fixture
def static_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set static credentials in environment."""
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret-key")
    return {"access_key_id": "test-access-key", "secret_access_key": "test-secret-key"}


@pytest.fixture
def minimal_config() -> Config:
    """Minimal config for testing."""
    return Config(
        r2=R2Config(
            account_id="test-account-id",
            endpoint_url="https://test-account-id.r2.cloudflarestorage.com",
            broker_url="https://broker.example.com/credentials",
        ),
        auth=AuthConfig(token_cache_path="/tmp/object-storage-client-test/token.json"),
        defaults=DefaultsConfig(region="auto"),
    )
