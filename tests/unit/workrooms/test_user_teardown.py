"""Every account this helper creates is removed and proven gone.

A partly created account is still found and removed; every teardown step runs
even when an earlier one failed, and every failure is named. A create the server
declined made no account, so cleanup must not send an operator after one.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

import pytest
from kamiwaza_sdk.exceptions import APIError
from tests.integration import _workroom_disposable_user as disposable
from tests.integration import _workroom_support as support
from tests.unit.workrooms._user_fakes import (
    SECRET_MARKER,
    Admin,
    AdminAuth,
    Config,
    UserClient,
    create,
    factory,
    sdk_error,
)


pytestmark = pytest.mark.unit


def test_an_account_created_before_its_create_call_failed_is_found_and_removed() -> (
    None
):
    auth = AdminAuth(create_error=sdk_error(), store_before_create_error=True)

    with pytest.raises(disposable.CredentialRequestError):
        create(auth, UserClient())

    (orphan,) = auth.users
    assert auth.deleted == [orphan]
    assert auth.readbacks == [orphan], "the removal was not proven"


def test_a_failure_after_login_removes_the_account() -> None:
    auth = AdminAuth()
    whoami_error = APIError("users/me unavailable", status_code=503)

    with pytest.raises(APIError) as raised:
        create(auth, client := UserClient(whoami_error=whoami_error))

    assert raised.value is whoami_error
    (account,) = auth.users
    assert auth.deleted == [account]
    assert auth.listings == 0, "the known account id was not used"
    assert client.posted == ["/auth/logout"], "the obtained login was not ended"


def test_a_cleanup_failure_after_a_failed_setup_is_raised_from_the_setup_error() -> (
    None
):
    """The setup error is the root cause; a withheld cleanup must not hide it."""
    auth = AdminAuth()
    whoami_error = APIError("users/me unavailable", status_code=503)

    with pytest.raises(support.CleanupError) as raised:
        create(auth, UserClient(whoami_error=whoami_error, logout_error=sdk_error()))

    assert raised.value.__cause__ is whoami_error
    assert "log the disposable user out" in str(raised.value)
    assert SECRET_MARKER not in str(raised.value)
    (account,) = auth.users
    assert auth.deleted == [account]


def test_an_interrupted_setup_still_interrupts_when_its_cleanup_fails() -> None:
    """A Ctrl-C must still stop the run; the cleanup failure is only its context."""
    auth = AdminAuth()

    with pytest.raises(KeyboardInterrupt) as raised:
        create(
            auth,
            UserClient(whoami_error=KeyboardInterrupt(), logout_error=sdk_error()),
        )

    assert isinstance(raised.value.__context__, support.CleanupError)
    (account,) = auth.users
    assert auth.deleted == [account]


def test_the_login_client_is_closed_and_teardown_closes_the_users_client() -> None:
    auth = AdminAuth()
    login_client, user_client = UserClient(), UserClient()

    def factory(*, base_url, api_key=None):
        return login_client if api_key is None else user_client

    user = disposable.create_workroom_user(Admin(auth), factory, "http://x")
    assert (login_client.closed, user_client.closed) == (1, 0)

    disposable.delete_workroom_user(Admin(auth), user)

    assert user_client.closed == 1
    assert user_client.posted == ["/auth/logout"]
    # Logout sends the login's refresh token.
    assert user_client.bodies == [{"json": {"refresh_token": "refresh"}}]


def test_logout_failure_still_deletes_proves_absence_and_withholds() -> None:
    auth = AdminAuth()
    user = create(auth, UserClient(logout_error=sdk_error()))

    with pytest.raises(support.CleanupError) as raised:
        disposable.delete_workroom_user(Admin(auth), user)

    assert SECRET_MARKER not in str(raised.value)
    assert auth.deleted == [user.id]
    # Read back once at creation, then once by the absence proof.
    assert auth.readbacks == [user.id, user.id], "the absence proof did not run"


def test_a_failed_delete_is_still_followed_by_the_absence_proof() -> None:
    auth = AdminAuth(delete_error=APIError("delete failed", status_code=500))
    user = create(auth, UserClient())

    with pytest.raises(support.CleanupError) as raised:
        disposable.delete_workroom_user(Admin(auth), user)

    assert auth.readbacks == [user.id, user.id]
    assert "still readable" in str(raised.value)


def test_teardown_fails_when_the_account_is_still_readable() -> None:
    auth = AdminAuth(delete_applies=False)
    user = create(auth, UserClient())

    with pytest.raises(support.CleanupError, match="still readable"):
        disposable.delete_workroom_user(Admin(auth), user)


def test_a_login_without_a_refresh_token_fails_teardown_loudly() -> None:
    auth = AdminAuth()
    user = create(auth, UserClient())
    user.refresh_token = None

    with pytest.raises(support.CleanupError, match="refresh token"):
        disposable.delete_workroom_user(Admin(auth), user)

    assert auth.deleted == [user.id], "the account was not deleted"


def test_an_interrupt_at_the_fixture_yield_still_deletes_the_user(
    untraced: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Ctrl-C can land before pytest registers the fixture's teardown."""
    # session_user refuses an echoing run; pin the logger so an ambient
    # --log-level=DEBUG cannot abort the whole unit session here.
    caplog.set_level(logging.INFO, logger=disposable.SDK_CLIENT_LOGGER)
    auth = AdminAuth()
    request = SimpleNamespace(config=Config())
    fixture = disposable.session_user(
        request, Admin(auth), factory(UserClient()), "http://x"
    )
    user = next(fixture)

    with pytest.raises(KeyboardInterrupt):
        fixture.throw(KeyboardInterrupt())

    assert auth.deleted == [user.id]


def test_a_failed_setup_closes_the_signed_in_client() -> None:
    auth = AdminAuth()
    login_client = UserClient()
    user_client = UserClient(
        whoami_error=APIError("users/me unavailable", status_code=503)
    )

    def factory(*, base_url, api_key=None):
        return login_client if api_key is None else user_client

    with pytest.raises(APIError):
        disposable.create_workroom_user(Admin(auth), factory, "http://x")

    assert (login_client.closed, user_client.closed) == (1, 1)


def test_a_failed_client_build_logs_out_through_the_login_client() -> None:
    """The login client can still end the login the factory failed to re-use."""
    auth = AdminAuth()
    login_client = UserClient()

    def factory(*, base_url, api_key=None):
        if api_key is not None:
            raise RuntimeError("client construction broke")
        return login_client

    with pytest.raises(disposable.CredentialRequestError):
        disposable.create_workroom_user(Admin(auth), factory, "http://x")

    assert login_client.posted == ["/auth/logout"]
    assert login_client.bodies == [{"json": {"refresh_token": "refresh"}}]
    assert login_client.closed == 1
    (account,) = auth.users
    assert auth.deleted == [account]


def test_a_local_user_no_listing_shows_is_named_as_unconfirmed() -> None:
    """The create's outcome is unknown and the listing shows nothing: say so.

    A 503 leaves it open whether the server acted, unlike the declined 4xx in
    the test below.
    """
    auth = AdminAuth(create_error=APIError("upstream broke", status_code=503))

    with pytest.raises(support.CleanupError) as reported:
        create(auth, UserClient())

    # The create never stored an account, so the report's own text is the only
    # place the unique name can come from: a prefix alone would not tell two
    # concurrent runs' leftovers apart.
    message = str(reported.value)
    assert re.search(
        rf"{re.escape(disposable.USERNAME_PREFIX)}[0-9a-f]{{16}}", message
    ), f"the report does not name a unique account: {message}"
    assert isinstance(reported.value.__cause__, disposable.CredentialRequestError), (
        "the setup failure is not the cleanup report's cause"
    )

    assert auth.listings == 1, "the account was not looked for by name"
    assert auth.deleted == [], "an account was deleted without being found"


def test_the_registered_cleanups_run_before_the_login_is_logged_out() -> None:
    """One teardown for everything the user owns, while its login still works."""
    auth = AdminAuth()
    client = UserClient()
    user = create(auth, client)
    order: list[str] = []
    user.cleanups.append(
        (
            "registered cleanup",
            lambda: order.append(f"cleanup, posts so far {client.posted}"),
        )
    )

    disposable.delete_workroom_user(Admin(auth), user)

    assert order == ["cleanup, posts so far []"], "the logout ran before the cleanup"
    assert client.posted == ["/auth/logout"]
    assert auth.deleted == [user.id]


def test_a_registered_cleanup_that_fails_still_deletes_the_account() -> None:
    auth = AdminAuth()
    user = create(auth, UserClient())

    def broken() -> None:
        raise APIError("the ledger could not finish", status_code=500)

    user.cleanups.append(("registered cleanup", broken))

    with pytest.raises(support.CleanupError, match="registered cleanup"):
        disposable.delete_workroom_user(Admin(auth), user)

    assert auth.deleted == [user.id], "the account survived a failed ledger cleanup"


def test_an_interrupt_during_setup_shows_why_cleanup_failed() -> None:
    """The interrupt stops the run; the resource left behind must still be named."""
    auth = AdminAuth(delete_error=sdk_error())
    client = UserClient()

    def interrupted_login(*_args, **_kwargs):
        raise KeyboardInterrupt

    client.login_with_password = interrupted_login  # type: ignore[method-assign]

    with pytest.raises(KeyboardInterrupt) as stopped:
        create(auth, client)

    assert auth.deleted, "the account deletion was never attempted"

    assert isinstance(stopped.value.__cause__, support.CleanupError), (
        "the cleanup failure is not the interrupt's cause, so it is not rendered"
    )


def test_a_declined_create_reports_no_account_to_look_for() -> None:
    """A 4xx means the server refused; an operator must not be sent after it."""
    auth = AdminAuth(create_error=APIError("bad password", status_code=400))

    with pytest.raises(disposable.CredentialRequestError):
        create(auth, UserClient())

    assert auth.listings == 1, "the account was not looked for at all"


def test_an_interrupt_in_cleanup_keeps_the_setup_failure_visible() -> None:
    """The interrupt stops the run; what brought us here must still be readable."""
    auth = AdminAuth()
    client = UserClient(whoami_error=APIError("setup broke", status_code=503))

    def interrupted_logout(*_args, **_kwargs):
        raise KeyboardInterrupt

    client.post = interrupted_logout  # type: ignore[method-assign]

    with pytest.raises(KeyboardInterrupt) as stopped:
        create(auth, client)

    # Whatever its type -- whoami is not withheld, so its own error propagates
    # -- the failure that brought us here must be the interrupt's cause.
    assert "setup broke" in str(stopped.value.__cause__), (
        f"the setup failure is not rendered: {stopped.value.__cause__!r}"
    )


def test_an_interrupt_in_cleanup_still_names_the_other_step_that_failed() -> None:
    """A stop must not drop the summary of the steps that failed beside it.

    ``attempt_all`` re-raises the interrupt carrying a ``CleanupError`` that
    names every other failed step. Attaching the setup failure as the cause
    must keep those names: the account this pass could not delete would
    otherwise be named nowhere, which is what the summary exists to prevent.
    """
    auth = AdminAuth(delete_error=APIError("delete refused", status_code=409))
    client = UserClient(whoami_error=APIError("setup broke", status_code=503))

    def interrupted_logout(*_args, **_kwargs):
        raise KeyboardInterrupt

    client.post = interrupted_logout  # type: ignore[method-assign]

    with pytest.raises(KeyboardInterrupt) as stopped:
        create(auth, client)

    cause = str(stopped.value.__cause__)
    assert "setup broke" in cause, f"the setup failure is not rendered: {cause}"
    assert "delete refused" in cause, f"the other failed step is not named: {cause}"
