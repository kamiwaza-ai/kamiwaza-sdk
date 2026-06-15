from kamiwaza_sdk.exceptions import APIError
from tests.integration.test_context_live import _is_workroom_binding_unavailable


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
