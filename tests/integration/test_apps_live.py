from __future__ import annotations

import os
from uuid import uuid4

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, AuthenticationError

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _create_minimal_template(client, name: str) -> dict:
    payload = {
        "name": name,
        "version": "1.0.0",
        "source_type": "public",
        "visibility": "private",
        "compose_yml": "version: '3.8'\nservices: {}\n",
        "risk_tier": 0,
        "description": "SDK integration test template",
    }
    return client.post("/apps/app_templates", json=payload)


def test_apps_config_and_garden_status(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    config = client.get("/apps/config/ephemeral_forced")
    assert "ephemeral_forced" in config

    status = client.get("/apps/garden/status")
    assert "garden_apps_available" in status
    assert "missing_apps" in status


def test_apps_template_crud_and_images(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client
    template_id: str | None = None

    try:
        created = _create_minimal_template(client, _unique("sdk-app-template"))
        template_id = str(created.get("id"))
        assert template_id

        templates = client.get("/apps/app_templates")
        assert any(str(item.get("id")) == template_id for item in templates)

        fetched = client.get(f"/apps/app_templates/{template_id}")
        assert fetched.get("id") == template_id

        update_payload = {"description": "SDK integration update"}
        updated = client.put(f"/apps/app_templates/{template_id}", json=update_payload)
        assert updated.get("description") == update_payload["description"]

        status = client.get(f"/apps/images/status/{template_id}")
        assert str(status.get("template_id")) == template_id
        assert "images" in status

        pull = client.post(f"/apps/images/pull/{template_id}")
        assert "images" in pull or pull.get("message") == "No images to pull"
    finally:
        if template_id:
            try:
                client.delete(f"/apps/app_templates/{template_id}")
            except APIError:
                pass


def test_apps_deploy_and_deployment_error_paths(
    live_kamiwaza_client, live_server_available
) -> None:
    client = live_kamiwaza_client
    bogus_id = uuid4()

    payload = {
        "name": _unique("sdk-app-deploy"),
        "template_id": str(bogus_id),
        "min_copies": 1,
        "starting_copies": 1,
    }

    with pytest.raises(APIError) as exc:
        client.post("/apps/deploy_app", json=payload)
    assert exc.value.status_code == 400

    with pytest.raises(APIError) as exc:
        client.get(f"/apps/deployment/{bogus_id}")
    assert exc.value.status_code == 404

    with pytest.raises(APIError) as exc:
        client.get(f"/apps/deployment/{bogus_id}/status")
    assert exc.value.status_code == 404

    with pytest.raises(APIError) as exc:
        client.delete(f"/apps/deployment/{bogus_id}")
    assert exc.value.status_code == 404

    with pytest.raises(APIError) as exc:
        client.delete(f"/apps/deployment/{bogus_id}/purge")
    assert exc.value.status_code == 404

    with pytest.raises(APIError) as exc:
        client.get(f"/apps/instance/{bogus_id}")
    assert exc.value.status_code == 404

    try:
        instances = client.get("/apps/instances", params={"deployment_id": str(bogus_id)})
        assert isinstance(instances, list)
    except APIError as exc:
        assert exc.status_code == 404

    # App session endpoints use a dedicated session-token auth dependency
    # (server PR #1376, ENG-2902). Tokens must be supplied via the
    # ``Authorization: Bearer`` or ``X-App-Session-Token`` header and must
    # match a deployment's hashed token in the database. Server precedence
    # validates ``Authorization: Bearer`` *first*, so if we sent this request
    # with the test's PAT attached the 401 would be for "no deployment
    # matches the PAT owner" rather than a true session-token rejection —
    # a false positive. Build a throwaway client with **no authenticator**
    # so only the ``X-App-Session-Token`` header drives the auth decision.
    bogus_session_token = f"sdk-session-{uuid4().hex}"
    session_headers = {"X-App-Session-Token": bogus_session_token}
    anon_client = KamiwazaClient(live_server_available)
    anon_client.authenticator = None  # defeat env-PAT fallback
    # Match on the server's session-token rejection detail
    # (``_extract_session_token`` raises 401 with ``"Invalid session token"``).
    # A pure-anon 401 produced by the SDK itself says "No authenticator
    # provided.", so matching on "session token" confirms the request
    # actually reached the server's session-token dependency rather than
    # being short-circuited anywhere else in the stack.
    session_token_pattern = r"(?i)session token"
    with pytest.raises(AuthenticationError, match=session_token_pattern):
        anon_client.post("/apps/sessions/heartbeat", headers=session_headers)
    with pytest.raises(AuthenticationError, match=session_token_pattern):
        anon_client.post(
            "/apps/sessions/end",
            headers=session_headers,
            json={"reason": "sdk-test"},
        )

    deployments = client.get("/apps/deployments")
    assert isinstance(deployments, list)


@pytest.mark.skipif(
    os.environ.get("KAMIWAZA_TEST_REMOTE_SYNC") != "1",
    reason="Set KAMIWAZA_TEST_REMOTE_SYNC=1 to exercise remote sync",
)
def test_apps_remote_sync(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client
    payload = {"names": [f"sdk-missing-{uuid4().hex}"]}
    result = client.post("/apps/remote/sync", json=payload)
    assert result.get("success") in (True, "true")


@pytest.mark.skipif(
    os.environ.get("KAMIWAZA_TEST_GARDEN_IMPORT") != "1",
    reason="Set KAMIWAZA_TEST_GARDEN_IMPORT=1 to exercise garden import",
)
def test_apps_garden_import(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client
    result = client.post("/apps/garden/import")
    assert result.get("success") in (True, "true")


def test_apps_remote_catalog_endpoints(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    status = client.get("/apps/remote/status")
    assert "cache_status" in status

    remote_apps = client.get("/apps/remote/apps")
    assert isinstance(remote_apps, list)
