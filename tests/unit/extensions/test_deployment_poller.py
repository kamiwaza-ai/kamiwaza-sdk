"""Tests for DeploymentPoller."""

from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_sdk.schemas.extensions import (
    Extension,
    ExtensionEndpoints,
    ExtensionServiceStatus,
    ExtensionStatus,
    ServiceStatusDetail,
)

from kamiwaza_extensions.deployment_poller import (
    DeploymentFailedError,
    DeploymentPoller,
    DeploymentTimeoutError,
)


@pytest.fixture
def poller():
    return DeploymentPoller()


def _make_ext(phase: str, url: str = None, message: str = None) -> Extension:
    endpoints = ExtensionEndpoints(external=url) if url else None
    services = []
    if message:
        services = [ExtensionServiceStatus(name="backend", message=message)]
    return Extension(
        name="test-dev-abc",
        type="app",
        version="1.0.0",
        phase=phase,
        endpoints=endpoints,
        services=services,
    )


class TestWaitForReady:
    @patch.object(DeploymentPoller, "_check_via_status_endpoint", return_value=(None, ""))
    @patch.object(DeploymentPoller, "_check_pods_ready", return_value=(True, "2/2 ready"))
    @patch("kamiwaza_extensions.deployment_poller.time.sleep")
    def test_returns_on_running(self, mock_sleep, mock_pods, mock_status, poller):
        client = MagicMock()
        client.extensions.get_extension.return_value = _make_ext(
            "Running", url="https://cluster.test/app"
        )

        result = poller.wait_for_ready(client, "test-dev-abc", timeout=10)
        assert result.phase == "Running"
        assert result.endpoints.external == "https://cluster.test/app"

    @patch.object(DeploymentPoller, "_check_via_status_endpoint", return_value=(None, ""))
    @patch.object(DeploymentPoller, "_check_pods_ready", return_value=(True, "2/2 ready"))
    @patch("kamiwaza_extensions.deployment_poller.time.sleep")
    def test_polls_through_provisioning(self, mock_sleep, mock_pods, mock_status, poller):
        client = MagicMock()
        client.extensions.get_extension.side_effect = [
            _make_ext("Pending"),
            _make_ext("Provisioning"),
            _make_ext("Running", url="https://cluster.test/app"),
        ]

        result = poller.wait_for_ready(client, "test-dev-abc", timeout=30)
        assert result.phase == "Running"
        assert client.extensions.get_extension.call_count == 3

    @patch("kamiwaza_extensions.deployment_poller.time.sleep")
    def test_raises_on_failed(self, mock_sleep, poller):
        client = MagicMock()
        client.extensions.get_extension.return_value = _make_ext(
            "Failed", message="ImagePullBackOff"
        )

        with pytest.raises(DeploymentFailedError, match="ImagePullBackOff"):
            poller.wait_for_ready(client, "test", timeout=10)

    @patch("kamiwaza_extensions.deployment_poller.time.monotonic")
    @patch("kamiwaza_extensions.deployment_poller.time.sleep")
    def test_raises_on_timeout(self, mock_sleep, mock_monotonic, poller):
        # Simulate time passing beyond deadline
        mock_monotonic.side_effect = [0, 0, 200]  # start, first check, past deadline
        client = MagicMock()
        client.extensions.get_extension.return_value = _make_ext("Provisioning")

        with pytest.raises(DeploymentTimeoutError, match="Provisioning"):
            poller.wait_for_ready(client, "test", timeout=120)

    @patch.object(DeploymentPoller, "_check_via_status_endpoint", return_value=(None, ""))
    @patch.object(DeploymentPoller, "_check_pods_ready", return_value=(False, "1/2 ready"))
    @patch("kamiwaza_extensions.deployment_poller.time.monotonic")
    @patch("kamiwaza_extensions.deployment_poller.time.sleep")
    def test_waits_for_pods_ready(self, mock_sleep, mock_monotonic, mock_pods, mock_status, poller):
        mock_monotonic.side_effect = [0, 0, 0, 200]
        client = MagicMock()
        client.extensions.get_extension.return_value = _make_ext("Running")

        with pytest.raises(DeploymentTimeoutError):
            poller.wait_for_ready(client, "test", timeout=120)

    @patch.object(DeploymentPoller, "_check_pods_ready", return_value=(True, "1/1 ready"))
    @patch("kamiwaza_extensions.deployment_poller.time.sleep")
    def test_uses_status_endpoint_when_available(self, mock_sleep, mock_pods, poller):
        """When status endpoint returns valid data, use it instead of kubectl."""
        client = MagicMock()
        client.extensions.get_extension.return_value = _make_ext("Running")

        status = ExtensionStatus(
            name="test",
            phase="Running",
            services=[
                ServiceStatusDetail(
                    name="backend",
                    image_tag="v1",
                    ready_replicas=1,
                    replicas=1,
                ),
            ],
        )
        client.extensions.get_extension_status.return_value = status

        result = poller.wait_for_ready(client, "test", timeout=10)
        assert result.phase == "Running"
        # _check_pods_ready should NOT have been called (status endpoint succeeded)
        mock_pods.assert_not_called()

    @patch.object(DeploymentPoller, "_check_pods_ready", return_value=(True, "1/1 ready"))
    @patch("kamiwaza_extensions.deployment_poller.time.monotonic")
    @patch("kamiwaza_extensions.deployment_poller.time.sleep")
    def test_falls_back_to_kubectl_when_status_fails(self, mock_sleep, mock_monotonic, mock_pods, poller):
        """When status endpoint raises, fall back to kubectl."""
        mock_monotonic.side_effect = [0, 0, 0]
        client = MagicMock()
        client.extensions.get_extension.return_value = _make_ext("Running")
        client.extensions.get_extension_status.side_effect = Exception("not available")

        result = poller.wait_for_ready(client, "test", timeout=10)
        assert result.phase == "Running"
        mock_pods.assert_called_once()
