"""Unit coverage for the T14 cross-workroom isolation probe (ENG-12477).

The live T14 test proves an indexed document is not visible from another
workroom. Two behaviours decide whether that proof is real:

* the foreign room must actually have a VectorDB, so a miss means isolation
  rather than an empty room that answered before resolving a backend; and
* the probe must stay strict, so a refusal that never reached backend
  resolution fails instead of passing.

Both get unit coverage here rather than only on a live deployment.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

import tests.integration.test_context_live as context_live
from kamiwaza_sdk.exceptions import APIError
from tests.integration.test_context_live import (
    _assert_foreign_search_misses,
    _create_foreign_workroom,
)

pytestmark = pytest.mark.unit


def _search_raising(error: APIError) -> Callable[[], dict[str, Any]]:
    def search() -> dict[str, Any]:
        raise error

    return search


# --- the probe stays strict -------------------------------------------------


def test_foreign_search_rejects_an_unprovisioned_backend() -> None:
    """The room is provisioned before probing, so this 503 is a regression."""
    error = APIError(
        'API request failed with status 503: {"code":"vectordb_instance_not_found"}',
        status_code=503,
        response_data={"code": "vectordb_instance_not_found"},
    )

    with pytest.raises(AssertionError):
        _assert_foreign_search_misses(_search_raising(error), "t14probe")


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


# --- the foreign room is provisioned before it is probed --------------------


def _fake_service(events: list[str]) -> SimpleNamespace:
    """A workrooms client that records its deletes into a shared event log."""
    workroom_id = uuid4()
    workrooms = SimpleNamespace(
        create=lambda _name, _type: SimpleNamespace(id=workroom_id),
        delete=events.append,
    )
    return SimpleNamespace(client=SimpleNamespace(workrooms=workrooms))


def test_create_foreign_workroom_provisions_its_own_vectordb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit provisioning holds on eager, lazy and disabled deployments."""
    created: list[dict[str, Any]] = []
    monkeypatch.setattr(
        context_live,
        "_create_temp_vectordb",
        lambda _service, *, prefix, workroom_id: created.append(
            {"created_vectordb_for": workroom_id, "prefix": prefix}
        )
        or "vdb-foreign",
    )

    cleanups: list[tuple[str, Callable[[], object]]] = []
    foreign_id = _create_foreign_workroom(_fake_service([]), cleanups)

    assert created == [
        {"created_vectordb_for": foreign_id, "prefix": "sdk-t14-other-vdb"}
    ]


def test_create_foreign_workroom_cleans_up_the_backend_before_the_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanups run in reverse, so the backend must be registered last."""
    monkeypatch.setattr(
        context_live,
        "_create_temp_vectordb",
        lambda _service, *, prefix, workroom_id: "vdb-foreign",
    )
    # One shared log, so the assertion pins the ORDER the two teardowns ran in
    # and not merely that each of them ran.
    events: list[str] = []
    monkeypatch.setattr(
        context_live,
        "_safe_delete_vectordb",
        lambda _service, vectordb_id, *, workroom_id: events.append(vectordb_id),
    )

    cleanups: list[tuple[str, Callable[[], object]]] = []
    foreign_id = _create_foreign_workroom(_fake_service(events), cleanups)
    context_live._run_cleanups(cleanups)

    assert events == ["vdb-foreign", foreign_id]
