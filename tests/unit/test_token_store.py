from __future__ import annotations

import time

import pytest

from kamiwaza_sdk.token_store import FileTokenStore, InMemoryTokenStore, StoredToken

pytestmark = pytest.mark.unit


def test_file_token_store_round_trip(tmp_path):
    store = FileTokenStore(tmp_path / "token.json")
    token = StoredToken(
        access_token="abc", refresh_token="ref", expires_at=time.time() + 60
    )

    store.save(token)
    loaded = store.load()

    assert loaded == token
    assert loaded and not loaded.is_expired


def test_file_token_store_handles_missing(tmp_path):
    store = FileTokenStore(tmp_path / "missing.json")
    assert store.load() is None
    store.clear()  # no crash


def test_in_memory_token_store_round_trip():
    store = InMemoryTokenStore()
    token = StoredToken(
        access_token="abc", refresh_token="ref", expires_at=time.time() + 60
    )

    assert store.load() is None
    store.save(token)
    assert store.load() == token

    store.clear()
    assert store.load() is None


def test_in_memory_token_store_instances_are_isolated():
    """ENG-5955: two instances must not share state, so per-persona stores
    in contract tests can keep distinct bearer tokens in the same process.
    """
    store_a = InMemoryTokenStore()
    store_b = InMemoryTokenStore()

    token_a = StoredToken(
        access_token="A", refresh_token=None, expires_at=time.time() + 60
    )
    token_b = StoredToken(
        access_token="B", refresh_token=None, expires_at=time.time() + 60
    )

    store_a.save(token_a)
    store_b.save(token_b)

    assert store_a.load() == token_a
    assert store_b.load() == token_b
    assert store_a.load() != store_b.load()
