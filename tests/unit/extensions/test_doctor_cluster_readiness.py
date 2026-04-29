"""Tests for DoctorChecker.cluster_extension_readiness (B1a / §4.2.8)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.doctor import DoctorChecker
from kamiwaza_extensions.exit_codes import ExitCode
from kamiwaza_extensions.platform_compat import OPERATOR_COMPATIBLE_TAGS, OPERATOR_IMAGE


@pytest.fixture
def checker_with_connection(tmp_path):
    """A DoctorChecker with an active Kamiwaza connection mocked.

    The probe gates on connection presence (review PR #84 High #1) so the
    legacy tests below need a connection to reach the kubectl + CRD path
    they actually exercise. Tests that want to verify the no-connection
    short-circuit live in TestClusterReadinessRequiresKamiwazaConnection.
    """
    instance = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
    instance._conn_mgr.get_active_connection = MagicMock(
        return_value=MagicMock(name="dev"),
    )
    return instance


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
    def test_pass_when_crd_present_operator_available_and_image_compatible(self, checker_with_connection):
        checker = checker_with_connection
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
    def test_fails_with_cluster_not_ready_on_image_pull_back_off(self, checker_with_connection):
        checker = checker_with_connection
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

    def test_fails_when_crd_missing(self, checker_with_connection):
        checker = checker_with_connection
        with patch(
            "subprocess.run",
            side_effect=_stub_kubectl(1, None, _pods_payload()),
        ):
            result = checker.cluster_extension_readiness()
        assert result.status == "fail"
        assert result.exit_code == int(ExitCode.CLUSTER_NOT_READY)
        assert "CRD" in result.message

    def test_fails_when_operator_deployment_missing(self, checker_with_connection):
        checker = checker_with_connection
        with patch(
            "subprocess.run",
            side_effect=_stub_kubectl(0, None, _pods_payload()),
        ):
            result = checker.cluster_extension_readiness()
        assert result.status == "fail"
        assert result.exit_code == int(ExitCode.CLUSTER_NOT_READY)
        assert "extension-operator Deployment" in result.message

    def test_fails_when_deployment_unavailable(self, checker_with_connection):
        checker = checker_with_connection
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
    def test_warns_when_tag_outside_compatible_set_but_available(self, checker_with_connection):
        checker = checker_with_connection
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
    def test_warns_when_kubectl_unavailable(self, checker_with_connection):
        checker = checker_with_connection
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = checker.cluster_extension_readiness()
        assert result.status == "warn"
        assert "kubectl" in result.message.lower()


@pytest.mark.unit
class TestClusterReadinessRequiresKamiwazaConnection:
    """Review PR #84 High #1: kz-ext doctor must not exit non-zero on a
    workstation that has kubectl installed but no Kamiwaza connection
    configured (or pointed at an unrelated cluster). Without a connection
    there's no cluster the user is trying to reach, so a hard fail like
    ``CRD not installed`` is wrong."""

    def test_warns_when_no_active_connection(self, tmp_path):
        # Build without the fixture — fixture mocks a connection; we want
        # the no-connection path here.
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        checker._conn_mgr.get_active_connection = MagicMock(return_value=None)

        with patch("subprocess.run") as mock_run:
            result = checker.cluster_extension_readiness()
            # The gate fired before kubectl was even probed.
            assert mock_run.call_count == 0

        assert result.status == "warn"
        assert "no kamiwaza connection" in result.message.lower()
        assert result.exit_code is None  # warn never propagates an exit code

    def test_runs_normally_when_connection_present(self, checker_with_connection):
        # Sanity check that the gate doesn't accidentally short-circuit
        # the legacy probe paths.
        checker = checker_with_connection
        compat = f"{OPERATOR_IMAGE}:{OPERATOR_COMPATIBLE_TAGS[0]}"
        with patch(
            "subprocess.run",
            side_effect=_stub_kubectl(0, _deploy_payload(compat), _pods_payload()),
        ):
            result = checker.cluster_extension_readiness()
        assert result.status == "pass"
