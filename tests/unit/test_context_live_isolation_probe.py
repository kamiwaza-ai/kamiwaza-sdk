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
    service = SimpleNamespace(
        list_vectordbs=lambda *, workroom_id: [
            {"id": "vdb-1", "workroom_id": workroom_id}
        ]
    )
    monkeypatch.setattr(
        context_live,
        "_wait_for_vectordb_ready",
        lambda _service, vectordb_id, *, workroom_id, timeout_seconds: ready.append(
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
        if attempts["count"] == 1:
            return []
        return [{"id": "vdb-2", "workroom_id": workroom_id}]

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


def test_wait_for_workroom_vectordb_ignores_an_instance_from_another_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Global or shared backend must not be mistaken for the room's own."""
    service = SimpleNamespace(
        list_vectordbs=lambda *, workroom_id: [
            {"id": "vdb-global", "workroom_id": "ffffffff-ffff-ffff-ffff-ffffffffffff"}
        ]
    )
    monkeypatch.setattr(context_live.time, "sleep", lambda _seconds: None)

    with pytest.raises(AssertionError, match="never had a VectorDB provisioned"):
        _wait_for_workroom_vectordb(service, "room-1", timeout_seconds=0.0)


def test_wait_for_workroom_vectordb_tolerates_a_transient_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Core is mid-provision here; one 5xx must not abort the live test."""
    attempts = {"count": 0}

    def list_vectordbs(*, workroom_id: str) -> list[dict[str, str]]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise APIError("gateway blip", status_code=502)
        return [{"id": "vdb-4", "workroom_id": workroom_id}]

    service = SimpleNamespace(list_vectordbs=list_vectordbs)
    monkeypatch.setattr(context_live.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        context_live, "_wait_for_vectordb_ready", lambda *_args, **_kwargs: None
    )

    assert _wait_for_workroom_vectordb(service, "room-1") == "vdb-4"


def test_wait_for_workroom_vectordb_reraises_an_api_error_past_the_deadline() -> None:
    """Past the deadline the blip is the real failure, and must not be hidden."""

    def list_vectordbs(*, workroom_id: str) -> list[dict[str, str]]:
        raise APIError("gateway down", status_code=502)

    service = SimpleNamespace(list_vectordbs=list_vectordbs)

    with pytest.raises(APIError):
        _wait_for_workroom_vectordb(service, "room-1", timeout_seconds=0.0)


def test_wait_for_workroom_vectordb_bounds_the_readiness_leg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The readiness wait inherits the remaining budget, not a fresh timer."""
    budgets: list[float] = []
    service = SimpleNamespace(
        list_vectordbs=lambda *, workroom_id: [
            {"id": "vdb-3", "workroom_id": workroom_id}
        ]
    )
    monkeypatch.setattr(
        context_live,
        "_wait_for_vectordb_ready",
        lambda _service, _vectordb_id, *, workroom_id, timeout_seconds: budgets.append(
            timeout_seconds
        ),
    )

    _wait_for_workroom_vectordb(service, "room-1", timeout_seconds=30.0)

    assert budgets
    assert budgets[0] <= 30.0


def test_wait_for_workroom_vectordb_defaults_to_core_startup_grace() -> None:
    """A shorter default would fail a slow but supported provision."""
    assert context_live._VECTORDB_STARTUP_GRACE_SECONDS == 600.0
