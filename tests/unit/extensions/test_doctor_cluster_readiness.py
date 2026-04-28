"""Tests for DoctorChecker.cluster_extension_readiness (B1a / §4.2.8)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.doctor import DoctorChecker
from kamiwaza_extensions.exit_codes import ExitCode
from kamiwaza_extensions.platform_compat import OPERATOR_COMPATIBLE_TAGS, OPERATOR_IMAGE


def _deploy_payload(image: str, available: bool = True, gen: int = 1, observed: int | None = 1) -> dict:
    return {
        "metadata": {"name": "extension-operator", "generation": gen},
        "spec": {
            "template": {
                "spec": {"containers": [{"name": "operator", "image": image}]},
            },
        },
        "status": {
            "observedGeneration": observed if observed is not None else gen,
            "conditions": [
                {"type": "Available", "status": "True" if available else "False"},
            ],
        },
    }


def _pods_payload(reason: str | None = None, message: str | None = None) -> dict:
    if reason is None:
        return {"items": [{"status": {"containerStatuses": [{"state": {"running": {}}}]}}]}
    state = {"waiting": {"reason": reason, "message": message or reason}}
    return {"items": [{"status": {"containerStatuses": [{"state": state}]}}]}


def _stub_kubectl(crd_returncode: int, deploy_payload: dict | None, pods_payload: dict):
    """Build a subprocess.run side-effect for the four kubectl shell-outs.

    Order called by the probe:
      1. kubectl version --client=true -o json     (availability check)
      2. kubectl get crd kamiwazaextensions...     (CRD presence)
      3. kubectl get deploy extension-operator...  (Deployment JSON)
      4. kubectl get pods -l app...                (Pod backoff scan)
    """
    def _run(cmd, *args, **kwargs):
        if cmd[1] == "version":
            return MagicMock(returncode=0, stdout="{}", stderr="")
        if cmd[1] == "get" and cmd[2] == "crd":
            return MagicMock(returncode=crd_returncode, stdout="", stderr="not found" if crd_returncode else "")
        if cmd[1] == "get" and cmd[2] == "deploy":
            payload = "" if deploy_payload is None else json.dumps(deploy_payload)
            return MagicMock(
                returncode=0 if deploy_payload is not None else 1,
                stdout=payload,
                stderr="" if deploy_payload is not None else "not found",
            )
        if cmd[1] == "get" and cmd[2] == "pods":
            return MagicMock(returncode=0, stdout=json.dumps(pods_payload), stderr="")
        return MagicMock(returncode=1, stdout="", stderr="unhandled")
    return _run


@pytest.mark.unit
class TestClusterReadinessHappyPath:
    # TS-9
    def test_pass_when_crd_present_operator_available_and_image_compatible(self, tmp_path):
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        compat = f"{OPERATOR_IMAGE}:{OPERATOR_COMPATIBLE_TAGS[0]}"
        with patch(
            "subprocess.run",
            side_effect=_stub_kubectl(0, _deploy_payload(compat), _pods_payload()),
        ):
            result = checker.cluster_extension_readiness()
        assert result.status == "pass"
        assert OPERATOR_COMPATIBLE_TAGS[0] in result.message
        assert result.exit_code is None


@pytest.mark.unit
class TestClusterReadinessFailures:
    # TS-10
    def test_fails_with_cluster_not_ready_on_image_pull_back_off(self, tmp_path):
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        compat = f"{OPERATOR_IMAGE}:{OPERATOR_COMPATIBLE_TAGS[0]}"
        kubelet_msg = "manifest for extension-operator:v0.1.1 not found"
        with patch(
            "subprocess.run",
            side_effect=_stub_kubectl(
                0,
                _deploy_payload(compat),
                _pods_payload(reason="ImagePullBackOff", message=kubelet_msg),
            ),
        ):
            result = checker.cluster_extension_readiness()
        assert result.status == "fail"
        assert result.exit_code == int(ExitCode.CLUSTER_NOT_READY) == 23
        assert "ImagePullBackOff" in result.message
        assert kubelet_msg in result.message
        # Fix names the expected tag set so the user knows what is published.
        assert OPERATOR_COMPATIBLE_TAGS[0] in (result.fix or "")

    def test_fails_when_crd_missing(self, tmp_path):
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        with patch(
            "subprocess.run",
            side_effect=_stub_kubectl(1, None, _pods_payload()),
        ):
            result = checker.cluster_extension_readiness()
        assert result.status == "fail"
        assert result.exit_code == int(ExitCode.CLUSTER_NOT_READY)
        assert "CRD" in result.message

    def test_fails_when_operator_deployment_missing(self, tmp_path):
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        with patch(
            "subprocess.run",
            side_effect=_stub_kubectl(0, None, _pods_payload()),
        ):
            result = checker.cluster_extension_readiness()
        assert result.status == "fail"
        assert result.exit_code == int(ExitCode.CLUSTER_NOT_READY)
        assert "extension-operator Deployment" in result.message

    def test_fails_when_deployment_unavailable(self, tmp_path):
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        compat = f"{OPERATOR_IMAGE}:{OPERATOR_COMPATIBLE_TAGS[0]}"
        with patch(
            "subprocess.run",
            side_effect=_stub_kubectl(
                0,
                _deploy_payload(compat, available=False),
                _pods_payload(),
            ),
        ):
            result = checker.cluster_extension_readiness()
        assert result.status == "fail"
        assert result.exit_code == int(ExitCode.CLUSTER_NOT_READY)


@pytest.mark.unit
class TestClusterReadinessTagMismatchWarning:
    # TS-11
    def test_warns_when_tag_outside_compatible_set_but_available(self, tmp_path):
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        with patch(
            "subprocess.run",
            side_effect=_stub_kubectl(
                0,
                _deploy_payload(f"{OPERATOR_IMAGE}:v0.1.1"),
                _pods_payload(),
            ),
        ):
            result = checker.cluster_extension_readiness()
        assert result.status == "warn"
        assert "v0.1.1" in result.message
        assert OPERATOR_COMPATIBLE_TAGS[0] in (result.fix or "")
        # Warn does not propagate an exit code — the doctor command should
        # not exit non-zero just because the tag list grew without us.
        assert result.exit_code is None


@pytest.mark.unit
class TestClusterReadinessKubectlMissing:
    def test_warns_when_kubectl_unavailable(self, tmp_path):
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = checker.cluster_extension_readiness()
        assert result.status == "warn"
        assert "kubectl" in result.message.lower()
