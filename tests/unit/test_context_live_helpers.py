from types import SimpleNamespace
from uuid import uuid4

import pytest

from kamiwaza_sdk.services.context import ContextService
import tests.integration.test_context_live as context_live
from tests.integration.test_context_live import (
    _assert_global_scope_if_exposed,
    _wait_for_vectordb_ready,
    session_workroom,
)

pytestmark = pytest.mark.unit


def test_global_scope_assertion_uses_fixed_global_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_live, "DEFAULT_WORKROOM_ID", str(uuid4()))

    _assert_global_scope_if_exposed(
        {"workroom_id": ContextService.DEFAULT_WORKROOM_ID}
    )


def test_global_scope_assertion_skips_when_scope_not_exposed() -> None:
    _assert_global_scope_if_exposed({})


def test_global_scope_assertion_rejects_non_global_scope() -> None:
    with pytest.raises(AssertionError):
        _assert_global_scope_if_exposed({"workroom_id": str(uuid4())})


def test_session_workroom_uses_explicit_scope_without_entering() -> None:
    workroom_id = str(uuid4())
    calls: list[str] = []
    workrooms = SimpleNamespace(
        create=lambda *_args, **_kwargs: SimpleNamespace(id=workroom_id),
        enter=lambda _workroom_id: pytest.fail("session_workroom should not enter"),
        leave=lambda: pytest.fail("session_workroom should not leave"),
        delete=lambda _workroom_id: calls.append("delete"),
    )
    context_service = SimpleNamespace(client=SimpleNamespace(workrooms=workrooms))

    generator = session_workroom.__wrapped__(context_service)
    yielded = next(generator)
    assert yielded == workroom_id

    with pytest.raises(StopIteration):
        next(generator)

    assert calls == ["delete"]


def test_vectordb_wait_tolerates_transient_stopped_status(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses = iter(
        [
            {"status": "stopped", "endpoint": None},
            {"status": "provisioning", "endpoint": None},
            {"status": "running", "endpoint": "http://milvus.test"},
        ]
    )
    service = SimpleNamespace(get_vectordb=lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(context_live.time, "sleep", lambda _seconds: None)

    with caplog.at_level("INFO"):
        _wait_for_vectordb_ready(service, "vdb-1", poll_seconds=0)

    assert "stopped" in caplog.text
    assert "provisioning" in caplog.text
    assert "running" in caplog.text


def test_vectordb_wait_still_fails_immediately_on_failed_status() -> None:
    service = SimpleNamespace(
        get_vectordb=lambda *_args, **_kwargs: {
            "status": "failed",
            "endpoint": None,
        }
    )

    with pytest.raises(RuntimeError, match="non-recoverable status failed"):
        _wait_for_vectordb_ready(service, "vdb-1", poll_seconds=0)


def test_vectordb_wait_fails_after_stopped_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SimpleNamespace(
        get_vectordb=lambda *_args, **_kwargs: {
            "status": "stopped",
            "endpoint": None,
        }
    )
    timestamps = iter([0.0, 0.0, 31.0])
    monkeypatch.setattr(context_live.time, "monotonic", lambda: next(timestamps))
    monkeypatch.setattr(context_live.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="remained stopped"):
        _wait_for_vectordb_ready(service, "vdb-1", poll_seconds=0)
