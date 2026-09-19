"""A disposable non-admin user for the live workroom evidence tests (ENG-12325).

These tests enter workrooms as a local user created for the run and signed in
with a password grant. The shared administrator cannot stand in for it: its PAT
is refused a workroom enter at v1.2.1 (``test_seeding_live.py``'s
``test_pat_workroom_enter_is_rejected`` pins that), and these tests must not
disturb the other runs that authenticate as that administrator.

Creating an account is opt-in through ``DISPOSABLE_IDENTITIES_ENV``. Each
username is emitted as a ``DisposableAccountWarning`` before the account is
created, so the accounts a run created are named in pytest's warnings summary
(unless the run disables warnings, for example with ``--disable-warnings``).

What this module keeps out of pytest's output, and what it cannot:

- the password is never stored, and the tokens live in ``repr=False`` fields;
- ``refuse_credential_echo`` skips before any account exists when the run would
  print or record the account's credentials. A skipped test writes no evidence
  record (the emitter counts only passed and failed steps), and unrelated live
  tests in the same session still run. The options and variables it refuses:
  ``--showlocals``; ``--full-trace``, ``--pdb`` or ``--trace``, which render a
  Ctrl-C's traceback (and ``--full-trace`` the frames ``__tracebackhide__``
  hides) with frame arguments, bearer headers included; DEBUG logging for
  ``kamiwaza_sdk.client``, which logs request headers; and HTTP tracing
  (``KAMIWAZA_HTTP_TRACE`` or ``KAMIWAZA_HTTP_TRACE_FILE``), which writes
  request headers and bodies to a file. Each evidence module also calls it from
  a session-scoped fixture, so in a run of those modules it refuses before any
  live fixture authenticates. It cannot cover a session that reached the shared
  admin first: another module's test, running earlier in the same session, can
  leave the admin's credentials in a DEBUG log or a trace file, and a
  ``--showlocals`` teardown failure in a session fixture that holds a token
  renders it. That is how the integration suite's fixtures, logging and tracing
  treat every live test; these tests do not change it, and skipping (rather
  than exiting) leaves those other tests running;
- no function of this module that can be on the stack when a failure is raised
  takes the password or a token as an argument, because ``--tb=long`` renders
  the arguments of every frame in a failure's traceback;
- the calls made here that send the password or a token (create, login,
  building the signed-in client, logout) run with the SDK client's log lines
  suppressed. An ``Exception`` they raise is re-raised without context, naming
  only the operation, error type, and HTTP status; that covers SDK errors and
  the pydantic error raised when a token response does not validate, whose
  message echoes the tokens. A ``KeyboardInterrupt`` or ``SystemExit`` is
  re-raised as a fresh stop signal of the same kind, so the frames of the call
  it stopped go with it. The re-raise
  happens in a plain function with its frame hidden, and credential-carrying
  calls are lambdas, not ``functools.partial``, whose repr would show them if
  that frame were rendered.
- NOT covered: a transport-level failure (connection reset, timeout) of any
  SDK request made with a bearer, by this user's client or the shared admin
  client, is re-raised by the SDK with the ``requests``/``urllib3`` exception
  chained, and pytest renders that chain's frame arguments, request headers
  included (observed offline, 2026-09-17). That is SDK behavior shared by every
  live test; the logs of an evidence run must be scanned for tokens before they
  are kept.

The user's client holds a fixed access token and cannot refresh it, so setup
refuses a login whose token expires in under ``MIN_TOKEN_LIFETIME_SECONDS``.
A failure path that still outlives the token makes the owner-side cleanup steps
fail with an authentication error; those failures are reported, and each
resource left behind is named. A password grant that succeeds on the server but
whose response is lost, or does not validate, leaves a login the helper never
learns about, so cleanup cannot log it out; the same is true of an interrupt
that lands in the statement between the grant returning and its logout step
being prepared.
"""

from __future__ import annotations

import logging
import os
import secrets
import string
import uuid
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from typing import TypeVar
from uuid import UUID

import pytest
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.auth import LocalUserCreateRequest, TokenResponse

from ._workroom_support import (
    CleanupError,
    Step,
    attempt_all,
    NAME_SUFFIX_HEX,
    declined_by_the_server,
    expect_not_found,
    refuse_unconfirmed,
    rendered_with_summary,
)

DISPOSABLE_IDENTITIES_ENV = "KAMIWAZA_TEST_DISPOSABLE_IDENTITIES"
SDK_CLIENT_LOGGER = "kamiwaza_sdk.client"
USERNAME_PREFIX = "sdk-workroom-evidence-"
HTTP_TRACE_FLAG_ENV = "KAMIWAZA_HTTP_TRACE"
HTTP_TRACE_FILE_ENV = "KAMIWAZA_HTTP_TRACE_FILE"
# Covers the bounded waits these tests can make (BOUNDED_WAIT_BUDGET_SECONDS,
# derived from the live tests' own wait counts) plus the HTTP time around them.
# The user's client holds a fixed token, so a shorter lifetime expires mid-run.
MIN_TOKEN_LIFETIME_SECONDS = 300
# What CPython exits with when SystemExit carries a non-int code: it prints the
# code and exits 1. Replacing such a code keeps that status and drops the text.
NON_INT_EXIT_STATUS = 1

ClientFactory = Callable[..., KamiwazaClient]
T = TypeVar("T")

requires_disposable_identities = pytest.mark.skipif(
    os.environ.get(DISPOSABLE_IDENTITIES_ENV, "").strip() != "1",
    reason=(
        "creates a throwaway local account on the deployment; set "
        f"{DISPOSABLE_IDENTITIES_ENV}=1 to run it"
    ),
)


class DisposableAccountWarning(UserWarning):
    """Names a throwaway local account that a run creates."""


class CredentialRequestError(RuntimeError):
    """A call carrying a credential failed; its original message is withheld."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        # A 4xx means the server acted on the request and declined it, which
        # tells cleanup whether anything can have been created.
        self.status = status


_PASSWORD_CLASSES = (
    string.ascii_lowercase,
    string.ascii_uppercase,
    string.digits,
    "!@#$%^&*",
)
PASSWORD_LENGTH = 32


def _password() -> str:
    """A password that satisfies a realm policy requiring each character class.

    One character from each class, then filled out from all of them. Drawing
    uniformly from letters and digits alone would be refused by every create on
    a realm that requires a symbol.
    """
    everything = "".join(_PASSWORD_CLASSES)
    chars = [secrets.choice(alphabet) for alphabet in _PASSWORD_CLASSES]
    chars += [
        secrets.choice(everything)
        for _ in range(PASSWORD_LENGTH - len(_PASSWORD_CLASSES))
    ]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


@dataclass
class WorkroomUser:
    """A signed-in local user created by this run.

    ``cleanups`` are steps a test's other fixtures register here rather than
    running in a finalizer of their own. Everything this user owns is then torn
    down by one ``attempt_all``: pytest stops calling the remaining finalizers
    when one of them raises a ``BaseException`` (``_pytest.runner.TEST_OUTCOME``
    is ``(OutcomeException, Exception)``), so a Ctrl-C in a separate ledger
    finalizer would otherwise leave the account behind.
    """

    id: UUID
    username: str
    subject: str
    client: KamiwazaClient = field(repr=False)
    refresh_token: str | None = field(repr=False)
    cleanups: list[Step] = field(default_factory=list, repr=False)


def _refuse(reason: str) -> None:
    """Skip: an all-skipped entry writes no evidence record, and the session lives."""
    pytest.skip(reason)


def refuse_credential_echo(config: pytest.Config) -> None:
    """Skip before creating credentials that this run's output would print."""
    if config.getoption("showlocals", default=False):
        _refuse("--showlocals would print the tokens held in locals")
    for option, flag in (("fulltrace", "--full-trace"), ("usepdb", "--pdb")):
        if config.getoption(option, default=False):
            _refuse(
                f"{flag} would print a Ctrl-C's traceback, whose frames take the "
                "password, a token, or bearer headers as arguments"
            )
    if config.getoption("trace", default=False):
        _refuse("--trace opens a debugger where the credentials are readable")
    if logging.getLogger(SDK_CLIENT_LOGGER).isEnabledFor(logging.DEBUG):
        _refuse(f"DEBUG logging for {SDK_CLIENT_LOGGER} would print bearer tokens")
    # The integration conftest's http_trace_logging fixture writes every request's
    # headers and body to a file when either variable enables it.
    trace_flag = os.environ.get(HTTP_TRACE_FLAG_ENV, "").strip().lower()
    if (
        trace_flag in {"1", "true", "yes", "on"}
        or os.environ.get(HTTP_TRACE_FILE_ENV, "").strip()
    ):
        _refuse("HTTP tracing would write the password and tokens to its file")


def _unauthenticated(client: KamiwazaClient) -> KamiwazaClient:
    """Drop the ambient credential a client built without an api_key adopts.

    ``KamiwazaClient`` falls back to ``KAMIWAZA_API_KEY`` or
    ``KAMIWAZA_API_TOKEN`` from the environment, which on a configured host is
    the shared administrator's. The client that performs this user's password
    grant -- and the logout that may follow it, until the signed-in client
    exists -- must carry this user's identity and no other.
    """
    client.authenticator = None
    return client


def _status_without_payload(stop: SystemExit) -> int | None:
    """``stop``'s exit status, dropping a payload that could carry a credential.

    ``SystemExit.code`` is whatever was raised with it, and ``sys.exit("msg")``
    is an ordinary idiom, so the code can be a string. pytest renders it as
    ``E  SystemExit: <code>``, which would put a credential-bearing message
    into the output this module exists to keep it out of. An ``int`` or
    ``None`` is a status and carries nothing, so it passes through; anything
    else is replaced by the status CPython itself exits with for a non-int
    code, so the run still stops the same way with the payload dropped.
    """
    code = stop.code
    if code is None or isinstance(code, int):
        return code
    return NON_INT_EXIT_STATUS


def _withheld(operation: str, call: Callable[[], T]) -> T:
    """Run a call that sends a credential, withholding how it failed.

    Any ``Exception`` is caught because none can be trusted not to echo the
    credential (pydantic's message includes the response it failed to parse).
    The failure is re-raised, never swallowed. A ``KeyboardInterrupt`` or a
    ``SystemExit`` is re-raised as a fresh stop signal of the same kind, so the
    run still stops but the frames of the call it stopped, which take the
    credential as an argument, go with it; the replacement ``SystemExit``
    carries only a status, because ``SystemExit.code`` is an arbitrary object
    and pytest prints it. No other ``BaseException`` is raised by these calls,
    and one would keep its frames.
    """
    __tracebackhide__ = True  # keep `call` out of long tracebacks
    client_logger = logging.getLogger(SDK_CLIENT_LOGGER)
    previously_disabled = client_logger.disabled
    client_logger.disabled = True
    try:
        return call()
    except (KeyboardInterrupt, SystemExit) as stop:
        failure: BaseException = (
            SystemExit(_status_without_payload(stop))
            if isinstance(stop, SystemExit)
            else KeyboardInterrupt()
        )
    except Exception as exc:  # noqa: BLE001 - re-raised below without its message
        # Validated before it is interpolated, not only before it is stored:
        # the message is what pytest prints, so an attribute that was not an
        # int would have its repr rendered by the very text whose job is to
        # withhold everything else the exception carried.
        status = getattr(exc, "status_code", None)
        status = status if isinstance(status, int) else None
        failure = CredentialRequestError(
            f"{operation} failed with {type(exc).__name__} (HTTP status {status}); "
            "the original message is withheld because it can carry the credential",
            status=status,
        )
    finally:
        client_logger.disabled = previously_disabled
    raise failure from None


def _account_removal_steps(
    admin: KamiwazaClient, username: str, user_id: UUID
) -> list[Step]:
    return [
        (f"delete local user {username}", partial(admin.auth.delete_user, user_id)),
        (
            f"prove local user {username} is gone",
            partial(
                expect_not_found,
                partial(admin.auth.get_user, user_id),
                f"local user {username}",
            ),
        ),
    ]


def _session_left_open(reason: str) -> Step:
    """A cleanup step that reports a login it cannot log out."""

    def cannot_log_out() -> None:
        raise RuntimeError(f"{reason}, so the login cannot be logged out")

    return ("log the disposable user out", cannot_log_out)


def _logout_step(client: KamiwazaClient, refresh_token: str | None) -> Step:
    """Log a login out, sending its refresh token."""
    if refresh_token is None:
        return _session_left_open("the login returned no refresh token")
    return (
        "log the disposable user out",
        lambda: _withheld(
            "logout",
            lambda: client.post("/auth/logout", json={"refresh_token": refresh_token}),
        ),
    )


def _with_setup_failure(
    carried: BaseException | None, setup_error: BaseException
) -> BaseException:
    """The cause to attach, keeping any cleanup summary the stop already carried.

    ``attempt_all`` re-raises an interrupt with a ``CleanupError`` naming every
    step that failed in the same pass. Re-raising that interrupt ``from`` the
    setup failure replaces its cause, so those names would be dropped -- the
    one thing the summary exists to prevent. Both are kept instead, through the
    same folding helper ``_describe`` uses one level down, so the rule has one
    implementation rather than two that can be fixed apart.
    """
    if not isinstance(carried, CleanupError):
        return setup_error
    return CleanupError(rendered_with_summary(setup_error, carried))


def _remove_failed_setup(
    admin: KamiwazaClient,
    username: str,
    created_id: UUID | None,
    logout: Step | None,
    clients: list[KamiwazaClient],
    *,
    declined: bool = False,
) -> None:
    # Takes the logout as a prepared step, never the token: this frame is live
    # when a cleanup failure is raised, and pytest renders its arguments.
    steps: list[Step] = [] if logout is None else [logout]
    steps.extend(
        ("close the disposable user's client", client.close) for client in clients
    )
    if created_id is not None:
        steps.extend(_account_removal_steps(admin, username, created_id))
    else:
        # The create call failed; the server may still have created the account.
        # On 1.2.1 the listing returns every non-deleted local user, unpaged, so
        # an account it shows is removed here. One it does not show is
        # reported as unconfirmed: this cannot tell "never created" from "not
        # listed yet", and the difference matters on a shared deployment.
        def remove_by_username() -> None:
            # The lookup runs even for a declined create, because a 4xx does
            # not prove nothing was stored: a server that persisted the account
            # and then refused the response is exactly the case above, and
            # leaving that account behind is the failure this module exists to
            # prevent. The name is the ownership proof: its suffix is
            # NAME_SUFFIX_HEX hex digits from uuid4, the same width every other
            # name this ticket generates uses, so a match is this run's account
            # rather than a stranger's. That bounds, without erasing, the
            # converse risk: were a create declined *because* the name were
            # already taken, this would delete the holder's account. Reaching
            # that needs a suffix collision or an actor acting on the name
            # _announce_account prints before the create.
            for user in admin.auth.list_users():
                if user.username == username:
                    attempt_all(_account_removal_steps(admin, username, user.id))
                    return
            if not declined:
                refuse_unconfirmed(f"local user {username}")

        steps.append((f"find and remove local user {username}", remove_by_username))
    attempt_all(steps)


def _announce_account(username: str) -> None:
    """Name a throwaway account in pytest's warnings summary before creating it."""
    with warnings.catch_warnings():
        # Always shown: under -W error this warning would otherwise raise.
        warnings.simplefilter("always", DisposableAccountWarning)
        warnings.warn(
            f"creating disposable local user {username}",
            DisposableAccountWarning,
            stacklevel=3,
        )


def create_workroom_user(
    admin: KamiwazaClient, client_factory: ClientFactory, base_url: str
) -> WorkroomUser:
    """Create a local ``user``-role account and sign in as it.

    ``subject`` is the Keycloak ``sub``, which is what workrooms record as
    ``owner_user_id``; ``id`` is the local account id the admin routes take.
    The account is read back once created (the control for the absence proof
    at teardown), and the signed-in identity must not hold the admin role, so a
    test's non-admin claim does not depend on the server's default roles.
    No fixture has yielded while this runs, so any failure ends a login it
    obtained, removes the account (found by username only if its create call
    failed, since the server may have acted), and proves it gone before the
    error propagates. If that cleanup fails too, its error is raised from the
    setup error, so both are reported.
    """
    username = f"{USERNAME_PREFIX}{uuid.uuid4().hex[:NAME_SUFFIX_HEX]}"
    password = _password()
    _announce_account(username)
    request = LocalUserCreateRequest(
        username=username,
        email=f"{username}@example.invalid",
        password=password,
        roles=["user"],
    )
    created_id: UUID | None = None
    logout: Step | None = None
    open_clients: list[KamiwazaClient] = []
    try:
        # Lambdas, not functools.partial: a partial's repr shows its arguments,
        # and --full-trace renders _withheld's hidden frame, `call` included.
        created = _withheld(
            "creating a local user", lambda: admin.auth.create_local_user(request)
        )
        created_id = created.id
        admin.auth.get_user(created.id)
        login_client = _unauthenticated(client_factory(base_url=base_url))
        open_clients.append(login_client)
        token: TokenResponse = _withheld(
            "password login",
            lambda: login_client.auth.login_with_password(username, password),
        )
        # The login client can end this login until the signed-in client exists.
        logout = _logout_step(login_client, token.refresh_token)
        if not token.access_token.strip():
            # A falsy api_key sends the factory back to KAMIWAZA_API_KEY, so an
            # empty token would build the shared administrator's client.
            raise AssertionError(f"the login for {username} returned no access token")
        # Withheld: the factory's frame would render the access token argument.
        client = _withheld(
            "building a client for the login",
            lambda: client_factory(base_url=base_url, api_key=token.access_token),
        )
        open_clients.append(client)
        logout = _logout_step(client, token.refresh_token)
        login_client.close()
        open_clients.remove(login_client)
        if token.expires_in < MIN_TOKEN_LIFETIME_SECONDS:
            raise AssertionError(
                f"disposable user {username}'s token expires in {token.expires_in} s, "
                f"under the {MIN_TOKEN_LIFETIME_SECONDS} s a run can need"
            )
        identity = client.auth.get_current_user()
        if "admin" in identity.roles:
            raise AssertionError(
                f"disposable user {username} signed in as an administrator"
            )
        if "user" not in identity.roles:
            # The SDK defaults an omitted roles list to [], which would pass
            # the check above without the server saying anything.
            raise AssertionError(
                f"disposable user {username} signed in without the user role "
                "it was created with"
            )
        return WorkroomUser(
            id=created.id,
            username=username,
            subject=identity.sub,
            client=client,
            refresh_token=token.refresh_token,
        )
    except BaseException as setup_error:
        try:
            _remove_failed_setup(
                admin,
                username,
                created_id,
                logout,
                open_clients,
                declined=declined_by_the_server(setup_error),
            )
        except BaseException as cleanup_error:
            if not isinstance(cleanup_error, CleanupError):
                # An interrupt inside cleanup: it still stops the run, but the
                # setup failure that brought us here has to stay visible.
                raise cleanup_error from _with_setup_failure(
                    cleanup_error.__cause__, setup_error
                )
            if not isinstance(setup_error, Exception):
                # A Ctrl-C must still stop the run. ``from`` is what makes the
                # cleanup failure visible: _withheld raises the interrupt with
                # ``from None``, so as mere context it would be suppressed and
                # the resource left behind would never be named.
                raise setup_error from cleanup_error
            # The setup error is the root cause; a withheld cleanup failure
            # suppresses the context that would otherwise show it.
            raise cleanup_error from setup_error
        raise


def delete_workroom_user(admin: KamiwazaClient, user: WorkroomUser) -> None:
    """Run what the user owns, log it out, delete the account, prove it is gone.

    The registered cleanups run first, while the login still works. The logout
    sends the login's refresh token; a login without one is reported as a
    failure rather than skipped. Every step runs even if an earlier one fails,
    and every failure is reported.
    """
    steps = [
        *user.cleanups,
        _logout_step(user.client, user.refresh_token),
        *_account_removal_steps(admin, user.username, user.id),
        ("close the disposable user's client", user.client.close),
    ]
    attempt_all(steps)


def session_user(
    request: pytest.FixtureRequest,
    admin: KamiwazaClient,
    client_factory: ClientFactory,
    base_url: str,
) -> Iterator[WorkroomUser]:
    """Body of a ``workroom_user`` fixture: create, yield, delete.

    The delete sits in ``finally`` so that a Ctrl-C arriving at the ``yield``,
    before pytest has registered the fixture's teardown, still removes the user.
    """
    refuse_credential_echo(request.config)
    user = create_workroom_user(admin, client_factory, base_url)
    try:
        yield user
    finally:
        delete_workroom_user(admin, user)
