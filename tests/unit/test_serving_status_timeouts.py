from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from kamiwaza_sdk.services.serving import (
    DEPLOYMENT_STATUS_REQUEST_TIMEOUT_SECONDS,
    DeploymentStatusPoller,
    ServingService,
)

pytestmark = pytest.mark.unit


def _deployment_payload(deployment_id: UUID, status: str) -> dict:
    return {
        "id": str(deployment_id),
        "m_id": str(uuid4()),
        "m_config_id": str(uuid4()),
        "requested_at": "2026-06-09T00:00:00Z",
        "status": status,
        "instances": [],
    }


def test_get_deployment_status_bounds_transport_timeout(mock_client):
    deployment_id = uuid4()
    mock_client.expect(
        "GET",
        f"/serving/deployment/{deployment_id}/status",
        _deployment_payload(deployment_id, "DEPLOYING"),
    )

    ServingService(mock_client).get_deployment_status(deployment_id)

    assert mock_client.calls[0][2]["timeout"] == (
        DEPLOYMENT_STATUS_REQUEST_TIMEOUT_SECONDS
    )


class _StatusService:
    def __init__(self) -> None:
        self.timeouts: list[float | None] = []

    def get_deployment(
        self, deployment_id: UUID, *, timeout_seconds: float | None = None
    ) -> SimpleNamespace:
        self.timeouts.append(timeout_seconds)
        return SimpleNamespace(status="DEPLOYED", id=deployment_id)


def test_status_poller_caps_transport_timeout_to_remaining_budget():
    deployment_id = uuid4()
    service = _StatusService()

    deployment = DeploymentStatusPoller(
        service,
        poll_interval=0,
        timeout=5.0,
        sleep_fn=lambda _: None,
        time_fn=lambda: 0.0,
    ).wait_for(
        deployment_id, desired_status=["DEPLOYED"], failure_status=["FAILED"]
    )

    assert deployment.status == "DEPLOYED"
    assert service.timeouts == [5.0]
