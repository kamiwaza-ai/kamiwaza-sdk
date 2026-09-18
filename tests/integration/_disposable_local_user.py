"""Disposable local users for the live auth evidence tests (ENG-12326).

Each user is created through the admin API with a random password. Three
measures keep passwords and tokens out of the output these helpers control:

- dataclass fields excluded from reprs keep them out of assertion output;
- the helpers that send a password or a refresh token, or receive a new token
  (create, password login, password reset, refresh, logout, PAT minting),
  re-raise a failure without its context,
  naming only the operation, the error type, and the HTTP status, and suppress
  the SDK client's log lines for that call. The SDK's own message can carry the
  credential: ``AuthService.refresh_access_token`` sends the refresh token in
  the query string, and a transport error names the URL. Response validation
  errors are treated the same way, because they can echo the values;
- helpers that receive a password as an argument hide their frames from pytest
  tracebacks.

They do not control pytest's own output options, and these tests share the
rest of the live suite's exposure to them: the default ``--tb=auto`` and
``--tb=long`` render frame arguments, including SDK request headers and bodies;
``--showlocals`` renders locals; ``--fulltrace`` ignores traceback hiding; and
DEBUG logging for ``kamiwaza_sdk.client`` or ``urllib3`` prints headers and
URLs. The live suite's own autouse HTTP tracer (``KAMIWAZA_HTTP_TRACE`` /
``KAMIWAZA_HTTP_TRACE_FILE``, ``tests/integration/conftest.py``) wraps
``requests.Session.send`` and writes the request URL, its headers and body, and
the response head, all verbatim, so it records passwords, bearer and refresh
tokens (including the one ``refresh_access_token`` puts in the query string),
and minted PATs; it is off unless either variable is set. Run evidence captures with ``--tb=short``, default log levels and no HTTP
trace, and check their logs for credentials before committing them.

Creating an account is opt-in through ``DISPOSABLE_IDENTITIES_ENV``. An account
a run creates may persist after the run, and its password is not recorded
anywhere. Each username is emitted as a ``LeftoverIdentityWarning``, which
pytest reports whatever the test outcome.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import string
import uuid
import warnings
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import AuthenticationError, KamiwazaError, NotFoundError
from kamiwaza_sdk.schemas.auth import (
    LocalUserCreateRequest,
    LocalUserPasswordResetRequest,
    PATCreate,
    PATCreateResponse,
    TokenResponse,
)
from pydantic import ValidationError

USERNAME_PREFIX = "sdk-evidence-"
DISPOSABLE_IDENTITIES_ENV = "KAMIWAZA_TEST_DISPOSABLE_IDENTITIES"
SDK_CLIENT_LOGGER = "kamiwaza_sdk.client"
# A failed call that sends a credential raises one of these; both can carry it.
CREDENTIAL_CALL_ERRORS = (KamiwazaError, ValidationError)

ClientFactory = Callable[..., KamiwazaClient]

requires_disposable_identities = pytest.mark.skipif(
    os.environ.get(DISPOSABLE_IDENTITIES_ENV, "").strip() != "1",
    reason=(
        "creates local accounts that may persist after the run; "
        f"set {DISPOSABLE_IDENTITIES_ENV}=1 where that is acceptable"
    ),
)


class LeftoverIdentityWarning(UserWarning):
    """Names a disposable account that may persist after the run."""


class CredentialRequestError(RuntimeError):
    """An SDK call carrying a credential failed; the SDK's message is withheld."""


class CredentialRejected(CredentialRequestError):
    """The platform rejected the credential (the SDK raised AuthenticationError)."""


@dataclass(frozen=True)
class Login:
    """Tokens from one grant, kept out of reprs and assertion output."""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)

    @property
    def claims(self) -> dict[str, Any]:
        """Unverified access-token claims; identifiers only, never trusted."""
        payload = self.access_token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        return dict(json.loads(base64.urlsafe_b64decode(padded)))

    def require_refresh_token(self) -> str:
        token = self.refresh_token
        if token is None:
            pytest.fail("the grant returned no refresh token")
        assert token is not None  # narrows for type checkers; pytest.fail raised
        return token


@dataclass
class DisposableUser:
    """A local user created by this run, and every login opened for it."""

    id: UUID
    username: str
    email: str
    password: str = field(repr=False)
    logins: list[Login] = field(default_factory=list, repr=False)


@contextmanager
def _sdk_client_log_suppressed() -> Iterator[None]:
    client_logger = logging.getLogger(SDK_CLIENT_LOGGER)
    previously_disabled = client_logger.disabled
    client_logger.disabled = True
    try:
        yield
    finally:
        client_logger.disabled = previously_disabled


def _withheld(operation: str, exc: Exception) -> CredentialRequestError:
    status = getattr(exc, "status_code", None)
    message = (
        f"{operation} failed with {type(exc).__name__} (HTTP status {status}); "
        "the SDK message is withheld because it can carry the credential"
    )
    if isinstance(exc, AuthenticationError):
        return CredentialRejected(message)
    return CredentialRequestError(message)


def _warn_leftover(message: str) -> None:
    # Shown even under -W error: raised during fixture setup after the account
    # exists, the warning would abort setup and teardown would never run.
    with warnings.catch_warnings():
        warnings.simplefilter("always", LeftoverIdentityWarning)
        warnings.warn(message, LeftoverIdentityWarning, stacklevel=3)


def random_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "Kz7!" + "".join(secrets.choice(alphabet) for _ in range(24))


def create_disposable_user(admin: KamiwazaClient) -> DisposableUser:
    __tracebackhide__ = True  # the password it generates must not be rendered
    username = f"{USERNAME_PREFIX}{uuid.uuid4().hex[:12]}"
    email = f"{username}@example.invalid"
    password = random_password()
    try:
        with _sdk_client_log_suppressed():
            created = admin.auth.create_local_user(
                LocalUserCreateRequest(
                    username=username, email=email, password=password
                )
            )
    except CREDENTIAL_CALL_ERRORS as exc:
        # The account may exist even though the call failed, so report it the
        # same way as an account that was created.
        _warn_leftover(
            f"creating disposable local user {username} failed; it may exist"
        )
        raise _withheld(f"creating local user {username}", exc) from None
    _warn_leftover(f"created disposable local user {username} (id {created.id})")
    return DisposableUser(
        id=created.id, username=username, email=email, password=password
    )


def access_token_client(
    client_factory: ClientFactory, base_url: str, username: str, password: str
) -> KamiwazaClient:
    """Sign in by password grant; return a client that sends only the access token.

    The client holds no refresh token or login cookies, so it never refreshes,
    and it neither reads nor writes the token cache file. The session is not
    logged out: a v1.2.1 logout also tries to purge every active ephemeral
    workroom the user owns, and this helper signs in the operator.
    """
    __tracebackhide__ = True  # its password argument must not be rendered
    client = client_factory(base_url=base_url)
    try:
        with _sdk_client_log_suppressed():
            token = client.auth.login_with_password(username, password)
    except CREDENTIAL_CALL_ERRORS as exc:
        raise _withheld("password login", exc) from None
    return client_factory(base_url=base_url, api_key=token.access_token)


def mint_pat(client: KamiwazaClient, payload: PATCreate) -> PATCreateResponse:
    """Create a PAT; a failure is re-raised without the SDK message.

    A response that fails to parse or validate can echo the new token. The PAT
    may still have been created in that case; minted PATs expire with their TTL.
    """
    try:
        with _sdk_client_log_suppressed():
            return client.auth.create_pat(payload)
    except CREDENTIAL_CALL_ERRORS as exc:
        raise _withheld("creating a PAT", exc) from None


def password_login(
    client_factory: ClientFactory,
    base_url: str,
    user: DisposableUser,
    password: str | None = None,
) -> Login:
    """Sign in as the user and record the login so teardown can end it."""
    __tracebackhide__ = True  # its password argument must not be rendered
    client = client_factory(base_url=base_url)
    try:
        with _sdk_client_log_suppressed():
            token = client.auth.login_with_password(
                user.username, user.password if password is None else password
            )
    except CREDENTIAL_CALL_ERRORS as exc:
        raise _withheld("password login", exc) from None
    login = Login(access_token=token.access_token, refresh_token=token.refresh_token)
    user.logins.append(login)
    return login


def password_login_succeeds(
    client_factory: ClientFactory,
    base_url: str,
    user: DisposableUser,
    password: str,
) -> bool:
    __tracebackhide__ = True  # its password argument must not be rendered
    try:
        password_login(client_factory, base_url, user, password)
    except CredentialRejected:
        return False
    return True


def reset_password(admin: KamiwazaClient, user: DisposableUser) -> str:
    """Reset the user's password to a new random one; return the replaced one."""
    __tracebackhide__ = True  # both passwords are live and must not be rendered
    replaced = user.password
    new_password = random_password()
    try:
        with _sdk_client_log_suppressed():
            admin.auth.reset_user_password(
                user.id, LocalUserPasswordResetRequest(new_password=new_password)
            )
    except CREDENTIAL_CALL_ERRORS as exc:
        raise _withheld("resetting the password", exc) from None
    user.password = new_password
    return replaced


def authenticates(client: KamiwazaClient) -> bool:
    try:
        client.auth.get_current_user()
    except AuthenticationError:
        return False
    return True


def _refresh(
    client_factory: ClientFactory, base_url: str, login: Login
) -> TokenResponse | None:
    """Refresh the login once; ``None`` when its refresh token is rejected."""
    client = client_factory(base_url=base_url)
    try:
        with _sdk_client_log_suppressed():
            return client.auth.refresh_access_token(login.require_refresh_token())
    except AuthenticationError:
        return None
    except CREDENTIAL_CALL_ERRORS as exc:
        raise _withheld("token refresh", exc) from None


def refresh_succeeds(
    client_factory: ClientFactory,
    base_url: str,
    user: DisposableUser,
    login: Login,
) -> bool:
    """Try the login's refresh token, recording a refreshed login for teardown.

    A refresh can rotate the refresh token; the recorded pair is what lets
    teardown end the session it continues.
    """
    token = _refresh(client_factory, base_url, login)
    if token is None:
        return False
    user.logins.append(
        Login(access_token=token.access_token, refresh_token=token.refresh_token)
    )
    return True


def logout_with_refresh_token(
    client_factory: ClientFactory, base_url: str, login: Login
) -> None:
    """Ask the platform to end the login's Keycloak session.

    This helper sends the refresh token in the request body. ``AuthService.logout()``
    on a client that holds only an access token sends none. A success response
    does not prove the session ended, so callers that need the outcome try the
    refresh token afterwards.
    """
    client = client_factory(base_url=base_url, api_key=login.access_token)
    try:
        with _sdk_client_log_suppressed():
            client.post(
                "/auth/logout", json={"refresh_token": login.require_refresh_token()}
            )
    except CREDENTIAL_CALL_ERRORS as exc:
        raise _withheld("logout", exc) from None


def logout_access_token_only(
    client_factory: ClientFactory, base_url: str, login: Login
) -> None:
    """Call ``AuthService.logout()`` on a client holding only the access token.

    Unlike ``logout_with_refresh_token`` this sends whatever the SDK sends for
    a client built from an access token alone. The SDK client log is suppressed
    because the request carries that token in its headers.
    """
    client = client_factory(base_url=base_url, api_key=login.access_token)
    try:
        with _sdk_client_log_suppressed():
            client.auth.logout()
    except CREDENTIAL_CALL_ERRORS as exc:
        raise _withheld("logout", exc) from None


def user_is_readable(admin: KamiwazaClient, user_id: UUID) -> bool:
    try:
        admin.auth.get_user(user_id)
    except NotFoundError:
        return False
    return True


def _end_logins(
    client_factory: ClientFactory,
    base_url: str,
    logins: Iterable[tuple[int, Login]],
) -> list[str]:
    """Log out each indexed login in the order given; return the problems."""
    problems: list[str] = []
    for index, login in logins:
        try:
            logout_with_refresh_token(client_factory, base_url, login)
        except CredentialRejected:
            # The SDK raises AuthenticationError for any 401, and this request
            # carries the access token, so a rejection here does not prove the
            # session ended. It is safe to pass over only because
            # _confirm_login_ended re-checks every login's refresh token below
            # and reports any that still works.
            continue
        except CredentialRequestError as exc:
            problems.append(f"login {index}: {exc}")
    return problems


def _confirm_login_ended(
    client_factory: ClientFactory, base_url: str, index: int, login: Login
) -> list[str]:
    """Try the login's refresh token; end the session again if it still works."""
    try:
        refreshed = _refresh(client_factory, base_url, login)
    except CredentialRequestError as exc:
        return [f"checking login {index}: {exc}"]
    if refreshed is None:
        return []
    problems = [f"login {index} still refreshed after logout"]
    # The check itself continued the session, possibly with a rotated token,
    # so end it with the tokens the refresh returned.
    retry = Login(
        access_token=refreshed.access_token, refresh_token=refreshed.refresh_token
    )
    if retry.refresh_token is None:
        return problems
    try:
        logout_with_refresh_token(client_factory, base_url, retry)
    except CredentialRequestError as exc:
        problems.append(f"retrying the logout of login {index}: {exc}")
    return problems


def _delete_user(admin: KamiwazaClient, user: DisposableUser) -> list[str]:
    """Delete the user if readable, then check it is gone; return the problems."""
    problems: list[str] = []
    try:
        readable = user_is_readable(admin, user.id)
    except CREDENTIAL_CALL_ERRORS as exc:
        problems.append(f"reading the user raised {type(exc).__name__}: {exc}")
        readable = True  # state unknown, so the delete is still attempted
    try:
        if readable:
            admin.auth.delete_user(user.id)
        if user_is_readable(admin, user.id):
            problems.append("the user is still readable after deletion")
    except CREDENTIAL_CALL_ERRORS as exc:
        problems.append(f"deleting the user raised {type(exc).__name__}: {exc}")
    return problems


def tear_down(
    admin: KamiwazaClient,
    client_factory: ClientFactory,
    base_url: str,
    user: DisposableUser,
) -> None:
    """End every recorded login, prove each ended, then delete the user.

    Logins are ended newest first, since after a rotating refresh only the
    newest refresh token is still valid. Every step is attempted, and all the
    problems found are reported together at the end.
    """
    refreshable = [
        (index, login)
        for index, login in enumerate(user.logins)
        if login.refresh_token is not None
    ]
    problems = _end_logins(client_factory, base_url, reversed(refreshable))
    for index, login in refreshable:
        problems += _confirm_login_ended(client_factory, base_url, index, login)
    problems += _delete_user(admin, user)
    if problems:
        pytest.fail(f"teardown of {user.username}: " + "; ".join(problems))


def disposable_user_lifecycle(
    admin: KamiwazaClient,
    client_factory: ClientFactory,
    base_url: str,
) -> Iterator[DisposableUser]:
    """Body of a ``disposable_local_user`` fixture: create, yield, tear down."""
    user = create_disposable_user(admin)
    yield user
    tear_down(admin, client_factory, base_url, user)
