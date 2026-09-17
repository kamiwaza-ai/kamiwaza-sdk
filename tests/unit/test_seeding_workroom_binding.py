"""Bounded new-workroom projection recovery preserves failures and scope."""

from unittest.mock import Mock

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.seeding import workroom_binding as binding

pytestmark = pytest.mark.unit


def _pending():
    return APIError(
        "pending",
        status_code=503,
        response_data={"detail": "authorization_unavailable"},
    )


def test_pending_authority_recovers_before_binding(monkeypatch):
    client = Mock()
    client.workrooms.enter.side_effect = [_pending(), None]
    sleep = Mock()
    monkeypatch.setattr(binding.time, "sleep", sleep)
    binding.enter_projected_workroom(client, "room")
    assert client.workrooms.enter.call_count == 2
    client.workrooms.enter.assert_called_with("room")
    sleep.assert_called_once_with(1.0)


@pytest.mark.parametrize(
    "status,body",
    [
        (403, {"detail": "authorization_unavailable"}),
        (503, {}),
        (503, None),
        (503, {"detail": "backend_down"}),
        (409, {"detail": "workroom_binding_invalid"}),
    ],
)
def test_other_errors_are_never_retried(monkeypatch, status, body):
    error = APIError("failure", status_code=status, response_data=body)
    client = Mock()
    client.workrooms.enter.side_effect = error
    sleep = Mock()
    monkeypatch.setattr(binding.time, "sleep", sleep)
    with pytest.raises(APIError) as caught:
        binding.enter_projected_workroom(client, "room")
    assert caught.value is error
    client.workrooms.enter.assert_called_once_with("room")
    sleep.assert_not_called()


def test_persistent_outage_preserves_error_at_deadline(monkeypatch):
    ticks = iter([0.0, 29.75, 30.0])
    monkeypatch.setattr(binding.time, "monotonic", lambda: next(ticks))
    sleep = Mock()
    monkeypatch.setattr(binding.time, "sleep", sleep)
    error = _pending()
    client = Mock()
    client.workrooms.enter.side_effect = error
    with pytest.raises(APIError) as caught:
        binding.enter_projected_workroom(client, "room")
    assert caught.value is error
    sleep.assert_called_once_with(0.25)
    assert client.workrooms.enter.call_count == 2
