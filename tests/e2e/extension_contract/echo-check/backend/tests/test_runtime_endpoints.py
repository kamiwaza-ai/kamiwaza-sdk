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


def test_routed_request_with_app_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """ENG-5956: real routed deployment combines uvicorn ``--root-path``
    with ``KAMIWAZA_APP_PATH`` env var. Starlette strips ``root_path`` from
    ``scope['path']`` BEFORE route matching, so unprefixed routes are the
    correct shape — adding an app-level ``prefix=runtime_prefix`` on top
    causes 404 on every routed request.

    This test reproduces the prod scenario: KAMIWAZA_APP_PATH is set AND
    TestClient is constructed with ``root_path``. The container-side
    ``/api/whoami`` must be reachable.
    """
    monkeypatch.setenv("KAMIWAZA_APP_PATH", APP_PATH)
    import app.main as main_module

    reloaded = importlib.reload(main_module)
    with TestClient(reloaded.app, root_path=APP_PATH) as routed_client:
        # Proxy delivers the full routed URL; uvicorn strips root_path,
        # so the app sees scope["path"] = "/api/runtime".
        response = routed_client.get(f"{APP_PATH}/api/runtime")
        # Unprefixed health endpoint stays reachable for liveness probes.
        health_response = routed_client.get("/health")

    assert response.status_code == 200
    assert response.json()["kamiwaza_app_path"] == APP_PATH
    assert response.json()["root_path"] == APP_PATH
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
