"""Tests for kz-ext dev timeout diagnostics (B1b / §4.2.16)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.dev_diagnostics import diagnose_dev_timeout
from kamiwaza_extensions.platform_compat import OPERATOR_COMPATIBLE_TAGS, OPERATOR_IMAGE


def _deploy_json(image: str, available: bool = True, *, sidecar_first: bool = False) -> str:
    """Build the operator Deployment JSON.

    By default the operator container is the only one. When
    ``sidecar_first`` is True, prepend an unrelated sidecar at index 0 to
    catch the M3 regression where ``_container_image`` matches by
    container name rather than first-index assumption.
    """
    operator_container = {"name": "extension-operator", "image": image}
    containers = (
        [{"name": "metrics-exporter", "image": "prom/node-exporter:1.7"}, operator_container]
        if sidecar_first
        else [operator_container]
    )
    return json.dumps({
        "metadata": {"name": "extension-operator", "generation": 1},
        "spec": {"template": {"spec": {"containers": containers}}},
        "status": {
            "observedGeneration": 1,
            "conditions": [
                {"type": "Available", "status": "True" if available else "False"},
            ],
        },
    })


def _pods_json(reason: str | None = None, message: str | None = None) -> str:
    if reason is None:
        return json.dumps({"items": []})
    return json.dumps({
        "items": [
            {
                "status": {
                    "containerStatuses": [
                        {"state": {"waiting": {"reason": reason, "message": message or reason}}},
                    ],
                },
            },
        ],
    })


def _kubectl_stub(deploy_stdout: str | None, pods_stdout: str):
    """Build subprocess.run side-effect.

    Calls the diagnostics make:
      1. kubectl get pods ... -o json (operator pod ImagePullBackOff scan)
      2. kubectl get deploy ... -o json (operator Deployment payload)
    """
    def _run(cmd, *args, **kwargs):
        if cmd[1] == "get" and cmd[2] == "pods":
            return MagicMock(returncode=0, stdout=pods_stdout, stderr="")
        if cmd[1] == "get" and cmd[2] == "deploy":
            if deploy_stdout is None:
                return MagicMock(returncode=1, stdout="", stderr="not found")
            return MagicMock(returncode=0, stdout=deploy_stdout, stderr="")
        return MagicMock(returncode=1, stdout="", stderr="")
    return _run


@pytest.mark.unit
class TestDiagnoseDevTimeout:
    # TS-12: timeout handler distinguishes operator-not-ready from app-failure
    def test_image_pull_back_off_classified_as_operator_not_ready(self):
        compat = f"{OPERATOR_IMAGE}:{OPERATOR_COMPATIBLE_TAGS[0]}"
        with patch(
            "subprocess.run",
            side_effect=_kubectl_stub(
                _deploy_json(compat),
                _pods_json(reason="ImagePullBackOff", message="manifest not found"),
            ),
        ):
            d = diagnose_dev_timeout("my-app-dev-abc", "kamiwaza-extensions")
        assert d.category == "operator-not-ready"
        assert "ImagePullBackOff" in d.message
        assert OPERATOR_COMPATIBLE_TAGS[0] in (d.fix or "")

    def test_image_tag_mismatch_classified_as_operator_not_ready(self):
        with patch(
            "subprocess.run",
            side_effect=_kubectl_stub(
                _deploy_json(f"{OPERATOR_IMAGE}:v0.1.1"),
                _pods_json(),
            ),
        ):
            d = diagnose_dev_timeout("my-app-dev-abc", "kamiwaza-extensions")
        assert d.category == "operator-not-ready"
        assert "v0.1.1" in d.message
        assert OPERATOR_COMPATIBLE_TAGS[0] in d.message

    def test_deployment_not_available_classified_as_operator_not_ready(self):
        compat = f"{OPERATOR_IMAGE}:{OPERATOR_COMPATIBLE_TAGS[0]}"
        with patch(
            "subprocess.run",
            side_effect=_kubectl_stub(_deploy_json(compat, available=False), _pods_json()),
        ):
            d = diagnose_dev_timeout("my-app-dev-abc", "kamiwaza-extensions")
        assert d.category == "operator-not-ready"
        assert "Available" in d.message

    def test_healthy_operator_classified_as_app_failure(self):
        compat = f"{OPERATOR_IMAGE}:{OPERATOR_COMPATIBLE_TAGS[0]}"
        with patch(
            "subprocess.run",
            side_effect=_kubectl_stub(_deploy_json(compat), _pods_json()),
        ):
            d = diagnose_dev_timeout("my-app-dev-abc", "kamiwaza-extensions")
        assert d.category == "app-failure"
        assert "my-app-dev-abc" in d.message
        assert "kz-ext logs" in (d.fix or "")

    def test_unknown_when_operator_state_unreadable(self):
        with patch(
            "subprocess.run",
            side_effect=_kubectl_stub(None, _pods_json()),
        ):
            d = diagnose_dev_timeout("my-app-dev-abc", "kamiwaza-extensions")
        assert d.category == "unknown"


@pytest.mark.unit
class TestDiagnoseDevTimeoutSkipsForRemoteConnections:
    """Review re-review PR #84 H2: when ``run_dev_remote`` deploys to a
    remote Kamiwaza endpoint, the local kubectl context is by definition
    unrelated. Probing it would inspect a different cluster and could
    misclassify an app-level timeout as ``operator-not-ready`` (exit 23
    + "reinstall the platform"), which is confidently wrong on the
    remote case."""

    @pytest.mark.parametrize(
        "remote_url",
        [
            "https://kamiwaza.cloud/api",
            "https://customer-prod.kamiwaza.cloud/api",
        ],
    )
    def test_remote_connection_skips_kubectl_and_returns_unknown(self, remote_url):
        with patch("subprocess.run") as mock_run:
            d = diagnose_dev_timeout(
                "my-app-dev-abc", "kamiwaza-extensions",
                connection_url=remote_url,
            )
            # No kubectl probes — the gate fired first.
            assert mock_run.call_count == 0

        assert d.category == "unknown"
        assert "remote" in d.message.lower()

    def test_local_connection_runs_probe_normally(self):
        compat = f"{OPERATOR_IMAGE}:{OPERATOR_COMPATIBLE_TAGS[0]}"
        with patch(
            "subprocess.run",
            side_effect=_kubectl_stub(_deploy_json(compat), _pods_json()),
        ):
            d = diagnose_dev_timeout(
                "my-app-dev-abc", "kamiwaza-extensions",
                connection_url="https://kamiwaza.test/api",
            )
        # Healthy operator + local connection → app-failure (probe ran)
        assert d.category == "app-failure"

    def test_no_connection_url_runs_probe_for_back_compat(self):
        # When no connection_url is supplied (older callers, tests), the
        # gate is permissive — preserve the pre-fix behaviour. New callers
        # in run_dev_remote always pass connection_url.
        compat = f"{OPERATOR_IMAGE}:{OPERATOR_COMPATIBLE_TAGS[0]}"
        with patch(
            "subprocess.run",
            side_effect=_kubectl_stub(_deploy_json(compat), _pods_json()),
        ):
            d = diagnose_dev_timeout("my-app-dev-abc", "kamiwaza-extensions")
        assert d.category == "app-failure"


@pytest.mark.unit
class TestOperatorContainerByName:
    """Review re-review PR #84 M3: ``_container_image`` matches the
    operator container by name so a future sidecar at index 0 doesn't
    shadow the operator container."""

    def test_operator_image_correctly_extracted_when_sidecar_at_index_0(self):
        compat = f"{OPERATOR_IMAGE}:{OPERATOR_COMPATIBLE_TAGS[0]}"
        # _deploy_json with sidecar_first=True puts an unrelated sidecar
        # ahead of the operator. The diagnostic must still pick the
        # operator's image and classify the run as healthy (app-failure
        # downstream), not as an unrelated-image operator-not-ready.
        with patch(
            "subprocess.run",
            side_effect=_kubectl_stub(
                _deploy_json(compat, sidecar_first=True), _pods_json(),
            ),
        ):
            d = diagnose_dev_timeout("my-app-dev-abc", "kamiwaza-extensions")
        assert d.category == "app-failure"

    def test_operator_image_mismatch_detected_even_with_sidecar(self):
        # Sidecar at index 0 has a known-good image; operator at index 1
        # has an incompatible tag. Without the M3 fix this would read the
        # sidecar's image and miss the mismatch.
        with patch(
            "subprocess.run",
            side_effect=_kubectl_stub(
                _deploy_json(f"{OPERATOR_IMAGE}:v0.1.1", sidecar_first=True),
                _pods_json(),
            ),
        ):
            d = diagnose_dev_timeout("my-app-dev-abc", "kamiwaza-extensions")
        assert d.category == "operator-not-ready"
        assert "v0.1.1" in d.message
