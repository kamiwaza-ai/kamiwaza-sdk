from __future__ import annotations

import uuid

import pytest

from kamiwaza_sdk.schemas.auth import PATCreate
from kamiwaza_sdk.services.auth import AuthService

pytestmark = pytest.mark.unit


def test_login_with_password_posts_form(dummy_client):
    token_payload = {
        "access_token": "token",
        "token_type": "bearer",
        "expires_in": 3600,
        "refresh_token": "refresh",
    }
    responses = {("post", "/auth/token"): token_payload}
    client = dummy_client(responses)
    service = AuthService(client)

    token = service.login_with_password("admin", "kamiwaza")

    assert token.access_token == "token"
    method, path, kwargs = client.calls[0]
    assert method == "post"
    assert path == "/auth/token"
    assert kwargs["data"]["username"] == "admin"
    assert kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert kwargs["skip_auth"] is True


def test_create_pat_round_trip(dummy_client):
    pat_id = str(uuid.uuid4())
    pat_response = {
        "token": "pat-token",
        "pat": {
            "id": pat_id,
            "jti": "jti-1",
            "owner_id": "urn:li:corpuser:admin",
            "name": "sdk",
            "ttl_seconds": 3600,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "revoked": False,
        },
    }
    responses = {("post", "/auth/pats"): pat_response}
    client = dummy_client(responses)
    service = AuthService(client)

    payload = PATCreate(name="sdk", ttl_seconds=60)
    result = service.create_pat(payload)

    assert result.token == "pat-token"
    assert result.pat.jti == "jti-1"
    method, path, kwargs = client.calls[0]
    assert path == "/auth/pats"
    assert kwargs["params"]["name"] == "sdk"
