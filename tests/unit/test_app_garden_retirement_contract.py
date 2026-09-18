"""Unit cover for the App Garden evidence module's retirement station.

ENG-12432. The station's job is to prove that ``stop_deployment`` stopped the
workload, and the ways it can fail to prove that are all reachable without a
cluster: a platform that acknowledges the stop and never performs it, a row the
platform reaps before any poll sees ``STOPPED``, and a status lookup that errors.

Driving the station with a fake client, in the manner of
``test_live_model_file_cleanup_contract.py``, pins each of those without waiting
on a deployment. The clock is faked too: the real station polls every 5s for
300s.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from kamiwaza_sdk.exceptions import APIError
from tests.integration import test_app_garden_lifecycle_live as garden

NAME = "eng12432-garden-fake"


@pytest.fixture(autouse=True)
def _fast_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clock that reaches the station's 300s deadline in a few polls."""
    ticks = iter(range(0, 10_000, 60))
    monkeypatch.setattr(
        garden,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _seconds: None),
    )


def _client(*, status, listed: bool, stopped: bool = True) -> SimpleNamespace:
    """A client whose status poll returns (or raises) ``status``.

    ``listed`` says whether the platform still carries a row under ``NAME``,
    which is how the station tells a reaped deployment from a stuck one.
    """

    def get_deployment_status(_deployment_id):
        if isinstance(status, Exception):
            raise status
        return status

    rows = [SimpleNamespace(id=uuid4(), name=NAME)] if listed else []
    return SimpleNamespace(
        apps=SimpleNamespace(
            stop_deployment=lambda _deployment_id: stopped,
            get_deployment_status=get_deployment_status,
            list_deployments=lambda: rows,
        )
    )


def test_a_deployment_stuck_in_stop_requested_does_not_pass_the_station() -> None:
    """STOP_REQUESTED records the request, not the outcome.

    Accepting it would publish stop-lifecycle evidence for a workload still
    running on a shared host.
    """
    client = _client(status="STOP_REQUESTED", listed=True)
    with pytest.raises(pytest.fail.Exception, match="did not stop within 300s"):
        garden._assert_retirement_station(client, uuid4(), NAME)


def test_a_reaped_row_counts_as_a_completed_stop() -> None:
    """The race the station has to tolerate: the row goes before STOPPED shows."""
    client = _client(status="STOP_REQUESTED", listed=False)
    garden._assert_retirement_station(client, uuid4(), NAME)


def test_a_stopped_status_counts_without_the_row_being_reaped() -> None:
    client = _client(status="STOPPED", listed=True)
    garden._assert_retirement_station(client, uuid4(), NAME)


def test_a_status_error_on_a_row_still_listed_is_not_read_as_a_stop() -> None:
    """A lookup failure is a fault while the deployment is still there."""
    client = _client(status=APIError("boom"), listed=True)
    with pytest.raises(APIError, match="boom"):
        garden._assert_retirement_station(client, uuid4(), NAME)


def test_a_status_error_on_an_absent_row_counts_as_a_completed_stop() -> None:
    client = _client(status=APIError("404"), listed=False)
    garden._assert_retirement_station(client, uuid4(), NAME)


def test_a_stop_call_that_reports_failure_fails_the_station() -> None:
    client = _client(status="STOPPED", listed=False, stopped=False)
    with pytest.raises(AssertionError, match="did not report success"):
        garden._assert_retirement_station(client, uuid4(), NAME)
