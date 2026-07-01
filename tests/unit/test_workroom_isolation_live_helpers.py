from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from kamiwaza_sdk.exceptions import APIError, AuthorizationError
from tests.integration import test_workroom_isolation_live as isolation_live

pytestmark = pytest.mark.unit


@dataclass
class _FakeSdk:
    responses: list[Any]
    calls: list[tuple[str, dict[str, str]]]

    def get(self, endpoint: str, *, headers: dict[str, str]):
        self.calls.append((endpoint, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _forbidden() -> APIError:
    return APIError(
        "API request failed with status 403: ",
        status_code=403,
        response_text="",
        response_data=None,
    )


def test_self_access_connector_list_first_success_does_not_sleep_or_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workroom_id = "wr-a"
    sdk = _FakeSdk(responses=[{"items": [{"workroom_id": workroom_id}]}], calls=[])
    sleeps: list[float] = []
    monkeypatch.setattr(
        isolation_live,
        "time",
        SimpleNamespace(sleep=sleeps.append),
        raising=False,
    )

    result = isolation_live._list_connectors_self_access(sdk, workroom_id, label="A")

    assert result == [{"workroom_id": workroom_id}]
    assert sdk.calls == [("/dde/connectors/", {"X-Workroom-Id": workroom_id})]
    assert sleeps == []


def test_self_access_connector_list_non_403_propagates_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workroom_id = "wr-b"
    server_error = APIError(
        "API request failed with status 500: boom",
        status_code=500,
        response_text="boom",
        response_data={"detail": "boom"},
    )
    sdk = _FakeSdk(responses=[server_error], calls=[])
    sleeps: list[float] = []
    monkeypatch.setattr(
        isolation_live,
        "time",
        SimpleNamespace(sleep=sleeps.append),
        raising=False,
    )

    with pytest.raises(APIError) as exc_info:
        isolation_live._list_connectors_self_access(sdk, workroom_id, label="B")

    assert exc_info.value is server_error
    assert sdk.calls == [("/dde/connectors/", {"X-Workroom-Id": workroom_id})]
    assert sleeps == []


def test_self_access_connector_list_retries_typed_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workroom_id = "wr-b"
    sdk = _FakeSdk(
        responses=[
            AuthorizationError("workroom binding not visible yet", status_code=403),
            {"items": [{"workroom_id": workroom_id}]},
        ],
        calls=[],
    )
    sleeps: list[float] = []
    monkeypatch.setattr(
        isolation_live,
        "_SELF_ACCESS_CONNECTOR_RETRY_DELAYS_SECONDS",
        (0.01,),
        raising=False,
    )
    monkeypatch.setattr(
        isolation_live,
        "time",
        SimpleNamespace(sleep=sleeps.append),
        raising=False,
    )

    result = isolation_live._list_connectors_self_access(sdk, workroom_id, label="B")

    assert result == [{"workroom_id": workroom_id}]
    assert len(sdk.calls) == 2
    assert sleeps == [0.01]


def test_self_access_connector_list_retries_one_transient_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workroom_id = "wr-b"
    sdk = _FakeSdk(
        responses=[_forbidden(), {"items": [{"workroom_id": workroom_id}]}],
        calls=[],
    )
    sleeps: list[float] = []
    monkeypatch.setattr(
        isolation_live,
        "_SELF_ACCESS_CONNECTOR_RETRY_DELAYS_SECONDS",
        (0.01,),
        raising=False,
    )
    monkeypatch.setattr(
        isolation_live,
        "time",
        SimpleNamespace(sleep=sleeps.append),
        raising=False,
    )

    result = isolation_live._list_connectors_self_access(sdk, workroom_id, label="B")

    assert result == [{"workroom_id": workroom_id}]
    assert sdk.calls == [
        ("/dde/connectors/", {"X-Workroom-Id": workroom_id}),
        ("/dde/connectors/", {"X-Workroom-Id": workroom_id}),
    ]
    assert sleeps == [0.01]


def test_self_access_connector_list_persistent_403_keeps_diagnostic_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workroom_id = "wr-b"
    sdk = _FakeSdk(responses=[_forbidden(), _forbidden()], calls=[])
    monkeypatch.setattr(
        isolation_live,
        "_SELF_ACCESS_CONNECTOR_RETRY_DELAYS_SECONDS",
        (0.01,),
        raising=False,
    )
    monkeypatch.setattr(
        isolation_live,
        "time",
        SimpleNamespace(sleep=lambda _delay: None),
        raising=False,
    )

    with pytest.raises(APIError) as exc_info:
        isolation_live._list_connectors_self_access(sdk, workroom_id, label="B")

    error = exc_info.value
    assert error.status_code == 403
    assert "positive connector self-access failed" in str(error)
    assert "label=B" in str(error)
    assert "workroom_id=wr-b" in str(error)
    assert "endpoint=/dde/connectors/" in str(error)
    assert "attempts=2" in str(error)


def test_self_access_connector_list_persistent_typed_403_preserves_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workroom_id = "wr-b"
    body = {"detail": {"reason": "workroom_binding_not_visible"}}
    last_error = AuthorizationError(
        "workroom binding not visible yet",
        status_code=403,
        body=body,
    )
    sdk = _FakeSdk(responses=[last_error], calls=[])
    monkeypatch.setattr(
        isolation_live,
        "_SELF_ACCESS_CONNECTOR_RETRY_DELAYS_SECONDS",
        (),
        raising=False,
    )

    with pytest.raises(APIError) as exc_info:
        isolation_live._list_connectors_self_access(sdk, workroom_id, label="B")

    error = exc_info.value
    assert error.__cause__ is last_error
    assert error.response_data == body
