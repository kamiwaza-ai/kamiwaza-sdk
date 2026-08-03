"""Security and compatibility tests for broker-backed authentication."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from kamiwaza_extensions.client.auth import sso
from kamiwaza_extensions.client.config import AuthConfig, Config, R2Config


def _config(token_path: Path, **auth_overrides: object) -> Config:
    auth = AuthConfig(token_cache_path=token_path, **auth_overrides)
    return Config(
        r2=R2Config(broker_url="https://broker.example.com"),
        auth=auth,
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://broker.example.com",
        "http://localhost:8787",
        "http://127.0.0.1:8787",
    ],
)
def test_validate_broker_url_accepts_secure_or_loopback_origins(url: str) -> None:
    sso._validate_broker_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://broker.example.com",
        "file:///tmp/broker",
        "https://user:secret@broker.example.com",
    ],
)
def test_validate_broker_url_rejects_unsafe_origins(url: str) -> None:
    with pytest.raises(RuntimeError, match="broker URL|Broker URL"):
        sso._validate_broker_url(url)


@pytest.mark.parametrize(
    "name",
    ["callback_success.html", "callback_error.html", "callback_no_code.html"],
)
def test_callback_templates_are_package_resources(name: str) -> None:
    assert "<!DOCTYPE html>" in sso._load_template(name)


def test_save_token_is_atomic_and_private(tmp_path: Path) -> None:
    token_path = tmp_path / "auth" / "token.json"

    sso._save_token(token_path, "opaque-token", fallback_ttl_seconds=60)

    assert json.loads(token_path.read_text())["token"] == "opaque-token"
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700
    assert list(token_path.parent.glob(f".{token_path.name}.*")) == []


def test_non_interactive_mode_requires_cached_token(tmp_path: Path) -> None:
    config = _config(tmp_path / "token.json", non_interactive=True)

    with pytest.raises(RuntimeError, match="Interactive Cloudflare login is disabled"):
        sso.get_cloudflare_token(config)


def test_custom_token_directory_must_already_be_private(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    config = _config(shared / "token.json", non_interactive=True)

    with pytest.raises(RuntimeError, match="permissions are too broad"):
        sso.get_cloudflare_token(config)


def test_non_interactive_mode_accepts_cached_token(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"token": "cached"}))
    config = _config(token_path, non_interactive=True)

    assert sso.get_cloudflare_token(config) == "cached"
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_non_interactive_mode_ignores_non_object_cache(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text("[]")
    config = _config(token_path, non_interactive=True)

    with pytest.raises(RuntimeError, match="Interactive Cloudflare login is disabled"):
        sso.get_cloudflare_token(config)


def test_login_callback_binds_state_bucket_and_redirect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeServer:
        def __init__(self, address, handler_class):
            assert address == ("127.0.0.1", 0)
            self.server_address = ("127.0.0.1", 43123)
            self.handler_class = handler_class
            self.timeout = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def handle_request(self):
            handler = self.handler_class.__new__(self.handler_class)
            handler.path = (
                "/callback?state=fixed-state&bucket=catalog-dev&code=auth-code"
            )
            handler._send_response = lambda: None
            handler.do_GET()

    exchange = MagicMock(return_value="jwt-token")
    save = MagicMock()
    monkeypatch.setattr(sso, "HTTPServer", FakeServer)
    monkeypatch.setattr(sso.secrets, "token_urlsafe", lambda _size: "fixed-state")
    monkeypatch.setattr(sso, "_exchange_code_for_token", exchange)
    monkeypatch.setattr(sso, "_save_token", save)
    config = _config(tmp_path / "token.json", callback_timeout_seconds=5)

    token = sso._run_cloudflare_login(config, tmp_path / "token.json", "catalog-dev")

    assert token == "jwt-token"
    call = exchange.call_args.kwargs
    assert call["code"] == "auth-code"
    redirect = urlparse(call["redirect_uri"])
    assert redirect.hostname == "127.0.0.1"
    assert redirect.port == 43123
    assert parse_qs(redirect.query) == {
        "state": ["fixed-state"],
        "bucket": ["catalog-dev"],
    }
    save.assert_called_once_with(tmp_path / "token.json", "jwt-token", 900)


def test_login_callback_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeServer:
        server_address = ("127.0.0.1", 43123)

        def __init__(self, _address, _handler_class):
            self.timeout = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def handle_request(self):
            raise AssertionError("expired callback should not handle a request")

    monkeypatch.setattr(sso, "HTTPServer", FakeServer)
    monotonic = iter([0.0, 1.0])
    monkeypatch.setattr(sso.time, "monotonic", lambda: next(monotonic))
    config = _config(tmp_path / "token.json", callback_timeout_seconds=0.1)

    with pytest.raises(RuntimeError, match="callback timeout"):
        sso._run_cloudflare_login(config, tmp_path / "token.json")


def test_credential_exchange_requires_key_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock()
    response.json.return_value = {"access_key_id": "only-one"}
    monkeypatch.setattr(sso.requests, "post", MagicMock(return_value=response))

    with pytest.raises(RuntimeError, match="secret_access_key"):
        sso.exchange_token_for_credentials(
            "opaque-token", "https://broker.example.com", bucket="catalog"
        )


def test_credential_exchange_requires_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = MagicMock()
    response.json.return_value = []
    monkeypatch.setattr(sso.requests, "post", MagicMock(return_value=response))

    with pytest.raises(RuntimeError, match="JSON object"):
        sso.exchange_token_for_credentials(
            "opaque-token", "https://broker.example.com", bucket="catalog"
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"access_key_id": "key", "secret_access_key": "secret", "session_token": 1},
            "session_token",
        ),
        (
            {"access_key_id": "key", "secret_access_key": "secret", "expiration": 1},
            "expiration",
        ),
    ],
)
def test_credential_exchange_rejects_invalid_optional_fields(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, object], message: str
) -> None:
    response = MagicMock()
    response.json.return_value = payload
    monkeypatch.setattr(sso.requests, "post", MagicMock(return_value=response))

    with pytest.raises(RuntimeError, match=message):
        sso.exchange_token_for_credentials(
            "opaque-token", "https://broker.example.com", bucket="catalog"
        )
