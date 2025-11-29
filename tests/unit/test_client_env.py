from __future__ import annotations

import pytest

from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.authentication import ApiKeyAuthenticator


pytestmark = pytest.mark.unit


def _clear_base_env(monkeypatch):
    monkeypatch.delenv("KAMIWAZA_BASE_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_BASE_URI", raising=False)


def test_client_requires_base_url_when_no_env(monkeypatch):
    _clear_base_env(monkeypatch)
    with pytest.raises(ValueError):
        KamiwazaClient()


def test_client_uses_base_url_env(monkeypatch):
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://env.example/api")
    client = KamiwazaClient()
    assert client.base_url == "https://env.example/api"


def test_client_uses_base_uri_env(monkeypatch):
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URI", "https://uri.example/api")
    client = KamiwazaClient()
    assert client.base_url == "https://uri.example/api"


def test_client_uses_api_token_env(monkeypatch):
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://env.example/api")
    monkeypatch.delenv("KAMIWAZA_API_KEY", raising=False)
    monkeypatch.setenv("KAMIWAZA_API_TOKEN", "pat-from-env")
    client = KamiwazaClient()
    assert isinstance(client.authenticator, ApiKeyAuthenticator)
    assert client.authenticator.api_key == "pat-from-env"
