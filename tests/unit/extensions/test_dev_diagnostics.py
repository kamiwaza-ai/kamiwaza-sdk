"""Tests for kz-ext dev timeout diagnostics (B1b / §4.2.16)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.dev_diagnostics import diagnose_dev_timeout
from kamiwaza_extensions.platform_compat import OPERATOR_COMPATIBLE_TAGS, OPERATOR_IMAGE


def _deploy_json(image: str, available: bool = True) -> str:
    return json.dumps({
        "metadata": {"name": "extension-operator", "generation": 1},
        "spec": {
            "template": {"spec": {"containers": [{"image": image}]}},
        },
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
