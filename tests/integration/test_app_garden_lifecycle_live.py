"""Opt-in live App Garden lifecycle against an operator-approved disposable template."""

from __future__ import annotations

import os
import time
from uuid import UUID, uuid4

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def test_app_garden_deploy_ready_and_stop(live_kamiwaza_client) -> None:
    template = os.getenv("KAMIWAZA_APP_GARDEN_TEST_TEMPLATE_ID")
    if not template:
        pytest.skip("No approved disposable App Garden template ID configured")

    client = live_kamiwaza_client
    deployment = client.apps.deploy(
        template_id=UUID(template), name=f"sdk-app-{uuid4().hex[:8]}"
    )
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            status = str(client.apps.get_deployment_status(deployment.id)).upper()
            if "RUNNING" in status or "DEPLOYED" in status:
                break
            if "FAILED" in status or "ERROR" in status:
                pytest.fail(f"App deployment entered terminal state: {status}")
            time.sleep(2)
        else:
            pytest.fail(f"App deployment did not become ready: {status}")
        assert client.apps.get_deployment(deployment.id).id == deployment.id
    finally:
        assert client.apps.stop_deployment(deployment.id)
