"""First-ingress projection waits must stay bounded and fail closed."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.validation import federation_readiness as readiness

pytestmark = pytest.mark.contract


def _persona(listing):
    return SimpleNamespace(
        catalog=SimpleNamespace(datasets=SimpleNamespace(list=listing))
    )


def _pending():
    return APIError(
        "not ready",
        status_code=503,
        response_data={"detail": "authorization_unavailable"},
    )


def test_waits_for_projection_using_only_dataset_reads(monkeypatch):
    listing = Mock(side_effect=[_pending(), _pending(), ["fixture"]])
    sleep = Mock()
    monkeypatch.setattr(readiness.time, "sleep", sleep)
    assert readiness.authorized_datasets(_persona(listing), "peer") == ["fixture"]
    assert listing.call_count == 3
    listing.assert_called_with(target_cluster="peer")
    assert sleep.call_count == 2


@pytest.mark.parametrize(
    "error",
    [
        APIError("denied", status_code=401),
        APIError("denied", status_code=403),
        APIError("missing", status_code=404),
        APIError("broken", status_code=500),
        APIError("unavailable", status_code=503, response_data={"detail": "other"}),
        APIError(
            "unavailable", status_code=503, response_data="authorization_unavailable"
        ),
        RuntimeError("connection failure"),
    ],
)
def test_other_failures_are_not_retried(monkeypatch, error):
    listing = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr(readiness.time, "sleep", sleep)
    with pytest.raises(type(error)) as raised:
        readiness.authorized_datasets(_persona(listing), "peer")
    assert raised.value is error
    assert listing.call_count == 1
    sleep.assert_not_called()


def test_projection_timeout_does_not_pass_or_publish_error_payload(monkeypatch):
    listing = Mock(side_effect=_pending())
    ticks = iter([0.0, 29.75, 30.0])
    sleep = Mock()
    monkeypatch.setattr(readiness.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(readiness.time, "sleep", sleep)
    with pytest.raises(TimeoutError, match="authorization projection timed out"):
        readiness.authorized_datasets(_persona(listing), "peer")
    sleep.assert_called_once_with(0.25)
    assert listing.call_count == 2
