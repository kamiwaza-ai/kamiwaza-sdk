"""Local-user administration against a live deployment (ENG-12326).

``test_local_user_administration_lifecycle`` is the evidence-bearing test for
``auth.local-user-administration``. It proves administration of a local
account, not enterprise SSO sign-in. The two ``test_deleted_local_user_*``
tests stay unmapped: each pins, as a strict expected failure, one behavior
observed on 1.2.1.

Every test here creates a local account, so all of them are opt-in; see
``_disposable_local_user``.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import partial

import pytest
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    KamiwazaError,
    NotFoundError,
)
from kamiwaza_sdk.schemas.auth import (
    LocalUserPasswordResetRequest,
    LocalUserUpdateRequest,
    PATCreate,
)

from . import _disposable_local_user as disposable

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    disposable.requires_disposable_identities,
]


def _revoke_with_any_working_credential(jti: str, *clients: KamiwazaClient) -> None:
    """Revoke the PAT with the first client that can.

    The user's own session is tried first; if deletion ended that session, the
    PAT revokes itself. A jti the platform no longer knows is already in the
    state this cleanup wants, so a 404 ends the attempt rather than failing it
    (whether re-revoking answers 404 or succeeds is not pinned anywhere). If
    every client is rejected as unauthenticated, the PAT itself no longer
    authenticates and needs no revocation, so a platform fix surfaces as a clean
    XPASS rather than a cleanup error. Any other failure is raised once every
    client has been tried.
    """
    failures: list[KamiwazaError] = []
    for client in clients:
        try:
            client.auth.revoke_pat(jti)
        except NotFoundError:
            continue
        except AuthenticationError:
            continue
        except KamiwazaError as exc:
            failures.append(exc)
            continue
        return
    if failures:
        raise failures[0]


@pytest.fixture
def disposable_local_user(
    live_kamiwaza_client: KamiwazaClient,
    live_server_available: str,
    client_factory: disposable.ClientFactory,
) -> Iterator[disposable.DisposableUser]:
    yield from disposable.disposable_user_lifecycle(
        live_kamiwaza_client, client_factory, live_server_available
    )


def test_local_user_administration_lifecycle(
    live_kamiwaza_client: KamiwazaClient,
    live_server_available: str,
    client_factory: disposable.ClientFactory,
    disposable_local_user: disposable.DisposableUser,
) -> None:
    admin = live_kamiwaza_client
    user = disposable_local_user

    read_back = admin.auth.get_user(user.id)
    assert (read_back.username, read_back.email) == (user.username, user.email)
    assert (read_back.roles, read_back.active, read_back.deleted) == (
        ["user"],
        True,
        False,
    )
    assert any(listed.id == user.id for listed in admin.auth.list_users())

    login = disposable.password_login(client_factory, live_server_available, user)
    as_user = client_factory(base_url=live_server_available, api_key=login.access_token)
    signed_in = as_user.auth.get_current_user()
    assert signed_in.username == user.username, "the login is not the created account"
    user_subject = signed_in.sub

    # Every administration route is refused to the non-admin. The writes aim at
    # its own account, including granting itself the admin role; account
    # creation is not attempted, because an accepted create would leave another
    # identity behind.
    refused_calls = (
        as_user.auth.list_users,
        partial(as_user.auth.get_user, user.id),
        partial(
            as_user.auth.update_user, user.id, LocalUserUpdateRequest(roles=["admin"])
        ),
        partial(
            as_user.auth.reset_user_password,
            user.id,
            LocalUserPasswordResetRequest(new_password=disposable.random_password()),
        ),
        partial(as_user.auth.delete_user, user.id),
    )
    for admin_only_call in refused_calls:
        with pytest.raises(APIError) as denied:
            admin_only_call()
        assert denied.value.status_code == 403
        assert "Admin role required" in str(denied.value)
    after_refusals = admin.auth.get_user(user.id)
    assert (after_refusals.roles, after_refusals.deleted) == (["user"], False)
    password_unchanged = disposable.password_login_succeeds(
        client_factory, live_server_available, user, user.password
    )
    assert password_unchanged is True, "the refused password reset changed the password"
    with pytest.raises(AuthorizationError):
        admin.auth.require_admin(as_user.auth.forward_auth_headers())
    admin.auth.require_admin(admin.auth.forward_auth_headers())

    updated_email = f"{user.username}-updated@example.invalid"
    admin.auth.update_user(user.id, LocalUserUpdateRequest(email=updated_email))
    assert admin.auth.get_user(user.id).email == updated_email

    previous_password = disposable.reset_password(admin, user)
    relogin = disposable.password_login(client_factory, live_server_available, user)
    as_user_again = client_factory(
        base_url=live_server_available, api_key=relogin.access_token
    )
    assert as_user_again.auth.get_current_user().sub == user_subject
    old_password_works = disposable.password_login_succeeds(
        client_factory, live_server_available, user, previous_password
    )
    assert old_password_works is False, "the replaced password still authenticates"

    others_before = {listed.id for listed in admin.auth.list_users()} - {user.id}
    deleted = admin.auth.delete_user(user.id)
    assert (deleted.deleted, deleted.active) == (True, False)
    with pytest.raises(NotFoundError):
        admin.auth.get_user(user.id)
    remaining = {listed.id for listed in admin.auth.list_users()}
    # Equality rather than absence: absence from an empty list would prove
    # nothing, and equality also catches a deletion that adds rows. A
    # deployment whose only local account was this one legitimately lists none.
    # This reads the list twice, so it assumes nothing else creates or deletes a
    # local account in between; the live suite runs serially.
    assert remaining == others_before, (
        "the accounts listed after deletion are not exactly the ones that were "
        "listed beside this user"
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Pinned 1.2.1 behavior: this assertion does not hold on the deployment "
        "under test. See ENG-12326."
    ),
)
def test_deleted_local_user_sign_in_attempt(
    live_kamiwaza_client: KamiwazaClient,
    live_server_available: str,
    client_factory: disposable.ClientFactory,
    disposable_local_user: disposable.DisposableUser,
) -> None:
    user = disposable_local_user
    disposable.password_login(client_factory, live_server_available, user)

    live_kamiwaza_client.auth.delete_user(user.id)

    signed_in = disposable.password_login_succeeds(
        client_factory, live_server_available, user, user.password
    )
    assert signed_in is False, "pinned 1.2.1 sign-in behavior; see ENG-12326"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Pinned 1.2.1 behavior: this assertion does not hold on the deployment "
        "under test. See ENG-12326."
    ),
)
def test_deleted_local_user_pat_request(
    live_kamiwaza_client: KamiwazaClient,
    live_server_available: str,
    client_factory: disposable.ClientFactory,
    disposable_local_user: disposable.DisposableUser,
) -> None:
    user = disposable_local_user
    login = disposable.password_login(client_factory, live_server_available, user)
    as_user = client_factory(base_url=live_server_available, api_key=login.access_token)
    pat = disposable.mint_pat(
        as_user, PATCreate(name=f"{user.username}-pat", ttl_seconds=900)
    )
    pat_client = client_factory(base_url=live_server_available, api_key=pat.token)
    jti = pat.pat.jti
    del pat
    try:
        if not disposable.authenticates(pat_client):
            pytest.fail("the user's PAT did not authenticate before deletion")

        live_kamiwaza_client.auth.delete_user(user.id)

        pat_still_works = disposable.authenticates(pat_client)
        assert pat_still_works is False, "pinned 1.2.1 PAT behavior; see ENG-12326"
    finally:
        _revoke_with_any_working_credential(jti, as_user, pat_client)
