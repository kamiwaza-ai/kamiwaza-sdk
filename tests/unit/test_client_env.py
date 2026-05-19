from __future__ import annotations

import pytest

from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.authentication import ApiKeyAuthenticator


pytestmark = pytest.mark.unit


def _clear_base_env(monkeypatch):
    monkeypatch.delenv("KAMIWAZA_BASE_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_BASE_URI", raising=False)
    monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)


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


def test_client_disables_ssl_verification_for_falsey_env_values(monkeypatch):
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://env.example/api")
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", " no ")

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


def test_client_request_injects_session_verify_when_ssl_enabled(monkeypatch):
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://env.example/api")
    client = KamiwazaClient()
    calls: list[dict[str, object]] = []

    def _fake_request(method: str, url: str, **kwargs: object) -> _NoContentResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _NoContentResponse()

    monkeypatch.setattr(client.session, "request", _fake_request)

    client.get("/serving/deployments", expect_json=False)

    assert calls[0]["kwargs"]["verify"] is True


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


def test_client_request_preserves_explicit_verify_false(monkeypatch):
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://env.example/api")
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
    client = KamiwazaClient()
    calls: list[dict[str, object]] = []

    def _fake_request(method: str, url: str, **kwargs: object) -> _NoContentResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _NoContentResponse()

    monkeypatch.setattr(client.session, "request", _fake_request)

    client.get("/serving/deployments", expect_json=False, verify=False)

    assert calls[0]["kwargs"]["verify"] is False


def test_client_request_preserves_explicit_verify_none(monkeypatch):
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://env.example/api")
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
    client = KamiwazaClient()
    calls: list[dict[str, object]] = []

    def _fake_request(method: str, url: str, **kwargs: object) -> _NoContentResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _NoContentResponse()

    monkeypatch.setattr(client.session, "request", _fake_request)

    client.get("/serving/deployments", expect_json=False, verify=None)

    assert calls[0]["kwargs"]["verify"] is None


def test_client_request_respects_runtime_session_verify_override(monkeypatch):
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://env.example/api")
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
    client = KamiwazaClient()
    client.session.verify = "/tmp/custom-ca.pem"
    calls: list[dict[str, object]] = []

    def _fake_request(method: str, url: str, **kwargs: object) -> _NoContentResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _NoContentResponse()

    monkeypatch.setattr(client.session, "request", _fake_request)

    client.get("/serving/deployments", expect_json=False)

    assert calls[0]["kwargs"]["verify"] == "/tmp/custom-ca.pem"


def test_client_request_custom_ca_beats_env_bundle(monkeypatch):
    """When KAMIWAZA_VERIFY_SSL=false initially disabled verification but the
    caller later sets session.verify to a custom CA path, that path must be
    injected into every request – even when REQUESTS_CA_BUNDLE is set – so
    that requests' merge_environment_settings cannot override it with the
    env bundle."""
    _clear_base_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://env.example/api")
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")

    client = KamiwazaClient()
    # Caller re-enables verification with a custom CA bundle at runtime.
    client.session.verify = "/tmp/custom-ca.pem"

    calls: list[dict[str, object]] = []

    def _fake_request(method: str, url: str, **kwargs: object) -> _NoContentResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _NoContentResponse()

    monkeypatch.setattr(client.session, "request", _fake_request)

    client.get("/serving/deployments", expect_json=False)

    assert calls[0]["kwargs"]["verify"] == "/tmp/custom-ca.pem"
