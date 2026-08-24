"""Required shared-IDP delegated job and approved-package edge."""

from __future__ import annotations

import json
import os
import re
import shlex
import time
import uuid
from typing import Any
from urllib.parse import quote

import pytest

from kamiwaza_sdk.schemas.delegated_jobs import normalize_python_packages

from .test_federation_shared_idp_gated_retrieval_live import (
    _receiver_prereqs,  # noqa: F401 - dependency of imported live fixture
    _active_persona_session,
    _assert_receiver_job_provenance,
    _required_mesh_call,
    shared_idp_gated_pair,  # noqa: F401 - imported fixture
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.requires_two_clusters,
    pytest.mark.requires_shared_idp,
    pytest.mark.requires_owned_shared_realm,
    pytest.mark.requires_delegated_workload,
]

_IMPORT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_TEST_CLASSIFICATION = "U"
_RESULT_MARKER_WAIT_SECONDS = 15.0
_RESULT_MARKER_POLL_SECONDS = 1.0


def _environment_string_list(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        pytest.skip("delegated workload package fixture is not configured; set " + name)
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{name} must contain a JSON string list: {exc}")
    if not isinstance(values, list):
        pytest.fail(f"{name} must contain a JSON list")
    if any(not isinstance(value, str) for value in values):
        pytest.fail(f"{name} must contain only strings")
    return tuple(value.strip() for value in values)


def _delegated_package_config() -> tuple[
    tuple[str, ...], tuple[str, ...], dict[str, str]
]:
    coordinates = normalize_python_packages(
        list(_environment_string_list("KAMIWAZA_DELEGATED_TEST_PACKAGES_JSON"))
    )
    import_names = _environment_string_list("KAMIWAZA_DELEGATED_TEST_IMPORTS_JSON")
    if len(coordinates) < 2:
        pytest.fail("delegated workload edge requires at least two dependencies")
    if len(coordinates) != len(import_names):
        pytest.fail("delegated package and import lists must have equal lengths")
    for import_name in import_names:
        if _IMPORT_NAME.fullmatch(import_name) is None:
            pytest.fail("delegated test import is not a Python import name")
    expected_versions = dict(
        coordinate.split("==", maxsplit=1) for coordinate in coordinates
    )
    return coordinates, import_names, expected_versions


def _assert_package_fixture_changes_environment(
    result: Any,
    marker: str,
    expected_versions: dict[str, str],
) -> None:
    record = _require_gated_result_record(result)
    assert record.get("probe") == marker, result
    installed = record.get("package_versions")
    assert isinstance(installed, dict), result
    assert any(
        installed.get(name) != version for name, version in expected_versions.items()
    ), "base image already contains every exact package fixture"


def _assert_delegated_result(
    result: Any,
    marker: str,
    import_names: tuple[str, ...],
    expected_versions: dict[str, str],
) -> None:
    record = _require_gated_result_record(result)
    assert record.get("probe") == marker, result
    assert record.get("package_imports") == list(import_names), result
    assert record.get("package_versions") == expected_versions, result


def _require_gated_result_record(result: Any) -> dict[str, Any]:
    assert result.status == "SUCCEEDED", result
    envelope = result.result if isinstance(result.result, dict) else {}
    records = envelope.get("data")
    metadata = envelope.get("metadata")
    assert isinstance(records, list) and len(records) == 1, result
    assert isinstance(records[0], dict), result
    assert isinstance(metadata, dict), result
    assert isinstance(metadata.get("gate_audit"), list), result
    assert len(metadata["gate_audit"]) == 1, result
    assert records[0].get("classification") == _TEST_CLASSIFICATION, result
    return records[0]


def _await_delegated_result(persona: Any, result: Any, target_cluster: str) -> Any:
    """Wait briefly for KubeRay's submitter log to expose the result marker."""
    deadline = time.monotonic() + _RESULT_MARKER_WAIT_SECONDS
    while result.result is None:
        if time.monotonic() >= deadline:
            return result
        time.sleep(_RESULT_MARKER_POLL_SECONDS)
        result = _required_mesh_call(
            lambda: persona.jobs.wait(
                str(result.job_id),
                timeout=int(_RESULT_MARKER_WAIT_SECONDS),
                target_cluster=target_cluster,
            )
        )
    return result


def test_shared_idp_delegated_job_installs_approved_package(
    request: pytest.FixtureRequest,
) -> None:
    """Route one delegated RayJob and import an operator-approved dependency."""
    wiring: dict[str, Any] = request.getfixturevalue("shared_idp_gated_pair")
    persona, _token = _active_persona_session(wiring["personas"]["U"])
    coordinates, import_names, expected_versions = _delegated_package_config()
    delegated_access = {
        "datasets": [
            {
                "urn": wiring["urn"],
                "operations": ["discover"],
            }
        ]
    }
    baseline_marker = f"eng8454-base-{uuid.uuid4().hex}"
    baseline_script = (
        "import importlib.metadata, json\n"
        f"packages = {tuple(expected_versions)!r}\n"
        "versions = {}\n"
        "for name in packages:\n"
        "    try:\n"
        "        versions[name] = importlib.metadata.version(name)\n"
        "    except importlib.metadata.PackageNotFoundError:\n"
        "        versions[name] = None\n"
        f"payload = [{{'classification': {_TEST_CLASSIFICATION!r}, "
        f"'probe': {baseline_marker!r}, 'package_versions': versions}}]\n"
        "print('KZ_MESH_RUN_ON_JSON::' + json.dumps(payload))\n"
    )
    baseline = _required_mesh_call(
        lambda: persona.jobs.run(
            entrypoint="python3 -c " + shlex.quote(baseline_script),
            target_cluster=wiring["name"],
            timeout_seconds=300,
            recoverable=True,
            delegated_access=delegated_access,
        )
    )
    baseline = _await_delegated_result(persona, baseline, wiring["name"])
    _assert_package_fixture_changes_environment(
        baseline,
        baseline_marker,
        expected_versions,
    )

    marker = f"eng8454-{uuid.uuid4().hex}"
    script = (
        "import importlib, importlib.metadata, json\n"
        f"names = {import_names!r}\n"
        f"packages = {tuple(expected_versions)!r}\n"
        "modules = [importlib.import_module(name).__name__ for name in names]\n"
        "versions = {name: importlib.metadata.version(name) for name in packages}\n"
        f"payload = [{{'classification': {_TEST_CLASSIFICATION!r}, "
        f"'probe': {marker!r}, 'package_imports': modules, "
        "'package_versions': versions}]\n"
        "print('KZ_MESH_RUN_ON_JSON::' + json.dumps(payload))\n"
    )

    result = _required_mesh_call(
        lambda: persona.jobs.run(
            entrypoint="python3 -c " + shlex.quote(script),
            target_cluster=wiring["name"],
            timeout_seconds=300,
            recoverable=True,
            delegated_access=delegated_access,
            python_packages=list(coordinates),
        )
    )
    result = _await_delegated_result(persona, result, wiring["name"])

    _assert_delegated_result(result, marker, import_names, expected_versions)
    selector = quote(wiring["name"], safe="")
    status = persona._request(
        "GET",
        f"/mesh/{selector}/api/cluster/jobs/{result.job_id}/status",
    )
    _assert_receiver_job_provenance(
        status,
        str(result.job_id),
        wiring["source_cluster_id"],
        wiring["receiver_cluster_id"],
    )
