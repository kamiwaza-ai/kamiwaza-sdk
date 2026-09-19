"""The account this helper signs in as, and the credential it carries.

The disposable user is created with the ``user`` role and no other, and a sign-in
that answers otherwise is refused and the account removed. The client that
performs the grant carries this user's identity and never the ambient
administrator's.
"""

from __future__ import annotations

import warnings

import pytest
from kamiwaza_sdk import KamiwazaClient
from tests.integration import _workroom_disposable_user as disposable
from tests.unit.workrooms._user_fakes import (
    Grant,
    REAL_ANNOUNCE_ACCOUNT,
    AdminAuth,
    UserClient,
    create,
)


pytestmark = pytest.mark.unit


def test_the_account_is_created_with_the_user_role_and_read_back() -> None:
    auth = AdminAuth()

    user = create(auth, UserClient())

    assert auth.requested_roles == [["user"]]
    assert auth.readbacks == [user.id], "the created account was not read back"


def test_an_account_that_signs_in_as_an_administrator_is_refused_and_removed() -> None:
    auth = AdminAuth()

    with pytest.raises(AssertionError, match="administrator"):
        create(auth, client := UserClient(grant=Grant(roles=("admin", "user"))))

    (account,) = auth.users
    assert auth.deleted == [account]
    assert client.posted == ["/auth/logout"], "the admin-role session was left open"


def test_a_sign_in_whose_roles_do_not_include_user_is_refused_and_removed() -> None:
    """An absent roles list must not pass as "not an administrator"."""
    auth = AdminAuth()

    with pytest.raises(AssertionError, match="user role"):
        create(auth, client := UserClient(grant=Grant(roles=("offline_access",))))

    (account,) = auth.users
    assert auth.deleted == [account]
    assert client.posted == ["/auth/logout"]


def test_a_login_with_no_access_token_is_refused_and_the_account_removed() -> None:
    """A falsy api_key sends the factory back to the ambient KAMIWAZA_API_KEY."""
    auth = AdminAuth()

    with pytest.raises(AssertionError, match="no access token"):
        create(auth, UserClient(grant=Grant(access_token="   ")))

    (account,) = auth.users
    assert auth.deleted == [account], "the account outlived a login it cannot use"


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
    auth = AdminAuth()
    client = UserClient()

    user = create(auth, client)

    assert client.authenticator is None, (
        "the login client kept the shared administrator's bearer"
    )
    assert user.client is client


def test_the_account_warning_names_the_account_under_error_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(disposable, "_announce_account", REAL_ANNOUNCE_ACCOUNT)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("error")
        user = create(AdminAuth(), UserClient())

    assert user.subject == "subject-id"
    assert [w.category for w in seen] == [disposable.DisposableAccountWarning]
    assert user.username in str(seen[0].message)
