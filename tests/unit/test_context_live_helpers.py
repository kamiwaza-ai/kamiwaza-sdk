from types import SimpleNamespace
from uuid import uuid4

import pytest

from kamiwaza_sdk.services.context import ContextService
import tests.integration.test_context_live as context_live
from tests.integration.test_context_live import (
    _assert_global_scope_if_exposed,
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
