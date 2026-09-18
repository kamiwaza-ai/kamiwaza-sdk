"""Credential handling and cleanup in the ENG-12325 disposable workroom user helper.

The live workroom tests sign in as a disposable user on a shared deployment.
These tests pin, without a deployment, that a failing call carrying the
password or a token cannot print it (in the exception message, or in a
``--tb=long`` or ``--full-trace`` traceback, including when setup fails after
login and its cleanup fails too), that a partly created account is removed and
proven gone, and that teardown attempts every step and reports every failure.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.auth import LocalUserResponse, TokenResponse, UserInfo
from tests.integration import _workroom_disposable_user as disposable
from tests.integration import _workroom_support as support

pytestmark = pytest.mark.unit

_REAL_ANNOUNCE_ACCOUNT = disposable._announce_account


@pytest.fixture(autouse=True)
def _no_fake_account_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The accounts here are fakes, so none should be named in the warnings summary."""
    monkeypatch.setattr(disposable, "_announce_account", lambda _username: None)


# Short enough that pydantic does not truncate it inside its input_value repr.
SECRET_MARKER = "sekret42"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _sdk_error() -> APIError:
    return APIError(f"request failed: {SECRET_MARKER}", status_code=400)


def _token_validation_error() -> Exception:
    """The error the SDK raises when a login response lacks ``expires_in``."""
    try:
        TokenResponse.model_validate(
            {"access_token": SECRET_MARKER, "refresh_token": SECRET_MARKER}
        )
    except Exception as exc:  # noqa: BLE001 - capture pydantic's real error
        return exc
    raise AssertionError("the malformed token response validated")


class _AdminAuth:
    """Admin-side account calls against an in-memory user store."""

    def __init__(
        self,
        *,
        create_error: Exception | None = None,
        store_before_create_error: bool = False,
        delete_applies: bool = True,
        delete_error: Exception | None = None,
    ) -> None:
        self.create_error = create_error
        self.store_before_create_error = store_before_create_error
        self.delete_applies = delete_applies
        self.delete_error = delete_error
        self.users: dict[UUID, LocalUserResponse] = {}
        self.requested_roles: list[object] = []
        self.deleted: list[UUID] = []
        self.readbacks: list[UUID] = []
        self.listings = 0

    def create_local_user(self, payload):
        self.requested_roles.append(payload.roles)
        if self.create_error is not None and not self.store_before_create_error:
            raise self.create_error
        created = LocalUserResponse(
            id=uuid4(),
            username=payload.username,
            active=True,
            deleted=False,
            is_external=False,
        )
        self.users[created.id] = created
        if self.create_error is not None:
            raise self.create_error
        return created

    def list_users(self):
        self.listings += 1
        return [user for user in self.users.values() if not user.deleted]

    def get_user(self, user_id):
        self.readbacks.append(user_id)
        user = self.users.get(user_id)
        if user is None or user.deleted:
            raise NotFoundError("User not found")
        return user

    def delete_user(self, user_id):
        self.deleted.append(user_id)
        if self.delete_error is not None:
            raise self.delete_error
        if self.delete_applies:
            self.users[user_id] = self.users[user_id].model_copy(
                update={"deleted": True}
            )
        return self.users[user_id]


class _Admin:
    def __init__(self, auth: _AdminAuth) -> None:
        self.auth = auth


class _UserClient:
    def __init__(
        self,
        *,
        # BaseException: the helper must handle a stop signal from these calls
        # as well as a failure, and a test raises each.
        login_error: BaseException | None = None,
        whoami_error: BaseException | None = None,
        logout_error: BaseException | None = None,
        access_token: str = "access-token",
        refresh_token: str = "refresh",
        expires_in: int = 300,
        roles: tuple[str, ...] = ("offline_access", "user"),
    ) -> None:
        self.roles = list(roles)
        self.access_token = access_token
        self.expires_in = expires_in
        self.refresh_token = refresh_token
        self.login_error = login_error
        self.whoami_error = whoami_error
        self.logout_error = logout_error
        self.logger_disabled_during_login: bool | None = None
        # A real client built without an api_key adopts KAMIWAZA_API_KEY from
        # the environment; the sentinel stands in for that ambient credential.
        self.authenticator = "ambient-credential"
        self.auth = self
        self.posted: list[str] = []
        self.bodies: list[dict] = []
        self.closed = 0

    def login_with_password(self, _username, _password):
        self.logger_disabled_during_login = logging.getLogger(
            disposable.SDK_CLIENT_LOGGER
        ).disabled
        if self.login_error is not None:
            raise self.login_error
        return TokenResponse(
            access_token=self.access_token,
            refresh_token=self.refresh_token,
            expires_in=self.expires_in,
        )

    def get_current_user(self):
        if self.whoami_error is not None:
            raise self.whoami_error
        return UserInfo(username="u", sub="subject-id", roles=self.roles)

    def post(self, path, **body):
        self.posted.append(path)
        self.bodies.append(body)
        if self.logout_error is not None:
            raise self.logout_error

    def close(self):
        self.closed += 1


def _factory(client: _UserClient):
    return lambda **_: client


def _create(auth: _AdminAuth, client: _UserClient) -> disposable.WorkroomUser:
    return disposable.create_workroom_user(_Admin(auth), _factory(client), "http://x")


def test_create_failure_withholds_the_sdk_message() -> None:
    # The server declined with 400, so nothing was created and cleanup has
    # nothing to report: the withheld setup error is what surfaces.
    auth = _AdminAuth(create_error=_sdk_error())

    with pytest.raises(disposable.CredentialRequestError) as raised:
        _create(auth, _UserClient())

    assert SECRET_MARKER not in str(raised.value)
    assert "HTTP status 400" in str(raised.value)
    assert raised.value.__suppress_context__ is True
    assert raised.value.__cause__ is None


def test_an_account_created_before_its_create_call_failed_is_found_and_removed() -> (
    None
):
    auth = _AdminAuth(create_error=_sdk_error(), store_before_create_error=True)

    with pytest.raises(disposable.CredentialRequestError):
        _create(auth, _UserClient())

    (orphan,) = auth.users
    assert auth.deleted == [orphan]
    assert auth.readbacks == [orphan], "the removal was not proven"


@pytest.mark.parametrize("login_error", [_sdk_error(), _token_validation_error()])
def test_login_failure_withholds_and_removes_the_account(login_error) -> None:
    auth = _AdminAuth()

    with pytest.raises(disposable.CredentialRequestError) as raised:
        _create(auth, _UserClient(login_error=login_error))

    assert SECRET_MARKER not in str(raised.value)
    assert raised.value.__suppress_context__ is True
    assert raised.value.__cause__ is None
    (account,) = auth.users
    assert auth.deleted == [account]
    # Read back once when created, then once by the absence proof.
    assert auth.readbacks == [account, account], "the removal was not proven"


def test_a_failure_after_login_removes_the_account() -> None:
    auth = _AdminAuth()
    whoami_error = APIError("users/me unavailable", status_code=503)

    with pytest.raises(APIError) as raised:
        _create(auth, client := _UserClient(whoami_error=whoami_error))

    assert raised.value is whoami_error
    (account,) = auth.users
    assert auth.deleted == [account]
    assert auth.listings == 0, "the known account id was not used"
    assert client.posted == ["/auth/logout"], "the obtained login was not ended"


def test_a_cleanup_failure_after_a_failed_setup_is_raised_from_the_setup_error() -> (
    None
):
    """The setup error is the root cause; a withheld cleanup must not hide it."""
    auth = _AdminAuth()
    whoami_error = APIError("users/me unavailable", status_code=503)

    with pytest.raises(support.CleanupError) as raised:
        _create(auth, _UserClient(whoami_error=whoami_error, logout_error=_sdk_error()))

    assert raised.value.__cause__ is whoami_error
    assert "log the disposable user out" in str(raised.value)
    assert SECRET_MARKER not in str(raised.value)
    (account,) = auth.users
    assert auth.deleted == [account]


def test_an_interrupted_setup_still_interrupts_when_its_cleanup_fails() -> None:
    """A Ctrl-C must still stop the run; the cleanup failure is only its context."""
    auth = _AdminAuth()

    with pytest.raises(KeyboardInterrupt) as raised:
        _create(
            auth,
            _UserClient(whoami_error=KeyboardInterrupt(), logout_error=_sdk_error()),
        )

    assert isinstance(raised.value.__context__, support.CleanupError)
    (account,) = auth.users
    assert auth.deleted == [account]


def test_the_login_client_is_closed_and_teardown_closes_the_users_client() -> None:
    auth = _AdminAuth()
    login_client, user_client = _UserClient(), _UserClient()

    def factory(*, base_url, api_key=None):
        return login_client if api_key is None else user_client

    user = disposable.create_workroom_user(_Admin(auth), factory, "http://x")
    assert (login_client.closed, user_client.closed) == (1, 0)

    disposable.delete_workroom_user(_Admin(auth), user)

    assert user_client.closed == 1
    assert user_client.posted == ["/auth/logout"]
    # Logout sends the login's refresh token.
    assert user_client.bodies == [{"json": {"refresh_token": "refresh"}}]


def test_an_interrupt_during_a_credential_call_does_not_carry_its_frames() -> None:
    """The interrupt is re-raised fresh, so no frame of the credential call renders."""

    def credential_call() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt) as raised:
        disposable._withheld("password login", credential_call)

    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert "credential_call" not in {entry.name for entry in raised.traceback}


def test_a_login_whose_token_expires_before_a_run_can_finish_is_refused() -> None:
    auth = _AdminAuth()

    with pytest.raises(AssertionError, match="expires in 60 s"):
        _create(auth, client := _UserClient(expires_in=60))

    (account,) = auth.users
    assert auth.deleted == [account]
    assert client.posted == ["/auth/logout"]


def test_sdk_client_logging_is_off_during_a_credential_call_and_restored() -> None:
    client = _UserClient()
    client_logger = logging.getLogger(disposable.SDK_CLIENT_LOGGER)
    assert client_logger.disabled is False

    _create(_AdminAuth(), client)

    assert client.logger_disabled_during_login is True
    assert client_logger.disabled is False


def test_the_account_warning_names_the_account_under_error_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(disposable, "_announce_account", _REAL_ANNOUNCE_ACCOUNT)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("error")
        user = _create(_AdminAuth(), _UserClient())

    assert user.subject == "subject-id"
    assert [w.category for w in seen] == [disposable.DisposableAccountWarning]
    assert user.username in str(seen[0].message)


def test_repr_omits_the_client_and_refresh_token() -> None:
    user = _create(_AdminAuth(), _UserClient())

    assert "refresh" not in repr(user)
    assert "client" not in repr(user)


def test_logout_failure_still_deletes_proves_absence_and_withholds() -> None:
    auth = _AdminAuth()
    user = _create(auth, _UserClient(logout_error=_sdk_error()))

    with pytest.raises(support.CleanupError) as raised:
        disposable.delete_workroom_user(_Admin(auth), user)

    assert SECRET_MARKER not in str(raised.value)
    assert auth.deleted == [user.id]
    # Read back once at creation, then once by the absence proof.
    assert auth.readbacks == [user.id, user.id], "the absence proof did not run"


def test_a_failed_delete_is_still_followed_by_the_absence_proof() -> None:
    auth = _AdminAuth(delete_error=APIError("delete failed", status_code=500))
    user = _create(auth, _UserClient())

    with pytest.raises(support.CleanupError) as raised:
        disposable.delete_workroom_user(_Admin(auth), user)

    assert auth.readbacks == [user.id, user.id]
    assert "still readable" in str(raised.value)


def test_teardown_fails_when_the_account_is_still_readable() -> None:
    auth = _AdminAuth(delete_applies=False)
    user = _create(auth, _UserClient())

    with pytest.raises(support.CleanupError, match="still readable"):
        disposable.delete_workroom_user(_Admin(auth), user)


@pytest.mark.parametrize("trace_option", ["--tb=long", "--full-trace"])
def test_a_long_traceback_does_not_render_a_credential(
    tmp_path, trace_option: str
) -> None:
    """``--tb=long`` renders every frame argument's repr.

    ``--full-trace`` also renders the frames ``__tracebackhide__`` hides. The
    probe pins the generated password to a known value and gives the login known
    tokens, then fails the create, the login, a malformed login response, the
    logout, building the signed-in client, setup after login followed by a
    failing cleanup step, and a login that raises ``SystemExit``. None of
    the SDK message, the password, or the tokens may appear in the rendered
    output, and each setup failure must still be reported.
    """
    probe = tmp_path / "test_probe_credential_traceback.py"
    probe.write_text(
        textwrap.dedent(
            """
            from tests.integration import _workroom_disposable_user as disposable
            from tests.unit import test_workroom_disposable_user as unit

            disposable.secrets.choice = lambda alphabet: "Q"


            def test_create_fails():
                unit._create(
                    unit._AdminAuth(create_error=unit._sdk_error()),
                    unit._UserClient(),
                )


            def test_login_fails():
                unit._create(
                    unit._AdminAuth(),
                    unit._UserClient(login_error=unit._sdk_error()),
                )


            def test_login_response_is_malformed():
                unit._create(
                    unit._AdminAuth(),
                    unit._UserClient(login_error=unit._token_validation_error()),
                )


            def test_logout_fails():
                auth = unit._AdminAuth()
                user = unit._create(
                    auth,
                    unit._UserClient(
                        logout_error=unit._sdk_error(),
                        refresh_token=unit.SECRET_MARKER,
                    ),
                )
                disposable.delete_workroom_user(unit._Admin(auth), user)


            def test_setup_fails_after_login_then_logout_fails():
                unit._create(
                    unit._AdminAuth(),
                    unit._UserClient(
                        whoami_error=unit.APIError(
                            "setup-original-503", status_code=503
                        ),
                        logout_error=unit._sdk_error(),
                        access_token=unit.SECRET_MARKER,
                        refresh_token=unit.SECRET_MARKER,
                    ),
                )


            def test_admin_role_is_refused_then_delete_fails():
                unit._create(
                    unit._AdminAuth(
                        delete_error=unit.APIError("delete broke", status_code=500)
                    ),
                    unit._UserClient(
                        roles=("admin",),
                        access_token=unit.SECRET_MARKER,
                        refresh_token=unit.SECRET_MARKER,
                    ),
                )


            def test_login_exits_the_run():
                # A SystemExit stops the run rather than failing it, and must
                # not carry the frames of the call it stopped.
                unit._create(
                    unit._AdminAuth(),
                    unit._UserClient(login_error=SystemExit(3)),
                )


            def test_building_the_client_fails_after_login():
                login_client = unit._UserClient(access_token=unit.SECRET_MARKER)

                def factory(base_url, api_key=None):
                    if api_key is not None:
                        raise RuntimeError("client construction broke")
                    return login_client

                disposable.create_workroom_user(
                    unit._Admin(unit._AdminAuth()), factory, "http://x"
                )
            """
        )
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(probe),
            trace_option,
            "-p",
            "no:cacheprovider",
            "-q",
            "--rootdir",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"},
    )
    output = completed.stdout + completed.stderr

    assert "8 failed" in output, output[-2000:]
    if trace_option == "--tb=long":
        # __tracebackhide__ keeps _withheld's own frame out of the traceback,
        # which renders as the frame's own source: its def line and `return
        # call()`. --full-trace shows hidden frames, so this is --tb=long only.
        assert "return call()" not in output, "_withheld's frame was rendered"
    assert output.count("CredentialRequestError") >= 4
    assert SECRET_MARKER not in output
    assert "Q" * disposable.PASSWORD_LENGTH not in output
    reported = [line for line in output.splitlines() if line.startswith("E ")]
    assert any("setup-original-503" in line for line in reported), output[-3000:]
    assert any("signed in as an administrator" in line for line in reported)


@pytest.mark.parametrize("trace_option", ["--full-trace", "--pdb"])
def test_an_interrupted_login_does_not_print_the_password(
    tmp_path, trace_option: str
) -> None:
    """Both options render a Ctrl-C's traceback with frame arguments."""
    probe = tmp_path / "test_probe_interrupted_login.py"
    probe.write_text(
        textwrap.dedent(
            """
            from tests.integration import _workroom_disposable_user as disposable
            from tests.unit import test_workroom_disposable_user as unit

            disposable.secrets.choice = lambda alphabet: "Q"


            def test_login_is_interrupted():
                unit._create(
                    unit._AdminAuth(),
                    unit._UserClient(login_error=KeyboardInterrupt()),
                )
            """
        )
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(probe),
            trace_option,
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"},
    )
    output = completed.stdout + completed.stderr

    assert "KeyboardInterrupt" in output, output[-2000:]
    assert "Q" * disposable.PASSWORD_LENGTH not in output


_TRACE_ENV = {
    "http_trace": ("KAMIWAZA_HTTP_TRACE", "1"),
    "http_trace_file": ("KAMIWAZA_HTTP_TRACE_FILE", "/tmp/kamiwaza-trace.jsonl"),
}


@pytest.fixture
def untraced(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name, _value in _TRACE_ENV.values():
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# What the skip reason must name, so a refusal cannot report the wrong cause.
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
    config = _Config(**({cause: True} if cause in _Config.OPTIONS else {}))
    level = logging.DEBUG if cause == "debug" else logging.INFO
    caplog.set_level(level, logger=disposable.SDK_CLIENT_LOGGER)
    if cause in _TRACE_ENV:
        untraced.setenv(*_TRACE_ENV[cause])

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
    config = _Config()

    try:
        disposable.refuse_credential_echo(config)
    except pytest.skip.Exception as refused:
        raise AssertionError(f"a safe run was refused: {refused}") from None

    assert set(_Config.OPTIONS) <= set(config.asked), (
        f"the refusal never read {set(_Config.OPTIONS) - set(config.asked)}"
    )


class _Config:
    OPTIONS = ("showlocals", "fulltrace", "usepdb", "trace")

    def __init__(self, **enabled: bool) -> None:
        self.options = {name: enabled.get(name, False) for name in self.OPTIONS}
        self.asked: list[str] = []

    def getoption(self, name, default=None):
        self.asked.append(name)
        return self.options.get(name, default)


def test_the_account_is_created_with_the_user_role_and_read_back() -> None:
    auth = _AdminAuth()

    user = _create(auth, _UserClient())

    assert auth.requested_roles == [["user"]]
    assert auth.readbacks == [user.id], "the created account was not read back"


def test_an_account_that_signs_in_as_an_administrator_is_refused_and_removed() -> None:
    auth = _AdminAuth()

    with pytest.raises(AssertionError, match="administrator"):
        _create(auth, client := _UserClient(roles=("admin", "user")))

    (account,) = auth.users
    assert auth.deleted == [account]
    assert client.posted == ["/auth/logout"], "the admin-role session was left open"


def test_a_sign_in_whose_roles_do_not_include_user_is_refused_and_removed() -> None:
    """An absent roles list must not pass as "not an administrator"."""
    auth = _AdminAuth()

    with pytest.raises(AssertionError, match="user role"):
        _create(auth, client := _UserClient(roles=("offline_access",)))

    (account,) = auth.users
    assert auth.deleted == [account]
    assert client.posted == ["/auth/logout"]


def test_a_login_without_a_refresh_token_fails_teardown_loudly() -> None:
    auth = _AdminAuth()
    user = _create(auth, _UserClient())
    user.refresh_token = None

    with pytest.raises(support.CleanupError, match="refresh token"):
        disposable.delete_workroom_user(_Admin(auth), user)

    assert auth.deleted == [user.id], "the account was not deleted"


def test_session_user_refuses_an_echoing_run_before_creating_anything(
    untraced: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=disposable.SDK_CLIENT_LOGGER)
    auth = _AdminAuth()
    request = SimpleNamespace(config=_Config(showlocals=True))

    with pytest.raises(pytest.skip.Exception):
        next(
            disposable.session_user(
                request, _Admin(auth), _factory(_UserClient()), "http://x"
            )
        )

    assert auth.requested_roles == [], "an account was created before the refusal"


def test_an_interrupt_at_the_fixture_yield_still_deletes_the_user(
    untraced: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Ctrl-C can land before pytest registers the fixture's teardown."""
    # session_user refuses an echoing run; pin the logger so an ambient
    # --log-level=DEBUG cannot abort the whole unit session here.
    caplog.set_level(logging.INFO, logger=disposable.SDK_CLIENT_LOGGER)
    auth = _AdminAuth()
    request = SimpleNamespace(config=_Config())
    fixture = disposable.session_user(
        request, _Admin(auth), _factory(_UserClient()), "http://x"
    )
    user = next(fixture)

    with pytest.raises(KeyboardInterrupt):
        fixture.throw(KeyboardInterrupt())

    assert auth.deleted == [user.id]


def test_a_failed_setup_closes_the_signed_in_client() -> None:
    auth = _AdminAuth()
    login_client = _UserClient()
    user_client = _UserClient(
        whoami_error=APIError("users/me unavailable", status_code=503)
    )

    def factory(*, base_url, api_key=None):
        return login_client if api_key is None else user_client

    with pytest.raises(APIError):
        disposable.create_workroom_user(_Admin(auth), factory, "http://x")

    assert (login_client.closed, user_client.closed) == (1, 1)


def test_a_failed_client_build_logs_out_through_the_login_client() -> None:
    """The login client can still end the login the factory failed to re-use."""
    auth = _AdminAuth()
    login_client = _UserClient()

    def factory(*, base_url, api_key=None):
        if api_key is not None:
            raise RuntimeError("client construction broke")
        return login_client

    with pytest.raises(disposable.CredentialRequestError):
        disposable.create_workroom_user(_Admin(auth), factory, "http://x")

    assert login_client.posted == ["/auth/logout"]
    assert login_client.bodies == [{"json": {"refresh_token": "refresh"}}]
    assert login_client.closed == 1
    (account,) = auth.users
    assert auth.deleted == [account]


def test_the_token_floor_covers_the_bounded_waits_these_tests_make() -> None:
    """A slow run must not outlive the token the disposable user cannot refresh."""
    assert support.BOUNDED_WAIT_BUDGET_SECONDS <= disposable.MIN_TOKEN_LIFETIME_SECONDS


def test_a_local_user_no_listing_shows_is_named_as_unconfirmed() -> None:
    """The create's outcome is unknown and the listing shows nothing: say so.

    A 503 leaves it open whether the server acted, unlike the declined 4xx in
    the test below.
    """
    auth = _AdminAuth(create_error=APIError("upstream broke", status_code=503))

    with pytest.raises(support.CleanupError) as reported:
        _create(auth, _UserClient())

    # The create never stored an account, so the report's own text is the only
    # place the unique name can come from: a prefix alone would not tell two
    # concurrent runs' leftovers apart.
    message = str(reported.value)
    assert re.search(
        rf"{re.escape(disposable.USERNAME_PREFIX)}[0-9a-f]{{12}}", message
    ), f"the report does not name a unique account: {message}"
    assert isinstance(reported.value.__cause__, disposable.CredentialRequestError), (
        "the setup failure is not the cleanup report's cause"
    )

    assert auth.listings == 1, "the account was not looked for by name"
    assert auth.deleted == [], "an account was deleted without being found"


def test_the_registered_cleanups_run_before_the_login_is_logged_out() -> None:
    """One teardown for everything the user owns, while its login still works."""
    auth = _AdminAuth()
    client = _UserClient()
    user = _create(auth, client)
    order: list[str] = []
    user.cleanups.append(
        (
            "registered cleanup",
            lambda: order.append(f"cleanup, posts so far {client.posted}"),
        )
    )

    disposable.delete_workroom_user(_Admin(auth), user)

    assert order == ["cleanup, posts so far []"], "the logout ran before the cleanup"
    assert client.posted == ["/auth/logout"]
    assert auth.deleted == [user.id]


def test_a_registered_cleanup_that_fails_still_deletes_the_account() -> None:
    auth = _AdminAuth()
    user = _create(auth, _UserClient())

    def broken() -> None:
        raise APIError("the ledger could not finish", status_code=500)

    user.cleanups.append(("registered cleanup", broken))

    with pytest.raises(support.CleanupError, match="registered cleanup"):
        disposable.delete_workroom_user(_Admin(auth), user)

    assert auth.deleted == [user.id], "the account survived a failed ledger cleanup"


def test_an_interrupt_during_setup_shows_why_cleanup_failed() -> None:
    """The interrupt stops the run; the resource left behind must still be named."""
    auth = _AdminAuth(delete_error=_sdk_error())
    client = _UserClient()

    def interrupted_login(*_args, **_kwargs):
        raise KeyboardInterrupt

    client.login_with_password = interrupted_login  # type: ignore[method-assign]

    with pytest.raises(KeyboardInterrupt) as stopped:
        _create(auth, client)

    assert auth.deleted, "the account deletion was never attempted"

    assert isinstance(stopped.value.__cause__, support.CleanupError), (
        "the cleanup failure is not the interrupt's cause, so it is not rendered"
    )


def test_a_keyless_client_adopts_the_ambient_key_and_the_helper_drops_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control is the SDK's own fallback: if it stops, this test says so."""
    monkeypatch.setenv("KAMIWAZA_API_KEY", "shared-admin-pat")
    adopted = KamiwazaClient(base_url="http://x")
    assert adopted.authenticator is not None, (
        "the SDK no longer adopts an ambient key, so this guard is testing nothing"
    )

    assert disposable._unauthenticated(adopted).authenticator is None


def test_the_login_client_carries_no_ambient_credential() -> None:
    """The password grant, and any logout before the signed-in client exists."""
    auth = _AdminAuth()
    client = _UserClient()

    user = _create(auth, client)

    assert client.authenticator is None, (
        "the login client kept the shared administrator's bearer"
    )
    assert user.client is client


def test_a_declined_create_reports_no_account_to_look_for() -> None:
    """A 4xx means the server refused; an operator must not be sent after it."""
    auth = _AdminAuth(create_error=APIError("bad password", status_code=400))

    with pytest.raises(disposable.CredentialRequestError):
        _create(auth, _UserClient())

    assert auth.listings == 1, "the account was not looked for at all"


def test_the_generated_password_seeds_one_character_from_each_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic: one sampled password would satisfy this 97% of the time.

    A realm that requires a symbol refuses every create built from letters and
    digits alone, so what matters is the per-class seeding rather than a lucky
    draw. The shuffle is neutralised so the seeded characters keep class order.
    """
    monkeypatch.setattr(
        disposable.secrets,
        "SystemRandom",
        lambda: SimpleNamespace(shuffle=lambda _chars: None),
    )

    password = disposable._password()

    # A literal, not the constant: the credential probes build their needle
    # from PASSWORD_LENGTH, so a test that reads it too would not notice a
    # change that shortens both.
    assert disposable.PASSWORD_LENGTH == 32
    assert len(password) == 32
    seeded = password[: len(disposable._PASSWORD_CLASSES)]
    for character, alphabet in zip(seeded, disposable._PASSWORD_CLASSES, strict=True):
        assert character in alphabet, (
            f"{character!r} does not seed the class it should: {alphabet!r}"
        )


def test_a_login_with_no_access_token_is_refused_and_the_account_removed() -> None:
    """A falsy api_key sends the factory back to the ambient KAMIWAZA_API_KEY."""
    auth = _AdminAuth()

    with pytest.raises(AssertionError, match="no access token"):
        _create(auth, _UserClient(access_token="   "))

    (account,) = auth.users
    assert auth.deleted == [account], "the account outlived a login it cannot use"


def test_an_interrupt_in_cleanup_keeps_the_setup_failure_visible() -> None:
    """The interrupt stops the run; what brought us here must still be readable."""
    auth = _AdminAuth()
    client = _UserClient(whoami_error=APIError("setup broke", status_code=503))

    def interrupted_logout(*_args, **_kwargs):
        raise KeyboardInterrupt

    client.post = interrupted_logout  # type: ignore[method-assign]

    with pytest.raises(KeyboardInterrupt) as stopped:
        _create(auth, client)

    # Whatever its type -- whoami is not withheld, so its own error propagates
    # -- the failure that brought us here must be the interrupt's cause.
    assert "setup broke" in str(stopped.value.__cause__), (
        f"the setup failure is not rendered: {stopped.value.__cause__!r}"
    )
