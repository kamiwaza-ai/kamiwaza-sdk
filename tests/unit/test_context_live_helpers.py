from types import SimpleNamespace
from uuid import uuid4

import pytest

import tests.integration.test_context_live as context_live
from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.services.context import ContextService
from tests.integration.test_context_live import (
    _assert_global_scope_if_exposed,
    _create_temp_vectordb,
    _next_stopped_since,
    _wait_for_vectordb_ready,
    session_workroom,
)

pytestmark = pytest.mark.unit


def test_stopped_grace_window_resets_after_transient_recovery() -> None:
    stopped_since = _next_stopped_since("vdb-1", "stopped", None, 1.0)

    assert _next_stopped_since("vdb-1", "provisioning", stopped_since, 2.0) is None


def test_stopped_grace_window_preserves_first_stopped_timestamp() -> None:
    assert _next_stopped_since("vdb-1", "stopped", 1.0, 2.0) == 1.0


def test_global_scope_assertion_uses_fixed_global_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_live, "DEFAULT_WORKROOM_ID", str(uuid4()))

    _assert_global_scope_if_exposed({"workroom_id": ContextService.DEFAULT_WORKROOM_ID})


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
    timestamps = [0.0, 0.0, 31.0]
    monotonic_calls = 0

    def monotonic() -> float:
        nonlocal monotonic_calls
        value = timestamps[min(monotonic_calls, len(timestamps) - 1)]
        monotonic_calls += 1
        return value

    monkeypatch.setattr(context_live.time, "monotonic", monotonic)
    monkeypatch.setattr(context_live.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="remained stopped"):
        _wait_for_vectordb_ready(service, "vdb-1", poll_seconds=0)


def test_create_temp_vectordb_retries_transient_create_500(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    create_calls: list[str] = []
    sleeps: list[float] = []

    def create_vectordb(*, name: str, **_kwargs: object) -> dict[str, str]:
        create_calls.append(name)
        if len(create_calls) == 1:
            raise APIError("transient create failure", status_code=500)
        return {"id": "vdb-1"}

    service = SimpleNamespace(
        create_vectordb=create_vectordb,
        list_vectordbs=lambda **_kwargs: [],
    )
    monkeypatch.setattr(context_live.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        context_live, "_wait_for_vectordb_ready", lambda *_a, **_k: None
    )

    with caplog.at_level("WARNING"):
        vectordb_id = _create_temp_vectordb(service, prefix="sdk-retry")

    assert vectordb_id == "vdb-1"
    assert len(create_calls) == 2
    assert len(set(create_calls)) == 1
    assert sleeps == [2.0]
    assert "VectorDB create failed with HTTP 500" in caplog.text


def test_create_temp_vectordb_reconciles_ambiguous_create_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_name = "sdk-reconcile-abcdef12"
    create_calls: list[dict[str, object]] = []
    list_calls: list[dict[str, object]] = []
    wait_calls: list[tuple[str, str | None]] = []

    def create_vectordb(**kwargs: object) -> dict[str, str]:
        create_calls.append(kwargs)
        raise APIError("ambiguous create failure", status_code=500)

    def list_vectordbs(**kwargs: object) -> list[dict[str, object]]:
        list_calls.append(kwargs)
        return [
            {"id": "vdb-decoy", "name": "sdk-reconcile-abcdef99"},
            {"id": None, "name": expected_name},
            {"id": "vdb-reconciled", "name": expected_name},
        ]

    service = SimpleNamespace(
        create_vectordb=create_vectordb,
        list_vectordbs=list_vectordbs,
    )
    monkeypatch.setattr(
        context_live,
        "uuid4",
        lambda: SimpleNamespace(hex="abcdef1234567890"),
    )
    monkeypatch.setattr(
        context_live,
        "_wait_for_vectordb_ready",
        lambda _service, vectordb_id, *, workroom_id=None: wait_calls.append(
            (vectordb_id, workroom_id)
        ),
    )
    monkeypatch.setattr(
        context_live.time,
        "sleep",
        lambda _seconds: pytest.fail("reconciled creates should not sleep or retry"),
    )

    vectordb_id = _create_temp_vectordb(
        service,
        prefix="sdk-reconcile",
        workroom_id="workroom-1",
    )

    assert vectordb_id == "vdb-reconciled"
    assert create_calls == [
        {
            "name": expected_name,
            "engine": "milvus",
            "workroom_id": "workroom-1",
        }
    ]
    assert list_calls == [{"workroom_id": "workroom-1"}]
    assert wait_calls == [("vdb-reconciled", "workroom-1")]


def test_create_temp_vectordb_reconciles_statusless_create_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_name = "sdk-transport-abcdef12"
    service = SimpleNamespace(
        create_vectordb=lambda **_kwargs: (_ for _ in ()).throw(
            APIError("connection reset")
        ),
        list_vectordbs=lambda **_kwargs: [
            {"id": "vdb-reconciled", "name": expected_name}
        ],
    )
    monkeypatch.setattr(
        context_live,
        "uuid4",
        lambda: SimpleNamespace(hex="abcdef1234567890"),
    )
    monkeypatch.setattr(
        context_live, "_wait_for_vectordb_ready", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        context_live.time,
        "sleep",
        lambda _seconds: pytest.fail("reconciled creates should not sleep or retry"),
    )

    vectordb_id = _create_temp_vectordb(service, prefix="sdk-transport")

    assert vectordb_id == "vdb-reconciled"


@pytest.mark.parametrize("status_code", ["500", 500.0])
def test_create_temp_vectordb_propagates_non_integer_status(
    monkeypatch: pytest.MonkeyPatch,
    status_code: object,
) -> None:
    error = APIError(
        "invalid status type",
        status_code=status_code,  # type: ignore[arg-type]
    )
    create_calls = 0

    def create_vectordb(**_kwargs: object) -> dict[str, str]:
        nonlocal create_calls
        create_calls += 1
        raise error

    service = SimpleNamespace(
        create_vectordb=create_vectordb,
        list_vectordbs=lambda **_kwargs: pytest.fail(
            "non-integer statuses should not trigger reconciliation"
        ),
    )
    monkeypatch.setattr(
        context_live.time,
        "sleep",
        lambda _seconds: pytest.fail("non-integer statuses should not be retried"),
    )

    with pytest.raises(APIError) as exc_info:
        _create_temp_vectordb(service, prefix="sdk-invalid-status")

    assert exc_info.value is error
    assert create_calls == 1


def test_create_temp_vectordb_retries_when_reconciliation_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    create_calls = 0
    list_calls = 0
    sleeps: list[float] = []

    def create_vectordb(**_kwargs: object) -> dict[str, str]:
        nonlocal create_calls
        create_calls += 1
        if create_calls == 1:
            raise APIError("transient create failure", status_code=500)
        return {"id": "vdb-after-lookup-failure"}

    def list_vectordbs(**_kwargs: object) -> list[dict[str, str]]:
        nonlocal list_calls
        list_calls += 1
        raise APIError("transient list failure", status_code=503)

    service = SimpleNamespace(
        create_vectordb=create_vectordb,
        list_vectordbs=list_vectordbs,
    )
    monkeypatch.setattr(context_live.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        context_live, "_wait_for_vectordb_ready", lambda *_a, **_k: None
    )

    with caplog.at_level("WARNING"):
        vectordb_id = _create_temp_vectordb(service, prefix="sdk-list-retry")

    assert vectordb_id == "vdb-after-lookup-failure"
    assert create_calls == 2
    assert list_calls == 1
    assert sleeps == [2.0]
    assert "Unable to reconcile VectorDB" in caplog.text


def test_create_temp_vectordb_reconciles_duplicate_after_ambiguous_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_name = "sdk-duplicate-abcdef12"
    create_errors = iter(
        [
            APIError("ambiguous create failure", status_code=500),
            APIError("duplicate name", status_code=409),
        ]
    )
    create_calls = 0
    list_calls = 0
    sleeps: list[float] = []

    def create_vectordb(**_kwargs: object) -> dict[str, str]:
        nonlocal create_calls
        create_calls += 1
        raise next(create_errors)

    def list_vectordbs(**_kwargs: object) -> list[dict[str, str]]:
        nonlocal list_calls
        list_calls += 1
        if list_calls == 1:
            return []
        return [{"id": "vdb-reconciled", "name": expected_name}]

    service = SimpleNamespace(
        create_vectordb=create_vectordb,
        list_vectordbs=list_vectordbs,
    )
    monkeypatch.setattr(
        context_live,
        "uuid4",
        lambda: SimpleNamespace(hex="abcdef1234567890"),
    )
    monkeypatch.setattr(context_live.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        context_live, "_wait_for_vectordb_ready", lambda *_a, **_k: None
    )

    vectordb_id = _create_temp_vectordb(service, prefix="sdk-duplicate")

    assert vectordb_id == "vdb-reconciled"
    assert create_calls == 2
    assert list_calls == 2
    assert sleeps == [2.0]


def test_create_temp_vectordb_reconciles_late_duplicate_in_workroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_name = "sdk-late-abcdef12"
    create_errors = iter(
        [
            APIError("ambiguous create failure", status_code=500),
            APIError("duplicate name", status_code=409),
        ]
    )
    create_calls: list[dict[str, object]] = []
    list_calls: list[dict[str, object]] = []
    wait_calls: list[tuple[str, str | None]] = []
    sleeps: list[float] = []

    def create_vectordb(**kwargs: object) -> dict[str, str]:
        create_calls.append(kwargs)
        raise next(create_errors)

    def list_vectordbs(**kwargs: object) -> list[dict[str, str]]:
        list_calls.append(kwargs)
        if len(list_calls) < 3:
            return []
        return [{"id": "vdb-late-reconciled", "name": expected_name}]

    service = SimpleNamespace(
        create_vectordb=create_vectordb,
        list_vectordbs=list_vectordbs,
    )
    monkeypatch.setattr(
        context_live,
        "uuid4",
        lambda: SimpleNamespace(hex="abcdef1234567890"),
    )
    monkeypatch.setattr(context_live.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        context_live,
        "_wait_for_vectordb_ready",
        lambda _service, vectordb_id, *, workroom_id=None: wait_calls.append(
            (vectordb_id, workroom_id)
        ),
    )

    vectordb_id = _create_temp_vectordb(
        service,
        prefix="sdk-late",
        workroom_id="workroom-1",
    )

    expected_create = {
        "name": expected_name,
        "engine": "milvus",
        "workroom_id": "workroom-1",
    }
    assert vectordb_id == "vdb-late-reconciled"
    assert create_calls == [expected_create, expected_create]
    assert list_calls == [{"workroom_id": "workroom-1"}] * 3
    assert sleeps == [2.0, 2.0]
    assert wait_calls == [("vdb-late-reconciled", "workroom-1")]


def test_create_temp_vectordb_retries_duplicate_reconciliation_before_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ambiguous_error = APIError("ambiguous create failure", status_code=500)
    duplicate_error = APIError("duplicate name", status_code=409)
    create_errors = iter([ambiguous_error, duplicate_error])
    create_calls = 0
    list_calls = 0
    sleeps: list[float] = []

    def create_vectordb(**_kwargs: object) -> dict[str, str]:
        nonlocal create_calls
        create_calls += 1
        raise next(create_errors)

    def list_vectordbs(**_kwargs: object) -> list[dict[str, str]]:
        nonlocal list_calls
        list_calls += 1
        return []

    service = SimpleNamespace(
        create_vectordb=create_vectordb,
        list_vectordbs=list_vectordbs,
    )
    monkeypatch.setattr(context_live.time, "sleep", sleeps.append)

    with pytest.raises(APIError) as exc_info:
        _create_temp_vectordb(service, prefix="sdk-duplicate-miss")

    assert exc_info.value is duplicate_error
    assert exc_info.value.__cause__ is ambiguous_error
    assert create_calls == 2
    assert list_calls == 3
    assert sleeps == [2.0, 2.0]


def test_create_temp_vectordb_propagates_initial_duplicate_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = APIError("duplicate name", status_code=409)
    service = SimpleNamespace(
        create_vectordb=lambda **_kwargs: (_ for _ in ()).throw(error),
        list_vectordbs=lambda **_kwargs: pytest.fail(
            "an initial conflict should not trigger reconciliation"
        ),
    )
    monkeypatch.setattr(
        context_live.time,
        "sleep",
        lambda _seconds: pytest.fail("an initial conflict should not be retried"),
    )

    with pytest.raises(APIError) as exc_info:
        _create_temp_vectordb(service, prefix="sdk-duplicate")

    assert exc_info.value is error


def test_create_temp_vectordb_does_not_retry_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = APIError("invalid request", status_code=422)
    service = SimpleNamespace(
        create_vectordb=lambda **_kwargs: (_ for _ in ()).throw(error),
        list_vectordbs=lambda **_kwargs: pytest.fail(
            "client errors should not trigger reconciliation"
        ),
    )
    monkeypatch.setattr(
        context_live.time,
        "sleep",
        lambda _seconds: pytest.fail("client errors should not be retried"),
    )

    with pytest.raises(APIError) as exc_info:
        _create_temp_vectordb(service, prefix="sdk-invalid")

    assert exc_info.value is error


def test_create_temp_vectordb_propagates_persistent_create_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = APIError("persistent create failure", status_code=500)
    create_calls = 0
    sleeps: list[float] = []

    def create_vectordb(**_kwargs: object) -> dict[str, str]:
        nonlocal create_calls
        create_calls += 1
        raise error

    service = SimpleNamespace(
        create_vectordb=create_vectordb,
        list_vectordbs=lambda **_kwargs: [],
    )
    monkeypatch.setattr(context_live.time, "sleep", sleeps.append)

    with pytest.raises(APIError) as exc_info:
        _create_temp_vectordb(service, prefix="sdk-persistent")

    assert exc_info.value is error
    assert create_calls == 3
    assert sleeps == [2.0, 2.0]
