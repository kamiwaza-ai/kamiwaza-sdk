from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.serving.serving import CreateModelDeployment

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _first_deployment_id(client) -> UUID | None:
    deployments = client.serving.list_deployments()
    if not deployments:
        return None
    return deployments[0].id


def _first_instance_id(client) -> UUID | None:
    instances = client.serving.list_model_instances()
    if not instances:
        return None
    return instances[0].id


def test_serving_status_and_health(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    status = client.get("/serving/status")
    assert status.get("status") in {"running", "not running"}

    health = client.get("/serving/health")
    assert isinstance(health, list)


def test_serving_deployments_and_instances(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    deployments = client.serving.list_deployments()
    assert isinstance(deployments, list)

    instances = client.serving.list_model_instances()
    assert isinstance(instances, list)

    if instances:
        instance = client.serving.get_model_instance(instances[0].id)
        assert instance.id == instances[0].id
    else:
        fake_instance_id = uuid4()
        with pytest.raises(APIError) as exc_info:
            client.get(f"/serving/model_instance/{fake_instance_id}")
        if exc_info.value.status_code == 500:
            pytest.skip(
                "Server defect: missing model instance returns 500 instead of 404 "
                "(see docs-local/0.10.0/00-server-defects.md)"
            )
        assert exc_info.value.status_code == 404


def test_serving_deployment_status_and_log_patterns(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client
    deployment_id = _first_deployment_id(client)

    if deployment_id is None:
        fake_deployment_id = uuid4()
        with pytest.raises(APIError) as exc_info:
            client.get(f"/serving/deployment/{fake_deployment_id}/status")
        assert exc_info.value.status_code == 404

        with pytest.raises(APIError) as exc_info:
            client.get(f"/serving/deployment/{fake_deployment_id}/logs/patterns")
        assert exc_info.value.status_code == 404
        return

    status = client.get(f"/serving/deployment/{deployment_id}/status")
    assert isinstance(status, str)
    assert status

    try:
        patterns = client.get(f"/serving/deployment/{deployment_id}/logs/patterns")
    except APIError as exc:
        if exc.status_code == 404:
            pytest.skip(
                "No deployment logs yet; /logs/patterns returned 404 "
                "(see docs-local/0.10.0/00-server-defects.md if unexpected)"
            )
        raise

    assert str(patterns.get("deployment_id")) == str(deployment_id)
    assert isinstance(patterns.get("patterns_detected"), dict)


def test_serving_estimate_model_vram(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    payload = CreateModelDeployment(
        m_id=uuid4(),
        m_config_id=uuid4(),
        min_copies=1,
        starting_copies=1,
    ).model_dump(mode="json")

    estimate = client.post("/serving/estimate_model_vram", json=payload)
    assert isinstance(estimate, dict)
    assert "computed_vram_estimate" in estimate
    assert "estimation_source" in estimate


def test_serving_engine_logs(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    try:
        logs = client.get("/serving/logs/vllm")
    except APIError as exc:
        if exc.status_code in (400, 500):
            pytest.skip(
                "Server defect: engine logs unavailable for vllm "
                "(see docs-local/0.10.0/00-server-defects.md)"
            )
        raise

    assert logs.get("engine_type") == "vllm"
    assert isinstance(logs.get("logs"), list)


@pytest.mark.skipif(
    os.environ.get("KAMIWAZA_TEST_RAY_START") != "1",
    reason="Set KAMIWAZA_TEST_RAY_START=1 to exercise /serving/start",
)
def test_serving_start_ray(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    status = client.get("/serving/status")
    if status.get("status") == "running":
        pytest.skip("Ray already running; skipping /serving/start")

    client.post("/serving/start")

    status = client.get("/serving/status")
    assert status.get("status") == "running"
