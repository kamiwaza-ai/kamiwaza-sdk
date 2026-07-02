from __future__ import annotations

import pytest

from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.seeding.client import (
    build_client_from_env,
    scoped_client_for_workroom,
)

pytestmark = pytest.mark.unit


def _platform_client() -> KamiwazaClient:
    client = KamiwazaClient(base_url="https://example.test/api", api_key="global-pat")
    client.session.verify = False
    return client


def test_scoped_client_uses_local_workroom_scope_without_enter():
    client = _platform_client()
    client._workrooms = object()

    scoped = scoped_client_for_workroom(client, "wr-1")

    assert scoped is not client
    assert scoped.base_url == "https://example.test/api"
    assert scoped.authenticator is client.authenticator
    assert scoped._default_headers == {"X-Workroom-Id": "wr-1"}
    # TLS setting carries over from the parent.
    assert scoped.session.verify is False


def test_scoped_client_for_global_still_returns_scoped_client():
    client = _platform_client()

    scoped = scoped_client_for_workroom(client, "global")

    assert scoped is not client
    assert scoped._default_headers == {"X-Workroom-Id": "global"}


def test_build_client_from_env_requires_base_url(monkeypatch):
    monkeypatch.delenv("KAMIWAZA_BASE_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_BASE_URI", raising=False)

    with pytest.raises(SystemExit):
        build_client_from_env()


def test_build_client_from_env_uses_explicit_args():
    client = build_client_from_env(base_url="https://example.test/api", api_key="k")
    assert client.base_url == "https://example.test/api"
    assert client.authenticator.api_key == "k"
