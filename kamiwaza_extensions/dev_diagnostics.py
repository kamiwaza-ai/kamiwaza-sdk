"""Diagnostics emitted when ``kz-ext dev`` fails or times out.

Inspects the ``extension-operator`` Deployment + Pod state and the extension
CR events to distinguish *operator-not-ready* from *your-app-failed-to-start*.

Design reference: §4.2.16 ``OperatorImagePin`` consumer + §4.8 B1b.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Optional

from kamiwaza_extensions.platform_compat import (
    OPERATOR_COMPATIBLE_TAGS,
    OPERATOR_DEPLOYMENT,
    OPERATOR_NAMESPACE,
    is_compatible_tag,
    parse_image_ref,
)


@dataclass
class TimeoutDiagnosis:
    """Structured diagnosis of why ``kz-ext dev`` timed out."""

    category: str  # "operator-not-ready" | "app-failure" | "unknown"
    message: str
    fix: Optional[str] = None


def diagnose_dev_timeout(extension_name: str, extensions_namespace: str) -> TimeoutDiagnosis:
    """Diagnose a ``DeploymentTimeoutError`` from ``kz-ext dev``.

    Order of checks (most actionable first):
      1. extension-operator Pod in ``ImagePullBackOff`` → operator-not-ready,
         name the kubelet error.
      2. extension-operator image tag not in ``OPERATOR_COMPATIBLE_TAGS`` →
         operator-not-ready, name the mismatch.
      3. extension-operator Deployment not Available → operator-not-ready.
      4. Otherwise → app-failure (fall through to caller's existing message).
    """
    backoff = _operator_pod_backoff()
    if backoff is not None:
        return TimeoutDiagnosis(
            category="operator-not-ready",
            message=f"extension-operator pod is in ImagePullBackOff: {backoff}",
            fix=(
                "The cluster's extension-operator cannot pull its image. "
                "Re-run the platform installer with a published tag "
                f"(expected one of: {', '.join(OPERATOR_COMPATIBLE_TAGS)})."
            ),
        )

    deploy = _operator_deployment()
    if deploy is None:
        return TimeoutDiagnosis(
            category="unknown",
            message=(
                f"Extension '{extension_name}' did not become Ready, and "
                "extension-operator state could not be inspected."
            ),
        )

    image_ref = _container_image(deploy)
    _, tag = parse_image_ref(image_ref) if image_ref else ("", None)
    if tag and not is_compatible_tag(tag):
        expected = ", ".join(OPERATOR_COMPATIBLE_TAGS)
        return TimeoutDiagnosis(
            category="operator-not-ready",
            message=(
                f"Extension operator is running '{tag}' which was not "
                f"published for this CLI version; expected one of: {expected}."
            ),
            fix="Re-install the platform with a compatible operator image.",
        )

    if not _deployment_available(deploy):
        return TimeoutDiagnosis(
            category="operator-not-ready",
            message="extension-operator Deployment is not Available.",
            fix=(
                "Inspect: kubectl describe deploy/extension-operator "
                f"-n {OPERATOR_NAMESPACE}"
            ),
        )

    return TimeoutDiagnosis(
        category="app-failure",
        message=(
            f"Extension '{extension_name}' failed to start within the "
            "timeout. Operator is healthy — inspect your extension's pod logs."
        ),
        fix=f"kz-ext logs --name {extension_name}",
    )


def _operator_deployment() -> Optional[dict]:
    try:
        result = subprocess.run(
            [
                "kubectl", "get", "deploy", OPERATOR_DEPLOYMENT,
                "-n", OPERATOR_NAMESPACE, "-o", "json",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def _operator_pod_backoff() -> Optional[str]:
    try:
        result = subprocess.run(
            [
                "kubectl", "get", "pods",
                "-n", OPERATOR_NAMESPACE,
                "-l", f"app.kubernetes.io/name={OPERATOR_DEPLOYMENT}",
                "-o", "json",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None

    for pod in payload.get("items", []):
        statuses = (pod.get("status", {}) or {}).get("containerStatuses", []) or []
        for cs in statuses:
            waiting = (cs.get("state", {}) or {}).get("waiting", {}) or {}
            reason = waiting.get("reason", "")
            if reason in ("ImagePullBackOff", "ErrImagePull"):
                return waiting.get("message") or reason
    return None


def _container_image(deploy: dict) -> str:
    try:
        return (
            deploy.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [{}])[0]
            .get("image", "")
        )
    except (IndexError, AttributeError, TypeError):
        return ""


def _deployment_available(deploy: dict) -> bool:
    status = deploy.get("status", {}) or {}
    observed = status.get("observedGeneration")
    spec_gen = deploy.get("metadata", {}).get("generation")
    if observed is not None and observed != spec_gen:
        return False
    conditions = status.get("conditions", []) or []
    return any(
        c.get("type") == "Available" and c.get("status") == "True"
        for c in conditions
    )
