from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from .test_helpers import APP_PATH


def test_health_check(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "ready": True}


def test_runtime_contract(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    monkeypatch.setenv("KAMIWAZA_DEPLOYMENT_ID", "dep-123")
    monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "https://kamiwaza.test/api")
    monkeypatch.setenv("KAMIWAZA_WORKROOM_ID", "wrk-123")

    response = client.get("/api/runtime", headers={"x-forwarded-prefix": APP_PATH})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "kamiwaza_app_path": APP_PATH,
        "kamiwaza_deployment_id": "dep-123",
        "kamiwaza_public_api_url": "https://kamiwaza.test/api",
        "kamiwaza_workroom_id": "wrk-123",
        "request_id": None,
        "root_path": APP_PATH,
        "forwarded_prefix": APP_PATH,
        "forwarded_uri": None,
        "request_path": "/api/runtime",
    }


def test_runtime_prefix_routes_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    import app.main as main_module

    reloaded = importlib.reload(main_module)
    with TestClient(reloaded.app) as prefixed_client:
        root_response = prefixed_client.get(APP_PATH)
        response = prefixed_client.get(f"{APP_PATH}/api/runtime")
        direct_api_response = prefixed_client.get("/api/runtime")
        health_response = prefixed_client.get("/health")

    assert root_response.status_code == 200
    assert root_response.json() == {"service": "echo-check", "status": "ok"}
    assert response.status_code == 200
    assert response.json()["kamiwaza_app_path"] == APP_PATH
    assert direct_api_response.status_code == 404
    assert health_response.status_code == 200


def test_cors_uses_explicit_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "https://kamiwaza.test/api")
    import app.main as main_module

    reloaded = importlib.reload(main_module)
    with TestClient(reloaded.app) as cors_client:
        response = cors_client.options(
            "/api/runtime",
            headers={"Origin": "https://kamiwaza.test", "Access-Control-Request-Method": "GET"},
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://kamiwaza.test"
    assert response.headers["access-control-allow-credentials"] == "true"
