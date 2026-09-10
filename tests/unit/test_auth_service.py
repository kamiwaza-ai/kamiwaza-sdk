from __future__ import annotations

import uuid

import pytest

from kamiwaza_sdk.schemas.auth import PATCreate, ValidationHeaders
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


def test_validation_headers_preserve_complete_identity_envelope():
    headers = ValidationHeaders.from_headers(
        {
            "X-User-Id": "user-1",
            "X-User-Roles": "admin, viewer",
            "X-User-Groups": "engineering, platform",
            "X-User-Attributes-Hash": "sha256:attributes",
            "X-User-System-High": "TS",
            "X-Workroom-Id": "workroom-1",
            "X-User-Workroom-Id": "workroom-alias",
            "X-User-Workroom-Role": "editor",
            "X-Auth-Azp": "extension-client",
            "X-User-Signature-Stable": "stable-signature",
        }
    )

    assert headers.user_id == "user-1"
    assert headers.user_roles == ["admin", "viewer"]
    assert headers.user_groups == ["engineering", "platform"]
    assert headers.user_attributes_hash == "sha256:attributes"
    assert headers.user_system_high == "TS"
    assert headers.workroom_id == "workroom-1"
    assert headers.user_workroom_id == "workroom-alias"
    assert headers.user_workroom_role == "editor"
    assert headers.auth_azp == "extension-client"
    assert headers.signature_stable == "stable-signature"
