from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app

from .test_helpers import APP_PATH, WORKROOM_ID, auth_headers


def test_whoami_requires_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    assert client.get("/api/whoami").status_code == 401


def test_observability_requires_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    assert client.get("/api/observability", params={"marker": "marker-123"}).status_code == 401


def test_whoami_accepts_forwarded_identity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
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
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    headers = auth_headers()
    headers["x-user-workroom-id"] = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    with TestClient(app) as direct_client:
        response = direct_client.get(
            "/api/workroom-check/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            headers=headers,
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Bound workroom mismatch"


def test_workroom_check_requires_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
    assert client.get(f"/api/workroom-check/{WORKROOM_ID}").status_code == 401


def test_session_returns_identity_and_jwt_exp(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # /api/session is served by kamiwaza_extensions_lib.create_session_router.
    # Replaces a legacy test against the retired kamiwaza_auth shim, which
    # imposed a fixed MAX_SESSION_SECONDS window and set a session cookie;
    # the canonical lib derives `expires_at` from the bearer JWT's `exp`
    # claim and does not set cookies.
    monkeypatch.setenv("KAMIWAZA_USE_AUTH", "true")
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
    with caplog.at_level(logging.WARNING, logger="echo_check"):
        response = client.get(
            "/api/workroom-check/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            headers={**auth_headers(), "x-request-id": "req-403"},
        )

    assert response.status_code == 403
    assert "echo-check-workroom-denied" in caplog.text
    assert "req-403" in caplog.text
