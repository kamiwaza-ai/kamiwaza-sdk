from __future__ import annotations

from urllib.parse import urlparse
from uuid import uuid4

import pytest

from .contracts import ECHO_CHECK

pytestmark = pytest.mark.live_extension


@pytest.fixture(scope="module")
def app_contract():
    return ECHO_CHECK


def test_echo_check_deploy_smoke(
    app_contract,
    live_extension_harness,
    deployed_app_contract,
) -> None:
    app_url = live_extension_harness.deployment_url(deployed_app_contract, app_contract)
    probe_headers = live_extension_harness.probe_headers(app_contract)
    app_path = urlparse(app_url).path
    deployment_id = str(deployed_app_contract["id"])

    root_payload = live_extension_harness.wait_for_json(app_url, headers=probe_headers)
    assert root_payload == {"service": "echo-check", "status": "ok"}

    ready_payload = live_extension_harness.wait_for_json(
        f"{app_url}{app_contract.readiness_path}",
        headers=probe_headers,
    )
    assert ready_payload == {"status": "ready", "ready": True}

    runtime_payload = live_extension_harness.wait_for_json(
        f"{app_url}{app_contract.smoke_path}",
        headers=probe_headers,
    )
    assert runtime_payload[app_contract.smoke_json_key] == app_path
    assert runtime_payload["kamiwaza_deployment_id"] == deployment_id

    auth_headers = live_extension_harness.auth_headers()
    whoami_payload = live_extension_harness.wait_for_json(f"{app_url}/api/whoami", headers=auth_headers)
    assert whoami_payload["authenticated"] is True
    assert whoami_payload["kamiwaza_app_path"] == app_path
    assert whoami_payload["user_id"]
    assert whoami_payload["email"]

    session_payload = live_extension_harness.wait_for_json(f"{app_url}/api/session", headers=auth_headers)
    assert session_payload["user_id"]
    assert session_payload["email"]
    assert "expires_at" in session_payload

    request_id = f"echo-check-{uuid4().hex}"
    log_marker = f"echo-check-observability-{uuid4().hex}"
    observability_headers = {
        **auth_headers,
        "X-Request-Id": request_id,
    }
    observability_payload = live_extension_harness.wait_for_json(
        f"{app_url}/api/observability?marker={log_marker}",
        headers=observability_headers,
    )
    assert observability_payload["status"] == "logged"
    assert observability_payload["marker"] == log_marker
    assert observability_payload["request_id"] == request_id
    assert observability_payload["kamiwaza_deployment_id"] == deployment_id

    deployment = live_extension_harness.deployment_diagnostics(deployment_id)
    assert str(deployment["id"]) == deployment_id
    assert str(deployment["status"]).upper() in {"DEPLOYED", "RUNNING"}
    assert deployment["access_path"] == deployed_app_contract["access_path"]
    assert isinstance(deployment.get("instances", []), list)

    log_payload = live_extension_harness.wait_for_deployment_logs(
        deployment_id,
        marker=log_marker,
        request_id=request_id,
    )
    assert log_payload["deployment_id"] == deployment_id
    assert isinstance(log_payload["logs"], list)
