from types import SimpleNamespace
from uuid import uuid4

import pytest

from kamiwaza_sdk.exceptions import APIError
from tests.integration.test_context_live import (
    _is_workroom_binding_unavailable,
    session_workroom,
)

pytestmark = pytest.mark.unit


def test_workroom_binding_unavailable_detects_structured_503() -> None:
    error = APIError(
        "binding unavailable",
        status_code=503,
        response_data={
            "detail": {
                "error": {
                    "class": "binding_unavailable",
                    "kind": "misconfiguration",
                }
            }
        },
    )

    assert _is_workroom_binding_unavailable(error) is True


def test_workroom_binding_unavailable_detects_legacy_503_detail() -> None:
    error = APIError(
        "binding unavailable",
        status_code=503,
        response_data={"detail": "workroom_binding_unavailable"},
    )

    assert _is_workroom_binding_unavailable(error) is True


def test_workroom_binding_unavailable_detects_reason_503_detail() -> None:
    error = APIError(
        "binding unavailable",
        status_code=503,
        response_data={"detail": {"reason": "binding_unavailable"}},
    )

    assert _is_workroom_binding_unavailable(error) is True


def test_workroom_binding_unavailable_rejects_other_enter_failures() -> None:
    forbidden = APIError(
        "forbidden",
        status_code=403,
        response_data={"detail": "workroom_access_denied"},
    )
    missing = APIError(
        "missing",
        status_code=404,
        response_data={"detail": "Workroom not found"},
    )
    generic_unavailable = APIError(
        "unavailable",
        status_code=503,
        response_data={"detail": "workroom_access_unavailable"},
    )

    assert _is_workroom_binding_unavailable(forbidden) is False
    assert _is_workroom_binding_unavailable(missing) is False
    assert _is_workroom_binding_unavailable(generic_unavailable) is False


@pytest.mark.parametrize(
    ("status_code", "response_data"),
    [
        (500, {"detail": {"error": {"class": "binding_unavailable"}}}),
        (503, None),
        (503, "gateway unavailable"),
        (503, {"detail": {}}),
        (503, {"detail": {"error": {"class": "unknown"}}}),
        (503, {"detail": {"error": {"class": ["binding_unavailable"]}}}),
    ],
)
def test_workroom_binding_unavailable_rejects_malformed_payloads(
    status_code: int,
    response_data: object,
) -> None:
    error = APIError(
        "not a binding outage",
        status_code=status_code,
        response_data=response_data,
    )

    assert _is_workroom_binding_unavailable(error) is False


def test_session_workroom_skips_when_enter_binding_is_unavailable() -> None:
    workroom_id = str(uuid4())
    workrooms = SimpleNamespace(
        create=lambda *_args, **_kwargs: SimpleNamespace(id=workroom_id),
        enter=lambda _workroom_id: (_ for _ in ()).throw(
            APIError(
                "binding unavailable",
                status_code=503,
                response_data={
                    "detail": {
                        "error": {
                            "class": "binding_unavailable",
                        }
                    }
                },
            )
        ),
        leave=lambda: pytest.fail("leave should not run when enter did not bind"),
        delete=lambda _workroom_id: None,
    )
    context_service = SimpleNamespace(client=SimpleNamespace(workrooms=workrooms))

    generator = session_workroom.__wrapped__(context_service)
    with pytest.raises(pytest.skip.Exception):
        next(generator)


def test_session_workroom_leaves_when_enter_response_fails_assertion() -> None:
    workroom_id = str(uuid4())
    calls: list[str] = []
    workrooms = SimpleNamespace(
        create=lambda *_args, **_kwargs: SimpleNamespace(id=workroom_id),
        enter=lambda _workroom_id: SimpleNamespace(workroom_id=uuid4()),
        leave=lambda: calls.append("leave"),
        delete=lambda _workroom_id: calls.append("delete"),
    )
    context_service = SimpleNamespace(client=SimpleNamespace(workrooms=workrooms))

    generator = session_workroom.__wrapped__(context_service)
    with pytest.raises(AssertionError):
        next(generator)

    assert calls == ["leave", "delete"]
