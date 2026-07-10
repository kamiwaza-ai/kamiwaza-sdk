from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app

from .test_helpers import APP_PATH, TRUSTED_PROXY_SECRET, WORKROOM_ID, auth_headers


def test_whoami_requires_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    # ENG-5956 RE-REVIEW #2 follow-up: protected routes are now gated on
    # the trusted-proxy marker (defense-in-depth). A request without it
    # returns 404 — supersedes the previous 401 outcome for no-auth.
    assert client.get("/api/whoami").status_code == 404


def test_observability_requires_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    # ENG-5956 RE-REVIEW #2 follow-up: gate fires before require_auth.
    assert client.get("/api/observability", params={"marker": "marker-123"}).status_code == 404


def test_whoami_accepts_forwarded_identity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    monkeypatch.setenv("KAMIWAZA_DEPLOYMENT_ID", "dep-123")

    response = client.get("/api/whoami", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["email"] == "tester@example.com"
    assert response.json()["kamiwaza_app_path"] == APP_PATH
    assert response.json()["kamiwaza_deployment_id"] == "dep-123"
    assert response.json()["current_workroom_id"] == WORKROOM_ID
    assert response.json()["workroom_role"] == "editor"


def test_workroom_check_routes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    assert (
        client.get(f"/api/workroom-check/{WORKROOM_ID}", headers=auth_headers()).status_code == 200
    )
    denied = client.get(
        "/api/workroom-check/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", headers=auth_headers()
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Bound workroom mismatch"


def test_workroom_check_accepts_uppercase_identifiers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    uppercase_path = client.get(
        f"/api/workroom-check/{WORKROOM_ID.upper()}", headers=auth_headers()
    )
    uppercase_identity_headers = auth_headers()
    uppercase_identity_headers["x-user-workroom-id"] = WORKROOM_ID.upper()
    uppercase_identity = client.get(
        f"/api/workroom-check/{WORKROOM_ID}", headers=uppercase_identity_headers
    )

    assert uppercase_path.status_code == 200
    assert uppercase_identity.status_code == 200
    assert uppercase_path.json()["current_workroom_id"] == WORKROOM_ID
    assert uppercase_identity.json()["current_workroom_id"] == WORKROOM_ID


def test_workroom_check_rejects_spoofed_x_user_workroom_id_on_direct_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENG-5956 RE-REVIEW #2 follow-up: defense-in-depth supersedes the
    workroom-binding-mismatch check on this code path.

    Direct (no root_path) client cannot pass ``require_routed_request``
    because ``KAMIWAZA_TRUSTED_PROXY_SECRET`` is unset → 404. The
    workroom-mismatch path stays exercised in other tests where the
    secret IS configured and the routed-trust gate has been passed."""
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    headers = auth_headers()
    headers["x-user-workroom-id"] = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    with TestClient(app) as direct_client:
        response = direct_client.get(
            "/api/workroom-check/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            headers=headers,
        )

    assert response.status_code == 404


def test_workroom_check_requires_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    # ENG-5956 RE-REVIEW #2 follow-up: gate fires before require_auth.
    assert client.get(f"/api/workroom-check/{WORKROOM_ID}").status_code == 404


def test_session_returns_identity_and_jwt_exp(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # /api/session is served by kamiwaza_extensions_lib.create_session_router.
    # Replaces a legacy test against the retired kamiwaza_auth shim, which
    # imposed a fixed MAX_SESSION_SECONDS window and set a session cookie;
    # the canonical lib derives `expires_at` from the bearer JWT's `exp`
    # claim and does not set cookies.
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    # ENG-5956 RE-REVIEW #2: /api/session is also gated on the marker.
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    headers = auth_headers(with_token=True)
    expected_exp = int(headers.pop("x-expires-at"))
    headers.pop("x-issued-at")
    response = client.get("/api/session", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["is_authenticated"] is True
    assert body["email"] == "tester@example.com"
    assert body["expires_at"] == expected_exp


def test_observability_logs_request_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    monkeypatch.setenv("KAMIWAZA_DEPLOYMENT_ID", "dep-123")
    with caplog.at_level(logging.INFO, logger="echo_check"):
        response = client.get(
            "/api/observability",
            params={"marker": "marker-123"},
            headers={**auth_headers(), "x-request-id": "req-123"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "logged"
    assert response.json()["marker"] == "marker-123"
    assert response.json()["request_id"] == "req-123"
    assert "marker-123" in caplog.text
    assert "req-123" in caplog.text
    assert "dep-123" in caplog.text


def test_observability_rejects_invalid_marker(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    response = client.get(
        "/api/observability", params={"marker": "bad\nmarker"}, headers=auth_headers()
    )
    assert response.status_code == 422


def test_workroom_check_logs_denials(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_TRUSTED_PROXY_SECRET", TRUSTED_PROXY_SECRET)
    with caplog.at_level(logging.WARNING, logger="echo_check"):
        response = client.get(
            "/api/workroom-check/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            headers={**auth_headers(), "x-request-id": "req-403"},
        )

    assert response.status_code == 403
    assert "echo-check-workroom-denied" in caplog.text
    assert "req-403" in caplog.text
