from __future__ import annotations

import time
from collections.abc import Iterator

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import app

APP_PATH = "/runtime/apps/test-123"
WORKROOM_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

# Used by `auth_headers(with_token=True)` to embed a JWT `exp` claim that the
# extensions-lib `/api/session` endpoint surfaces back as `expires_at`. Picked
# as a comfortable offset that won't collide with iat in tests.
TOKEN_EXP_SECONDS = 3600


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, root_path=APP_PATH) as test_client:
        yield test_client


def auth_headers(*, with_token: bool = False) -> dict[str, str]:
    # Two workroom headers by design: ``x-workroom-id`` is the canonical
    # platform envelope read by ``kamiwaza_extensions_lib.require_auth``;
    # ``x-user-workroom-id`` is echo-check's local trusted-forwarded
    # header (see app/workroom_trust.py) for the binding semantics these
    # tests cover.
    headers = {
        "x-user-id": "123e4567-e89b-12d3-a456-426614174000",
        "x-user-email": "tester@example.com",
        "x-user-name": "Test User",
        "x-user-roles": "user,admin",
        "x-workroom-id": WORKROOM_ID,
        "x-user-workroom-role": "editor",
        "x-user-workroom-id": WORKROOM_ID,
        "x-forwarded-proto": "https",
        "x-forwarded-prefix": APP_PATH,
        "x-forwarded-uri": f"{APP_PATH}/api/whoami",
    }
    if with_token:
        issued_at = int(time.time())
        expires_at = issued_at + TOKEN_EXP_SECONDS
        # TEST-ONLY FIXTURE: kamiwaza_extensions_lib accepts unsigned JWTs
        # only in its test-mode auth path (sid + iat + exp claims, no
        # signature verification). This fixture never hits real validation —
        # production extensions enforce signed tokens via the platform's
        # mesh-proxy ext_authz envelope, not via this helper.
        token = jwt.encode(
            {"sid": "session-1", "iat": issued_at, "exp": expires_at},
            key="",
            algorithm="none",
        )
        headers["authorization"] = f"Bearer {token}"
        headers["x-issued-at"] = str(issued_at)
        headers["x-expires-at"] = str(expires_at)
    return headers
