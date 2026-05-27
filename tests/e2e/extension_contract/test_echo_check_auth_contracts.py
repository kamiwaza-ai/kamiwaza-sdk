from __future__ import annotations

import pytest

from .contracts import ECHO_CHECK

pytestmark = [pytest.mark.live, pytest.mark.live_extension]


@pytest.fixture(scope="module")
def app_contract():
    return ECHO_CHECK


def test_echo_check_identity_propagation_contract(
    app_contract,
    live_extension_harness,
    deployed_app_contract,
) -> None:
    persona = live_extension_harness.persona("allowed_non_admin")
    app_url = live_extension_harness.app_url(deployed_app_contract, app_contract)
    payload = live_extension_harness.wait_for_json(
        f"{app_url}/api/whoami",
        headers=live_extension_harness.auth_headers_for_role("allowed_non_admin"),
    )

    assert payload["authenticated"] is True
    assert payload["user_id"]
    assert payload["email"]
    assert "@" in str(payload["email"])
    assert payload.get("current_workroom_id") is not None
    assert payload["workroom_role"] == persona.expected_workroom_role


def test_echo_check_rejects_spoofed_headers(
    app_contract,
    live_extension_harness,
    deployed_app_contract,
) -> None:
    app_url = live_extension_harness.app_url(deployed_app_contract, app_contract)
    baseline = live_extension_harness.wait_for_json(
        f"{app_url}/api/whoami",
        headers=live_extension_harness.auth_headers_for_role("allowed_non_admin"),
    )
    spoofed_headers = live_extension_harness.auth_headers_for_role("allowed_non_admin")
    spoofed_headers.update(
        {
            "X-User-Id": "forged-user",
            "X-User-Email": "evil@example.com",
            "X-User-Workroom-Role": "admin",
            "X-User-Workroom-Id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        }
    )

    payload = live_extension_harness.wait_for_json(f"{app_url}/api/whoami", headers=spoofed_headers)

    assert payload["user_id"] == baseline["user_id"]
    assert payload["email"] == baseline["email"]
    assert payload["workroom_role"] != "admin"
    assert payload["workroom_role"] == baseline["workroom_role"]
    assert baseline.get("current_workroom_id") is not None
    assert payload["current_workroom_id"] != "cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert payload["current_workroom_id"] == baseline["current_workroom_id"]


def test_workroom_boundary_contract(
    app_contract,
    live_extension_harness,
    deployed_app_contract,
) -> None:
    if live_extension_harness.bootstrap_state is None:
        pytest.skip("Bootstrap state is required for workroom boundary coverage")

    allowed_workroom_id = live_extension_harness.bootstrap_state.workrooms.get("allowed_workroom_id")
    denied_workroom_id = live_extension_harness.bootstrap_state.workrooms.get("denied_workroom_id")
    if not allowed_workroom_id or not denied_workroom_id:
        pytest.skip("Bootstrap state is missing workroom boundary identifiers")
    app_url = live_extension_harness.app_url(deployed_app_contract, app_contract)
    headers = live_extension_harness.auth_headers_for_role("allowed_non_admin")

    allowed = live_extension_harness.wait_for_json(
        f"{app_url}/api/workroom-check/{allowed_workroom_id}",
        headers=headers,
    )
    assert allowed["current_workroom_id"] == allowed_workroom_id

    denied = live_extension_harness.http.get(
        f"{app_url}/api/workroom-check/{denied_workroom_id}",
        headers=headers,
        timeout=10,
    )
    assert denied.status_code == 403
