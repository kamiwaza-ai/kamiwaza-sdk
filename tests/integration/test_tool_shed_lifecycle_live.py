"""Opt-in live Tool Shed lifecycle using an approved, credentialed template."""

from __future__ import annotations

import json
import os
import time
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def test_tool_shed_deploy_health_discover_and_stop(live_kamiwaza_client) -> None:
    template = os.getenv("KAMIWAZA_TOOL_SHED_TEST_TEMPLATE")
    env_json = os.getenv("KAMIWAZA_TOOL_SHED_TEST_ENV_JSON")
    if not template or not env_json:
        pytest.skip("No approved Tool Shed template and credentials configured")

    env_vars = json.loads(env_json)
    assert isinstance(env_vars, dict)
    service = live_kamiwaza_client.tools
    deployment = service.deploy_from_template(
        template_name=template,
        name=f"sdk-tool-{uuid4().hex[:8]}",
        env_vars=env_vars,
    )
    try:
        assert deployment.url
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            health = service.check_health(deployment.id)
            if health.status.lower() == "healthy":
                break
            time.sleep(2)
        else:
            pytest.fail(f"Tool deployment did not become healthy: {health.status}")
        discovery = service.discover_servers()
        assert any(item.deployment_id == deployment.id for item in discovery.servers)
    finally:
        assert service.stop_deployment(deployment.id)
