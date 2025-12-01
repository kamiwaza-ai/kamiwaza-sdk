from __future__ import annotations

import time
import uuid

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import AuthenticationError
from kamiwaza_sdk.schemas.auth import PATCreate

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


def test_password_authentication_allows_whoami(
    client_factory,
    live_server_available: str,
    live_username: str,
    live_password: str,
):
    client: KamiwazaClient = client_factory(base_url=live_server_available)
    client.authenticator = UserPasswordAuthenticator(live_username, live_password, client.auth)

    whoami = client.get("/whoami")
    assert whoami is not None
    user = client.auth.get_current_user()
    assert user.username == live_username


def test_pat_lifecycle_supports_api_key_auth(
    client_factory,
    live_server_available: str,
    live_username: str,
    live_password: str,
):
    admin_client: KamiwazaClient = client_factory(base_url=live_server_available)
    admin_client.authenticator = UserPasswordAuthenticator(live_username, live_password, admin_client.auth)

    baseline_user = admin_client.auth.get_current_user()
    expected_sub = baseline_user.sub
    expected_username = baseline_user.username

    pat_name = f"sdk-m1-{uuid.uuid4().hex[:8]}-{int(time.time())}"
    pat_response = admin_client.auth.create_pat(
        PATCreate(name=pat_name, ttl_seconds=900, scope="openid", aud="kamiwaza-platform")
    )
    pat_token = pat_response.token
    pat_jti = pat_response.pat.jti

    try:
        pat_client: KamiwazaClient = client_factory(base_url=live_server_available, api_key=pat_token)
        profile = pat_client.auth.get_current_user()
        assert profile.sub == expected_sub
        assert profile.username in {expected_username, expected_sub}
    finally:
        admin_client.auth.revoke_pat(pat_jti)

    revoked_client: KamiwazaClient = client_factory(base_url=live_server_available, api_key=pat_token)
    with pytest.raises(AuthenticationError):
        revoked_client.auth.get_current_user()
