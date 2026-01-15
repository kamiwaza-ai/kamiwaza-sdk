from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _forwardauth_request(client, method: str, path: str) -> None:
    headers = {
        "X-Forwarded-Uri": "/api/health",
        "X-Forwarded-Method": "GET",
    }
    try:
        if method == "GET":
            response = client.get(path, headers=headers, expect_json=False)
        else:
            response = client.post(path, headers=headers, expect_json=False)
    except APIError as exc:
        if exc.status_code in (401, 403):
            pytest.skip("ForwardAuth not available for this environment")
        raise

    assert response.status_code in (200, 204)
    if response.status_code == 200:
        lowered = {key.lower(): value for key, value in response.headers.items()}
        assert lowered.get("x-user-id")
        assert "x-user-roles" in lowered


def test_auth_metadata_health_jwks(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    metadata = client.get("/auth/")
    assert metadata.get("message") == "Kamiwaza Auth Service"

    health = client.get("/auth/health")
    assert health.get("status") == "healthy"
    assert "KAMIWAZA_USE_AUTH" in health

    jwks = client.get("/auth/jwks")
    assert isinstance(jwks.get("keys"), list)
    assert jwks.get("keys")


def test_auth_pat_list(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    pat_list = client.auth.list_pats()
    assert isinstance(pat_list.pats, list)


def test_auth_logout(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    result = client.auth.logout()
    assert result.message
    assert result.logout_time


def test_auth_refresh_flow(live_kamiwaza_client, live_username: str, live_password: str) -> None:
    if not live_username.strip() or not live_password.strip():
        pytest.skip("Provide live username/password to test refresh flow")

    token = live_kamiwaza_client.auth.login_with_password(live_username, live_password)
    if not token.refresh_token:
        pytest.skip("Refresh token not returned by password grant")

    refreshed = live_kamiwaza_client.auth.refresh_access_token(token.refresh_token)
    assert refreshed.access_token


def test_auth_validate_forwardauth_endpoints(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    _forwardauth_request(client, "GET", "/auth/validate")
    _forwardauth_request(client, "POST", "/auth/validate")
    _forwardauth_request(client, "GET", "/auth/forward/validate")
