"""Session-record removal and refresh-token logout evidence (ENG-12326).

Exercise both logout orderings and check an untouched control BEFORE purging it.
This deliberately does not claim already-issued access tokens are revoked.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from kamiwaza_sdk.schemas.auth import SessionPurgeRequest

from . import _disposable_local_user as disposable
from .test_auth_sessions_live import (
    open_sessions,
    session_store_available as session_store_available,
    session_tenant,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    disposable.requires_disposable_identities,
]


@pytest.fixture
def recovery_user(live_kamiwaza_client, live_server_available, client_factory):
    yield from disposable.disposable_user_lifecycle(
        live_kamiwaza_client, client_factory, live_server_available
    )


def _assert_admin_removal(admin, open_pair, purge) -> None:
    open_pair()
    admin.auth.delete_session(f"sdk-evidence-absent-{uuid4().hex}")
    assert admin.auth.purge_sessions(purge).revoked == 2
    first, second = open_pair()
    admin.auth.delete_session(second.claims["sid"])
    assert admin.auth.purge_sessions(purge).revoked == 1
    assert admin.auth.purge_sessions(purge).revoked == 0


def _assert_logout_identity(admin, open_pair, purge, logout) -> None:
    # If logout always removed the oldest record, the newest-target case fails.
    for target_index in (0, 1):
        pair = open_pair()
        target, kept = pair[target_index], pair[1 - target_index]
        logout(target)
        admin.auth.delete_session(kept.claims["sid"])
        assert admin.auth.purge_sessions(purge).revoked == 0, (
            "logout removed the wrong session record"
        )


@pytest.mark.usefixtures("session_store_available")
def test_session_record_lifecycle_with_fresh_control(
    live_kamiwaza_client,
    live_server_available,
    client_factory,
    recovery_user,
) -> None:
    admin, user = live_kamiwaza_client, recovery_user
    base_url = live_server_available

    def open_pair():
        return open_sessions(client_factory, base_url, user, 2)

    def logout(login):
        disposable.logout_with_refresh_token(client_factory, base_url, login)

    initial = open_pair()
    purge = SessionPurgeRequest(
        tenant_id=session_tenant(initial[0].claims),
        subject_id=initial[0].claims["sub"],
    )
    assert admin.auth.purge_sessions(purge).revoked == 2
    _assert_admin_removal(admin, open_pair, purge)
    _assert_logout_identity(admin, open_pair, purge, logout)

    kept, target = open_pair()
    logout(target)
    assert disposable.refresh_succeeds(client_factory, base_url, user, target) is False
    # No admin removal or purge has touched this control session.
    assert disposable.refresh_succeeds(client_factory, base_url, user, kept) is True
    assert admin.auth.purge_sessions(purge).revoked == 1
    assert admin.auth.purge_sessions(purge).revoked == 0
