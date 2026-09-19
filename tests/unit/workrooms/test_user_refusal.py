"""The run is refused before an account exists when output would echo it.

``--showlocals``, ``--full-trace``, ``--pdb``, ``--trace``, DEBUG client logging
and HTTP tracing each make pytest or the SDK render what this helper is built to
withhold. The refusal happens before the create, so a refused run leaves nothing
behind and writes no evidence record.

A login is refused on the same principle when its token would expire before the
run's bounded waits could finish: the disposable user's token cannot be
refreshed, so a short lifetime would strand a run mid-cleanup.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from tests.integration import _workroom_disposable_user as disposable
from tests.integration import _workroom_support as support
from tests.unit.workrooms._user_fakes import (
    Grant,
    TRACE_ENV,
    Admin,
    AdminAuth,
    Config,
    UserClient,
    create,
    factory,
)


pytestmark = pytest.mark.unit


_REFUSAL_NAMES = {
    "showlocals": "--showlocals",
    "fulltrace": "--full-trace",
    "usepdb": "--pdb",
    "trace": "--trace",
    "debug": disposable.SDK_CLIENT_LOGGER,
    "http_trace": "HTTP tracing",
    "http_trace_file": "HTTP tracing",
}


@pytest.mark.parametrize("cause", list(_REFUSAL_NAMES))
def test_refuses_to_run_when_output_would_echo_credentials(
    cause: str, caplog: pytest.LogCaptureFixture, untraced: pytest.MonkeyPatch
) -> None:
    config = Config(**({cause: True} if cause in Config.OPTIONS else {}))
    level = logging.DEBUG if cause == "debug" else logging.INFO
    caplog.set_level(level, logger=disposable.SDK_CLIENT_LOGGER)
    if cause in TRACE_ENV:
        untraced.setenv(*TRACE_ENV[cause])

    # A skip, not a failure and not an abort: an all-skipped entry writes no
    # evidence record, and the live tests that do not create an account run on.
    with pytest.raises(pytest.skip.Exception) as refused:
        disposable.refuse_credential_echo(config)
    assert _REFUSAL_NAMES[cause] in str(refused.value), (
        f"the skip reason does not name what was refused: {refused.value}"
    )


def test_allows_a_run_that_would_not_echo_credentials(
    caplog: pytest.LogCaptureFixture, untraced: pytest.MonkeyPatch
) -> None:
    """Guards the other direction: the refusal must not fire on a safe run.

    A refusal here would skip this test rather than fail it, so the skip is
    turned into a failure: a regression must not read as green.
    """
    caplog.set_level(logging.INFO, logger=disposable.SDK_CLIENT_LOGGER)
    untraced.setenv("KAMIWAZA_HTTP_TRACE", "0")
    config = Config()

    try:
        disposable.refuse_credential_echo(config)
    except pytest.skip.Exception as refused:
        raise AssertionError(f"a safe run was refused: {refused}") from None

    assert set(Config.OPTIONS) <= set(config.asked), (
        f"the refusal never read {set(Config.OPTIONS) - set(config.asked)}"
    )


def test_session_user_refuses_an_echoing_run_before_creating_anything(
    untraced: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=disposable.SDK_CLIENT_LOGGER)
    auth = AdminAuth()
    request = SimpleNamespace(config=Config(showlocals=True))

    with pytest.raises(pytest.skip.Exception):
        next(
            disposable.session_user(
                request, Admin(auth), factory(UserClient()), "http://x"
            )
        )

    assert auth.requested_roles == [], "an account was created before the refusal"


def test_a_login_whose_token_expires_before_a_run_can_finish_is_refused() -> None:
    auth = AdminAuth()

    with pytest.raises(AssertionError, match="expires in 60 s"):
        create(auth, client := UserClient(grant=Grant(expires_in=60)))

    (account,) = auth.users
    assert auth.deleted == [account]
    assert client.posted == ["/auth/logout"]


def test_the_token_floor_covers_the_bounded_waits_these_tests_make() -> None:
    """A slow run must not outlive the token the disposable user cannot refresh."""
    assert support.BOUNDED_WAIT_BUDGET_SECONDS <= disposable.MIN_TOKEN_LIFETIME_SECONDS
