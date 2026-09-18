"""``attempt_all`` and ``expect_not_found``: the cleanup primitives.

Every cleanup step must run even when an earlier one fails, every failure must
be named, and a stop signal must still stop the run once the remaining steps
have had their turn. Removal is proven by an authoritative read, never a search.
"""

from __future__ import annotations


import pytest
from kamiwaza_sdk.exceptions import (
    APIError,
    NotFoundError,
)
from tests.integration import _workroom_support as support

pytestmark = pytest.mark.unit


def test_attempt_all_runs_every_step_and_reports_each_failure() -> None:
    ran: list[str] = []

    def fail(label: str) -> None:
        ran.append(label)
        raise APIError(f"{label} broke", status_code=500)

    with pytest.raises(support.CleanupError) as raised:
        support.attempt_all(
            [
                ("first step", lambda: fail("first")),
                ("second step", lambda: ran.append("second")),
                ("third step", lambda: fail("third")),
            ]
        )

    assert ran == ["first", "second", "third"]
    message = str(raised.value)
    assert "first step" in message and "third step" in message
    assert "second step" not in message
    assert str(raised.value.__cause__) == "first broke"


def test_attempt_all_is_silent_when_every_step_succeeds() -> None:
    ran: list[str] = []

    support.attempt_all([("only step", lambda: ran.append("only"))])

    assert ran == ["only"], "the step was never run"


def test_attempt_all_runs_the_steps_after_an_interrupt_and_re_raises_it() -> None:
    """A Ctrl-C during one cleanup call must not skip the deletions after it."""
    ran: list[str] = []

    def interrupted() -> None:
        raise KeyboardInterrupt

    def broken() -> None:
        raise APIError("delete failed", status_code=500)

    with pytest.raises(KeyboardInterrupt) as stopped:
        support.attempt_all(
            [
                ("interrupted step", interrupted),
                ("failed step", broken),
                ("later step", lambda: ran.append("later")),
            ]
        )

    assert ran == ["later"], "cleanup stopped at the interrupt"
    # The interrupt wins the raise, so the failed step is named in its cause.
    assert "failed step" in str(stopped.value.__cause__)


def test_a_pytest_outcome_in_a_cleanup_step_is_reported_not_re_raised() -> None:
    """A skip inside cleanup must not decide the test's own outcome."""
    ran: list[str] = []

    def skipping() -> None:
        pytest.skip("a cleanup step should not skip the test")

    steps = [("skipping step", skipping), ("later step", lambda: ran.append("later"))]
    try:
        support.attempt_all(steps)
    except support.CleanupError as reported:
        assert "skipping step" in str(reported)
    except BaseException as escaped:  # noqa: BLE001 - the defect this test pins
        raise AssertionError(
            f"a cleanup step's {type(escaped).__name__} escaped attempt_all; "
            "raised here as a failure, since a skip would read as green"
        ) from None
    else:
        raise AssertionError("attempt_all reported nothing")

    assert ran == ["later"]


def test_a_system_exit_in_a_cleanup_step_still_runs_the_rest() -> None:
    ran: list[str] = []

    def exiting() -> None:
        raise SystemExit(3)

    with pytest.raises(SystemExit) as stopped:
        support.attempt_all(
            [("exiting step", exiting), ("later step", lambda: ran.append("later"))]
        )

    assert ran == ["later"] and stopped.value.code == 3


def test_a_nested_cleanups_summary_survives_an_interrupt() -> None:
    """The ledger's removals run as one step of the disposable user's teardown.

    Re-raising the inner interrupt would overwrite its cause, so what the inner
    call could not remove has to be folded into the outer summary.
    """

    def refused() -> None:
        raise APIError("delete refused", status_code=409)

    def interrupted() -> None:
        raise KeyboardInterrupt

    def inner() -> None:
        support.attempt_all(
            [("delete dataset d-1", refused), ("enter the workroom", interrupted)]
        )

    with pytest.raises(KeyboardInterrupt) as stopped:
        support.attempt_all(
            [("remove what the ledger recorded", inner), ("close", lambda: None)]
        )

    named = str(stopped.value.__cause__)
    assert "delete dataset d-1" in named, f"the inner summary was lost: {named}"


def test_expect_not_found_accepts_the_sdks_404() -> None:
    reads: list[str] = []

    def read() -> None:
        reads.append("read")
        raise NotFoundError("gone")

    support.expect_not_found(read, "the thing")

    assert reads == ["read"], "the absence proof never read the resource"


def test_expect_not_found_rejects_a_readable_resource() -> None:
    with pytest.raises(AssertionError, match="the thing is still readable"):
        support.expect_not_found(lambda: object(), "the thing")


@pytest.mark.parametrize(
    "error",
    [
        APIError("server broke", status_code=500),
        # The SDK raises NotFoundError for every JSON 404, so a plain 404 is
        # not a shape it produces and must not be read as absence.
        APIError("untyped 404", status_code=404),
    ],
)
def test_expect_not_found_propagates_any_other_error(error: APIError) -> None:
    def read() -> None:
        raise error

    with pytest.raises(APIError) as raised:
        support.expect_not_found(read, "the thing")
    assert raised.value is error
