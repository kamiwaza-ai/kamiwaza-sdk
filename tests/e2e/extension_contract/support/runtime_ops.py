from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import requests
from kamiwaza_sdk.exceptions import APIError

from .common import DEFAULT_DEPLOYMENT_ARTIFACT_DIR, REPO_ROOT, logger


def deploy_app(harness: Any, template_id: str, contract: Any) -> dict[str, Any]:
    return harness.client.post(
        "/apps/deploy_app",
        json={
            "name": f"{contract.extension_name}-poc-{uuid.uuid4().hex[:8]}",
            "template_id": template_id,
            "min_copies": 1,
            "starting_copies": 1,
            "env_vars": harness.deployment_env_vars(contract),
        },
    )


def get_deployment(harness: Any, deployment_id: str) -> dict[str, Any]:
    return harness.client.get(f"/apps/deployment/{deployment_id}")


def deployment_diagnostics(harness: Any, deployment_id: str) -> dict[str, Any]:
    deployment = get_deployment(harness, deployment_id)
    if str(deployment.get("id") or "") != deployment_id or not str(deployment.get("status") or "").strip() or not str(deployment.get("access_path") or "").strip():
        pytest.fail(f"Deployment diagnostics for {deployment_id} are incomplete: {deployment}")
    instances = deployment.get("instances")
    if instances is not None and not isinstance(instances, list):
        pytest.fail(f"Deployment diagnostics for {deployment_id} returned non-list instances: {deployment}")
    return deployment


def wait_for_deployment_logs(harness: Any, deployment_id: str, *, marker: str, request_id: str | None = None) -> dict[str, Any]:
    deadline = time.time() + harness.settings.probe_timeout_seconds
    last_summary = "no logs yet"
    while time.time() < deadline:
        try:
            payload = harness.client.get(f"/logger/deployment/{deployment_id}/logs")
        except (APIError, requests.RequestException) as exc:
            last_summary = str(exc)
            time.sleep(2)
            continue
        logs = payload.get("logs")
        if not isinstance(logs, list):
            last_summary = f"unexpected log payload: {payload}"
            time.sleep(2)
            continue
        combined = "\n".join(str(line) for line in logs)
        marker_found = marker in combined
        request_found = request_id is None or request_id in combined
        if marker_found and request_found:
            return payload
        last_summary = f"total_lines={payload.get('total_lines')} marker_found={marker_found} request_id_found={request_found}"
        time.sleep(2)
    pytest.fail(f"Did not observe deployment log marker for {deployment_id}. Expected marker={marker!r} request_id={request_id!r}. Last result: {last_summary}")


def wait_for_deployment(harness: Any, deployment_id: str) -> dict[str, Any]:
    deadline = time.time() + harness.settings.deployment_timeout_seconds
    last_status = "unknown"
    while time.time() < deadline:
        try:
            deployment = get_deployment(harness, deployment_id)
        except (APIError, requests.RequestException) as exc:
            last_status = str(exc)
            time.sleep(2)
            continue
        last_status = str(deployment.get("status", "unknown")).upper()
        if last_status in {"DEPLOYED", "RUNNING"} and deployment.get("access_path"):
            return deployment
        if last_status in {"FAILED", "ERROR", "STOPPED"}:
            pytest.fail(f"Deployment {deployment_id} entered terminal status {last_status}: {deployment}")
        time.sleep(5)
    pytest.fail(f"Deployment {deployment_id} did not become ready. Last status: {last_status}")


def app_url(harness: Any, deployment: dict[str, Any], contract: Any) -> str:
    return f"{harness.settings.origin.rstrip('/')}{_access_path(deployment, contract)}"


def readiness_url(harness: Any, deployment: dict[str, Any], contract: Any) -> str:
    return f"{app_url(harness, deployment, contract)}{contract.readiness_path}"


def smoke_url(harness: Any, deployment: dict[str, Any], contract: Any) -> str:
    return f"{app_url(harness, deployment, contract)}{contract.smoke_path}"


def deployment_url(harness: Any, deployment: dict[str, Any], contract: Any) -> str:
    return f"{app_url(harness, deployment, contract)}{contract.root_probe_path}"


def write_deployment_artifact(harness: Any, deployment: dict[str, Any], contract: Any) -> Path:
    output_dir = Path(os.getenv("LIVE_EXTENSION_OUTPUT_DIR") or str(DEFAULT_DEPLOYMENT_ARTIFACT_DIR)).expanduser()
    output_dir = output_dir if output_dir.is_absolute() else REPO_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / f"{contract.extension_name}.json"
    payload = {
        "extension_name": contract.extension_name,
        "template_name": contract.template_name,
        "template_version": contract.resolved_template_version(),
        "deployment_id": deployment.get("id"),
        "deployment_name": deployment.get("name"),
        "status": deployment.get("status"),
        "access_path": deployment.get("access_path"),
        "app_url": app_url(harness, deployment, contract),
        "readiness_url": readiness_url(harness, deployment, contract),
        "smoke_url": smoke_url(harness, deployment, contract),
        "requires_auth": contract.requires_auth,
    }
    artifact_path.write_text(f"{json.dumps(payload, indent=2, sort_keys=True)}\n", encoding="utf-8")
    return artifact_path


def wait_for_http_ok(harness: Any, url: str, headers: dict[str, str] | None = None) -> requests.Response:
    return _wait_for_response(harness, url, headers=headers)


def wait_for_json(harness: Any, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = _wait_for_response(harness, url, headers=headers)
    try:
        payload = response.json()
    except ValueError as exc:
        pytest.fail(f"Expected JSON from {url}, got: {response.text[:200]} ({exc})")
    if not isinstance(payload, dict):
        pytest.fail(f"Expected JSON object from {url}, got {type(payload).__name__}")
    return payload


def cleanup_deployment(harness: Any, deployment_id: str) -> None:
    for suffix, action in (("", "delete"), ("/purge", "purge")):
        try:
            harness.client.delete(f"/apps/deployment/{deployment_id}{suffix}")
        except (APIError, requests.RequestException) as exc:
            logger.warning("Failed to %s deployment %s: %s", action, deployment_id, exc)


def _wait_for_response(harness: Any, url: str, headers: dict[str, str] | None = None) -> requests.Response:
    deadline = time.time() + harness.settings.probe_timeout_seconds
    last_summary = "no response"
    while time.time() < deadline:
        try:
            response = harness.http.get(url, headers=headers, timeout=10)
        except requests.RequestException as exc:
            last_summary = str(exc)
            time.sleep(2)
            continue
        if response.status_code == 200:
            return response
        last_summary = f"status={response.status_code} body={response.text[:200].strip()}"
        time.sleep(2)
    pytest.fail(f"Probe never succeeded for {url}. Last result: {last_summary}")


def _access_path(deployment: dict[str, Any], contract: Any) -> str:
    access_path = str(deployment.get("access_path") or "").strip()
    if not access_path:
        pytest.fail(f"Deployment for {contract.extension_name} is missing access_path")
    return access_path if access_path.startswith("/") else f"/{access_path}"
