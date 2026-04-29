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


class _NoContentResponse:
    status_code = 204
    text = ""
    headers = {"content-type": "application/json"}

    def json(self) -> object:
        raise ValueError("No JSON payload")


def test_client_disables_ssl_verification_from_env(monkeypatch):
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://env.example/api")
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")

    client = KamiwazaClient()

    assert client.session.verify is False


def test_client_request_forces_verify_false_when_ssl_disabled(monkeypatch):
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://env.example/api")
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")
    client = KamiwazaClient()
    calls: list[dict[str, object]] = []

    def _fake_request(method: str, url: str, **kwargs: object) -> _NoContentResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _NoContentResponse()

    monkeypatch.setattr(client.session, "request", _fake_request)

    response = client.get("/serving/deployments", expect_json=False)

    assert isinstance(response, _NoContentResponse)
    assert calls[0]["kwargs"]["verify"] is False


def test_client_request_preserves_explicit_verify_override(monkeypatch):
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://env.example/api")
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
    client = KamiwazaClient()
    calls: list[dict[str, object]] = []

    def _fake_request(method: str, url: str, **kwargs: object) -> _NoContentResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _NoContentResponse()

    monkeypatch.setattr(client.session, "request", _fake_request)

    client.get("/serving/deployments", expect_json=False, verify=True)

    assert calls[0]["kwargs"]["verify"] is True
