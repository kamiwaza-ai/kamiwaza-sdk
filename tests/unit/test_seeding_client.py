from __future__ import annotations

from types import SimpleNamespace

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


def test_scoped_client_uses_workroom_enter_token():
    client = _platform_client()
    client._workrooms = SimpleNamespace(
        enter=lambda wid: SimpleNamespace(
            access_token="workroom-token", workroom_id=wid
        )
    )

    scoped = scoped_client_for_workroom(client, "wr-1")

    assert scoped is not client
    assert scoped.base_url == "https://example.test/api"
    # The scoped client authenticates with the workroom-scoped token.
    assert scoped.authenticator.api_key == "workroom-token"
    # TLS setting carries over from the parent.
    assert scoped.session.verify is False


def test_scoped_client_falls_back_when_no_token_reminted():
    client = _platform_client()
    client._workrooms = SimpleNamespace(
        enter=lambda wid: SimpleNamespace(access_token=None, workroom_id=wid)
    )

    scoped = scoped_client_for_workroom(client, "global")

    # No reminted token (e.g. Global workroom) -> reuse the original client.
    assert scoped is client


def test_build_client_from_env_requires_base_url(monkeypatch):
    monkeypatch.delenv("KAMIWAZA_BASE_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_BASE_URI", raising=False)

    with pytest.raises(SystemExit):
        build_client_from_env()


def test_build_client_from_env_uses_explicit_args():
    client = build_client_from_env(base_url="https://example.test/api", api_key="k")
    assert client.base_url == "https://example.test/api"
    assert client.authenticator.api_key == "k"
