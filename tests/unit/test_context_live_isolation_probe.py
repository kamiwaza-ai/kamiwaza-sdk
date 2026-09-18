"""Unit coverage for the T14 cross-workroom isolation probe (ENG-12477).

The live T14 test proves an indexed document is not visible from another
workroom. Two behaviours decide whether that proof is real:

* the probe must run against a foreign room that actually has a VectorDB, so a
  miss means isolation rather than an empty room; and
* the probe must stay strict, so a refusal that never reached backend
  resolution fails instead of passing.

Both get unit coverage here rather than only on a live deployment.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

import tests.integration.test_context_live as context_live
from kamiwaza_sdk.exceptions import APIError
from tests.integration.test_context_live import (
    _assert_foreign_search_misses,
    _wait_for_workroom_vectordb,
)

pytestmark = pytest.mark.unit


def _search_raising(error: APIError) -> Callable[[], dict[str, Any]]:
    def search() -> dict[str, Any]:
        raise error

    return search


def _unprovisioned_error() -> APIError:
    return APIError(
        'API request failed with status 503: {"code":"vectordb_instance_not_found"}',
        status_code=503,
        response_data={
            "code": "vectordb_instance_not_found",
            "message": "no VectorDB instance is provisioned for this workroom",
            "retry_after_seconds": 30,
        },
    )


# --- the probe stays strict -------------------------------------------------


def test_foreign_search_rejects_an_unprovisioned_backend() -> None:
    """After waiting for provisioning, this 503 is a regression, not a pass."""
    with pytest.raises(AssertionError):
        _assert_foreign_search_misses(_search_raising(_unprovisioned_error()), "t14probe")


def test_foreign_search_tolerates_denial() -> None:
    _assert_foreign_search_misses(
        _search_raising(APIError("forbidden", status_code=403)), "t14probe"
    )


def test_foreign_search_rejects_a_leaked_document() -> None:
    def search() -> dict[str, Any]:
        return {"results": [{"content": "The verification phrase is t14probe."}]}

    with pytest.raises(AssertionError):
        _assert_foreign_search_misses(search, "t14probe")


def test_foreign_search_accepts_results_without_the_needle() -> None:
    def search() -> dict[str, Any]:
        return {"results": [{"content": "an unrelated document"}]}

    _assert_foreign_search_misses(search, "t14probe")


# --- waiting for the room's auto-provisioned backend ------------------------


def test_wait_for_workroom_vectordb_returns_the_provisioned_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready: list[tuple[str, str]] = []
    service = SimpleNamespace(list_vectordbs=lambda *, workroom_id: [{"id": "vdb-1"}])
    monkeypatch.setattr(
        context_live,
        "_wait_for_vectordb_ready",
        lambda _service, vectordb_id, *, workroom_id: ready.append(
            (vectordb_id, workroom_id)
        ),
    )

    assert _wait_for_workroom_vectordb(service, "room-1") == "vdb-1"
    assert ready == [("vdb-1", "room-1")]


def test_wait_for_workroom_vectordb_polls_until_the_instance_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"count": 0}

    def list_vectordbs(*, workroom_id: str) -> list[dict[str, str]]:
        attempts["count"] += 1
        return [] if attempts["count"] == 1 else [{"id": "vdb-2"}]

    sleeps: list[float] = []
    service = SimpleNamespace(list_vectordbs=list_vectordbs)
    monkeypatch.setattr(context_live.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        context_live, "_wait_for_vectordb_ready", lambda *_args, **_kwargs: None
    )

    assert _wait_for_workroom_vectordb(service, "room-1") == "vdb-2"
    assert sleeps == [context_live._VECTORDB_PROVISION_POLL_SECONDS]


def test_wait_for_workroom_vectordb_fails_when_provisioning_never_lands() -> None:
    """A backend that never arrives is a product failure the smoke must report."""
    service = SimpleNamespace(list_vectordbs=lambda *, workroom_id: [])

    with pytest.raises(AssertionError, match="never had a VectorDB provisioned"):
        _wait_for_workroom_vectordb(service, "room-1", timeout_seconds=0.0)
