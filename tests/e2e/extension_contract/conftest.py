from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from .contracts import AppSmokeContract
from .support import (
    LiveExtensionHarness,
    LiveExtensionSettings,
    LiveRoutedIntegrationState,
    assert_origin_ready,
    build_live_client,
    load_live_routed_integration_state,
)


@pytest.fixture(scope="session")
def live_routed_integration_state() -> LiveRoutedIntegrationState | None:
    return load_live_routed_integration_state()


@pytest.fixture(scope="session")
def live_extension_settings(
    live_routed_integration_state: LiveRoutedIntegrationState | None,
) -> LiveExtensionSettings:
    if os.getenv("RUN_LIVE_EXTENSION_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_EXTENSION_TESTS=1 to enable live extension compatibility tests")
    settings = LiveExtensionSettings.from_env(live_routed_integration_state)
    if not settings.api_key and not (settings.username and settings.password):
        pytest.skip("Provide KAMIWAZA_API_KEY or KAMIWAZA_USERNAME/KAMIWAZA_PASSWORD for live extension tests")
    assert_origin_ready(settings)
    ping_url = f"{settings.base_url}/ping"
    import requests

    from .support import ping_response_ok

    try:
        response = requests.get(ping_url, timeout=5, verify=settings.verify_ssl)
    except requests.RequestException as exc:
        pytest.fail(f"Kamiwaza API is unreachable at {ping_url}: {exc}")
    if not ping_response_ok(response):
        pytest.fail(f"Kamiwaza API ping failed at {ping_url}: {response.status_code} {response.text[:200]}")
    return settings


@pytest.fixture(scope="session")
def live_kamiwaza_client(live_extension_settings: LiveExtensionSettings):
    return build_live_client(live_extension_settings)


@pytest.fixture(scope="session")
def live_extension_harness(
    live_extension_settings: LiveExtensionSettings,
    live_kamiwaza_client,
) -> Iterator[LiveExtensionHarness]:
    harness = LiveExtensionHarness(live_extension_settings, live_kamiwaza_client)
    try:
        yield harness
    finally:
        harness.close()


@pytest.fixture
def app_contract() -> AppSmokeContract:
    pytest.fail("app_contract fixture must be provided by the live extension test module")


@pytest.fixture(scope="module")
def deployed_app_contract(
    live_extension_harness: LiveExtensionHarness,
    app_contract: AppSmokeContract,
) -> Iterator[dict[str, object]]:
    deployment_id: str | None = None
    if not app_contract.extension_name:
        pytest.fail("app_contract must define an extension_name")
    try:
        live_extension_harness.build_extension(app_contract)
        live_extension_harness.push_app_template(app_contract)
        template = live_extension_harness.find_app_template(app_contract)
        template_id = str(template.get("id") or "").strip()
        if not template_id:
            pytest.fail(f"Template lookup for {app_contract.extension_name} returned no template id: {template}")
        live_extension_harness.pull_template_images(template_id)
        deployment = live_extension_harness.deploy_app(template_id, app_contract)
        deployment_id = str(deployment.get("id") or "").strip()
        if not deployment_id:
            pytest.fail(f"Deployment response for {app_contract.extension_name} returned no id: {deployment}")
        ready_deployment = live_extension_harness.wait_for_deployment(deployment_id)
        live_extension_harness.write_deployment_artifact(ready_deployment, app_contract)
        yield ready_deployment
    finally:
        if deployment_id and not live_extension_harness.keep_deployments:
            live_extension_harness.cleanup_deployment(deployment_id)
