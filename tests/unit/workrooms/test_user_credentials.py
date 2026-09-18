"""Nothing a disposable account is created with may reach the output.

A failing call that carries the password or a token must not print it -- not in
the exception message, not in a ``--tb=long`` or ``--full-trace`` traceback, and
not through the SDK client's own logging. The probes run pytest for real and
assert the credential is absent from what it rendered.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from tests.integration import _workroom_disposable_user as disposable
from tests.unit.workrooms._user_fakes import (
    REPO_ROOT,
    SECRET_MARKER,
    AdminAuth,
    UserClient,
    create,
    sdk_error,
    token_validation_error,
)


pytestmark = pytest.mark.unit


def test_create_failure_withholds_the_sdk_message() -> None:
    # The server declined with 400, so nothing was created and cleanup has
    # nothing to report: the withheld setup error is what surfaces.
    auth = AdminAuth(create_error=sdk_error())

    with pytest.raises(disposable.CredentialRequestError) as raised:
        create(auth, UserClient())

    assert SECRET_MARKER not in str(raised.value)
    assert "HTTP status 400" in str(raised.value)
    assert raised.value.__suppress_context__ is True
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("login_error", [sdk_error(), token_validation_error()])
def test_login_failure_withholds_and_removes_the_account(login_error) -> None:
    auth = AdminAuth()

    with pytest.raises(disposable.CredentialRequestError) as raised:
        create(auth, UserClient(login_error=login_error))

    assert SECRET_MARKER not in str(raised.value)
    assert raised.value.__suppress_context__ is True
    assert raised.value.__cause__ is None
    (account,) = auth.users
    assert auth.deleted == [account]
    # Read back once when created, then once by the absence proof.
    assert auth.readbacks == [account, account], "the removal was not proven"


def test_an_interrupt_during_a_credential_call_does_not_carry_its_frames() -> None:
    """The interrupt is re-raised fresh, so no frame of the credential call renders."""

    def credential_call() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt) as raised:
        disposable._withheld("password login", credential_call)

    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert "credential_call" not in {entry.name for entry in raised.traceback}


def test_sdk_client_logging_is_off_during_a_credential_call_and_restored() -> None:
    client = UserClient()
    client_logger = logging.getLogger(disposable.SDK_CLIENT_LOGGER)
    assert client_logger.disabled is False

    create(AdminAuth(), client)

    assert client.logger_disabled_during_login is True
    assert client_logger.disabled is False


# The probe source lives at module level so the test that runs it stays a
# readable sequence of steps rather than one long method: the string is data,
# not logic, and every assertion about what pytest rendered is below.
_CREDENTIAL_PROBE = textwrap.dedent(
    """
            from tests.integration import _workroom_disposable_user as disposable
            from tests.unit.workrooms import _user_fakes as unit

            disposable.secrets.choice = lambda alphabet: "Q"


            def test_create_fails():
                unit.create(
                    unit.AdminAuth(create_error=unit.sdk_error()),
                    unit.UserClient(),
                )


            def test_login_fails():
                unit.create(
                    unit.AdminAuth(),
                    unit.UserClient(login_error=unit.sdk_error()),
                )


            def test_login_response_is_malformed():
                unit.create(
                    unit.AdminAuth(),
                    unit.UserClient(login_error=unit.token_validation_error()),
                )


            def test_logout_fails():
                auth = unit.AdminAuth()
                user = unit.create(
                    auth,
                    unit.UserClient(
                        logout_error=unit.sdk_error(),
                        grant=unit.Grant(refresh_token=unit.SECRET_MARKER),
                    ),
                )
                disposable.delete_workroom_user(unit.Admin(auth), user)


            def test_setup_fails_after_login_then_logout_fails():
                unit.create(
                    unit.AdminAuth(),
                    unit.UserClient(
                        whoami_error=unit.APIError(
                            "setup-original-503", status_code=503
                        ),
                        logout_error=unit.sdk_error(),
                        grant=unit.Grant(
                            access_token=unit.SECRET_MARKER,
                            refresh_token=unit.SECRET_MARKER,
                        ),
                    ),
                )


            def test_admin_role_is_refused_then_delete_fails():
                unit.create(
                    unit.AdminAuth(
                        delete_error=unit.APIError("delete broke", status_code=500)
                    ),
                    unit.UserClient(
                        grant=unit.Grant(
                            roles=("admin",),
                            access_token=unit.SECRET_MARKER,
                            refresh_token=unit.SECRET_MARKER,
                        ),
                    ),
                )


            def test_login_exits_the_run():
                # A SystemExit stops the run rather than failing it, and must
                # not carry the frames of the call it stopped.
                unit.create(
                    unit.AdminAuth(),
                    unit.UserClient(login_error=SystemExit(3)),
                )


            def test_login_exits_the_run_with_a_message():
                # SystemExit.code is an arbitrary object and sys.exit("msg") is
                # an ordinary idiom, so a login that exits with a message can
                # carry the credential in the code itself. pytest renders the
                # code, so only a status may be re-raised.
                unit.create(
                    unit.AdminAuth(),
                    unit.UserClient(
                        login_error=SystemExit(
                            "login failed: " + unit.SECRET_MARKER
                        )
                    ),
                )


            def test_building_the_client_fails_after_login():
                login_client = unit.UserClient(grant=unit.Grant(access_token=unit.SECRET_MARKER))

                def factory(base_url, api_key=None):
                    if api_key is not None:
                        raise RuntimeError("client construction broke")
                    return login_client

                disposable.create_workroom_user(
                    unit.Admin(unit.AdminAuth()), factory, "http://x"
                )
            """
)


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
    probe.write_text(_CREDENTIAL_PROBE)
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

    assert "9 failed" in output, output[-2000:]
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
            from tests.unit.workrooms import _user_fakes as unit

            disposable.secrets.choice = lambda alphabet: "Q"


            def test_login_is_interrupted():
                unit.create(
                    unit.AdminAuth(),
                    unit.UserClient(login_error=KeyboardInterrupt()),
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


def test_repr_omits_the_client_and_refresh_token() -> None:
    user = create(AdminAuth(), UserClient())

    assert "refresh" not in repr(user)
    assert "client" not in repr(user)
