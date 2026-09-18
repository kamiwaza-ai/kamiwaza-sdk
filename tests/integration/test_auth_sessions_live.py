"""Session revocation against a live deployment (ENG-12326).

``test_session_revocation_lifecycle`` needs the platform session store. The
store is off in the platform settings default
(``AUTH_REBAC_SESSION_ENABLED=false``) and was off on the deployment under
test, where the admin session routes answered 503 ``session_store_disabled``;
the precheck then skips before any account is created. Its assertions are
derived from the v1.2.1 session code and have never run against an enabled
store, so it is deliberately unmapped and claims no capability. Two gaps to
close before mapping it: steps 3 and 4 both log out the first-opened session
of their pair, so a store that drops the oldest record satisfies them; and
step 5's control is a session step 3's purge already counted as revoked.

``test_sdk_logout_then_refresh`` stays unmapped: it pins behavior
observed on 1.2.1 and runs whether or not the store is enabled. It is specific
to ``AuthService.logout()`` on a client that holds only an access token; on the
client that signed in, the refresh token stopped working after logout
(ENG-12326).

Both tests create a local account, so both are opt-in; see
``_disposable_local_user``.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.auth import SessionPurgeRequest

from . import _disposable_local_user as disposable

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    disposable.requires_disposable_identities,
]

DEFAULT_TENANT = "__default__"


def session_tenant(claims: Mapping[str, Any]) -> str:
    """The tenant the platform files this login's session record under.

    Mirrors v1.2.1 ``session_store._resolve_persist_tenant_id`` as it applies to
    a password login: that function also reads ``realm``, but the claims reach it
    through ``TokenClaims``, which has no ``realm`` field. Its last fallback is
    the server's ``AUTH_REBAC_DEFAULT_TENANT_ID``, which a client cannot read;
    ``__default__`` is that setting's default. A wrong guess purges nothing,
    which the lifecycle test's first count rejects.
    """
    tenant = claims.get("tenant_id") or claims.get("tenant")
    return str(tenant) if tenant else DEFAULT_TENANT


def open_sessions(
    client_factory: disposable.ClientFactory,
    base_url: str,
    user: disposable.DisposableUser,
    count: int,
) -> list[disposable.Login]:
    """Sign in ``count`` times; each grant must open its own new session."""
    # Compare the new grants against the sessions already known, rather than
    # every recorded login: a refresh records a login carrying its origin's
    # sid, which would read as a duplicate.
    known = {login.claims["sid"] for login in user.logins}
    logins = [
        disposable.password_login(client_factory, base_url, user) for _ in range(count)
    ]
    if any(login.refresh_token is None for login in logins):
        pytest.skip("the password grant returned no refresh token")
    assert len({login.claims["sub"] for login in user.logins}) == 1
    session_ids = [login.claims["sid"] for login in logins]
    assert len(set(session_ids)) == len(session_ids), (
        "each password grant must open its own session"
    )
    assert not known & set(session_ids), (
        "a password grant reused a session opened earlier"
    )
    return logins


@pytest.fixture(scope="module")
def session_store_available(live_kamiwaza_session_client: KamiwazaClient) -> None:
    """Skip unless the admin session routes are served, before creating anything."""
    precheck = SessionPurgeRequest(
        tenant_id=DEFAULT_TENANT,
        subject_id=f"{disposable.USERNAME_PREFIX}precheck-{uuid.uuid4().hex}",
    )
    try:
        result = live_kamiwaza_session_client.auth.purge_sessions(precheck)
    except APIError as exc:
        if exc.status_code == 503 and "session_store_disabled" in str(exc):
            pytest.skip(
                "this deployment's session store is disabled (503 "
                "session_store_disabled); admin session revocation cannot be "
                "exercised here"
            )
        raise
    assert result.revoked == 0, "precheck subject unexpectedly had sessions"


@pytest.fixture
def disposable_local_user(
    live_kamiwaza_client: KamiwazaClient,
    live_server_available: str,
    client_factory: disposable.ClientFactory,
) -> Iterator[disposable.DisposableUser]:
    yield from disposable.disposable_user_lifecycle(
        live_kamiwaza_client, client_factory, live_server_available
    )


@pytest.mark.usefixtures("session_store_available")
def test_session_revocation_lifecycle(
    live_kamiwaza_client: KamiwazaClient,
    live_server_available: str,
    client_factory: disposable.ClientFactory,
    disposable_local_user: disposable.DisposableUser,
) -> None:
    admin = live_kamiwaza_client
    user = disposable_local_user

    # A subject purge is the only read of the session store the API offers, and
    # it empties what it reads. Each step therefore opens its own sessions and
    # reads its effect with one purge, so no step's count depends on another's.
    untouched = open_sessions(client_factory, live_server_available, user, 2)
    purge = SessionPurgeRequest(
        tenant_id=session_tenant(untouched[0].claims),
        subject_id=untouched[0].claims["sub"],
    )

    # 1. A delete naming no live session removes nothing. This count also proves
    #    the purge reads the tenant and subject the logins were filed under.
    # A refusal and a no-op are indistinguishable from here and both mean the
    # delete removed nothing, which is what the count below asserts.
    with contextlib.suppress(NotFoundError):
        admin.auth.delete_session(
            f"{disposable.USERNAME_PREFIX}absent-{uuid.uuid4().hex}"
        )
    assert admin.auth.purge_sessions(purge).revoked == 2, (
        "a delete naming no live session removed a record, or the purge key is wrong"
    )

    # 2. A delete naming one live session removes exactly one record. Which one
    #    is not observable: the API offers no per-session read.
    named, _ = open_sessions(client_factory, live_server_available, user, 2)
    admin.auth.delete_session(named.claims["sid"])
    assert admin.auth.purge_sessions(purge).revoked == 1, (
        "a delete naming one live session did not remove exactly one record"
    )

    # 3. A logout carrying the refresh token (the path the platform honors) makes
    #    that token unusable and removes its session's record, while the other
    #    session still refreshes. Refresh does not read the store, so that
    #    control can run after the purges.
    logged_out, kept = open_sessions(client_factory, live_server_available, user, 2)
    disposable.logout_with_refresh_token(
        client_factory, live_server_available, logged_out
    )
    logged_out_refreshed = disposable.refresh_succeeds(
        client_factory, live_server_available, user, logged_out
    )
    assert logged_out_refreshed is False, "logout left its refresh token usable"
    assert admin.auth.purge_sessions(purge).revoked == 1, (
        "logout did not leave exactly one record, so it removed both or none"
    )

    # 4. Which record logout removed is only observable by naming the other
    #    one, and a purge empties what it reads, so this needs its own pair.
    #    Revoking the session that was not logged out must leave nothing to
    #    purge; had logout removed that record instead, the revoke would find
    #    nothing and the logged-out session's record would survive.
    other_logged_out, other_kept = open_sessions(
        client_factory, live_server_available, user, 2
    )
    disposable.logout_with_refresh_token(
        client_factory, live_server_available, other_logged_out
    )
    admin.auth.delete_session(other_kept.claims["sid"])
    assert admin.auth.purge_sessions(purge).revoked == 0, (
        "a record survived a logout plus a revoke of the other session, so "
        "logout removed the wrong record"
    )

    # 5. Control: a session that was neither logged out nor revoked still
    #    refreshes, so step 3's failed refresh is about logout. ``kept`` was
    #    only purged from the store, and refresh does not read the store.
    kept_refreshed = disposable.refresh_succeeds(
        client_factory, live_server_available, user, kept
    )
    assert kept_refreshed is True, (
        "a session that was not logged out failed to refresh, so the failed "
        "refresh above proves nothing about logout"
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Pinned 1.2.1 behavior: this assertion does not hold on the deployment "
        "under test. See ENG-12326."
    ),
)
def test_sdk_logout_then_refresh(
    live_server_available: str,
    client_factory: disposable.ClientFactory,
    disposable_local_user: disposable.DisposableUser,
) -> None:
    user = disposable_local_user
    login, control = [
        disposable.password_login(client_factory, live_server_available, user)
        for _ in range(2)
    ]
    if login.refresh_token is None or control.refresh_token is None:
        pytest.skip("the password grant returned no refresh token")
    # pytest.fail, not assert: this xfail absorbs AssertionError, and a broken
    # control must surface as a failure rather than as the pinned behavior.
    if not disposable.refresh_succeeds(
        client_factory, live_server_available, user, control
    ):
        pytest.fail(
            "a session that was never logged out failed to refresh, so the check "
            "below would prove nothing"
        )
    disposable.logout_access_token_only(client_factory, live_server_available, login)

    refreshed = disposable.refresh_succeeds(
        client_factory, live_server_available, user, login
    )
    assert refreshed is False, "pinned 1.2.1 logout behavior; see ENG-12326"
