from __future__ import annotations

import time

import pytest

from kamiwaza_sdk.token_store import FileTokenStore, StoredToken

pytestmark = pytest.mark.unit


def test_file_token_store_round_trip(tmp_path):
    store = FileTokenStore(tmp_path / "token.json")
    token = StoredToken(access_token="abc", refresh_token="ref", expires_at=time.time() + 60)

    store.save(token)
    loaded = store.load()

    assert loaded == token
    assert loaded and not loaded.is_expired


def test_file_token_store_handles_missing(tmp_path):
    store = FileTokenStore(tmp_path / "missing.json")
    assert store.load() is None
    store.clear()  # no crash
