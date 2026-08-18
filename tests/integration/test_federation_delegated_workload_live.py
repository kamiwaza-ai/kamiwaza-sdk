"""Required shared-IDP delegated job and approved-package edge."""

from __future__ import annotations

import json
import os
import re
import shlex
import uuid
from typing import Any
from urllib.parse import quote

import pytest

from .test_federation_shared_idp_gated_retrieval_live import (
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

_IMPORT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def _environment_string_list(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        pytest.skip(
            "delegated workload package fixture is not configured; set " + name
        )
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{name} must contain a JSON string list: {exc}")
    if not isinstance(values, list):
        pytest.fail(f"{name} must contain a JSON list")
    if any(not isinstance(value, str) for value in values):
        pytest.fail(f"{name} must contain only strings")
    return tuple(value.strip() for value in values)


def _delegated_package_config() -> tuple[tuple[str, ...], tuple[str, ...]]:
    coordinates = _environment_string_list("KAMIWAZA_DELEGATED_TEST_PACKAGES_JSON")
    import_names = _environment_string_list("KAMIWAZA_DELEGATED_TEST_IMPORTS_JSON")
    if len(coordinates) < 2:
        pytest.fail("delegated workload edge requires at least two dependencies")
    if len(coordinates) != len(import_names):
        pytest.fail("delegated package and import lists must have equal lengths")
    for import_name in import_names:
        if _IMPORT_NAME.fullmatch(import_name) is None:
            pytest.fail("delegated test import is not a Python import name")
    return coordinates, import_names


def _assert_delegated_result(
    result: Any, marker: str, import_names: tuple[str, ...]
) -> None:
    assert result.status == "SUCCEEDED", result
    payload = result.result if isinstance(result.result, dict) else {}
    assert payload.get("probe") == marker, result
    assert payload.get("package_imports") == list(import_names), result


def test_shared_idp_delegated_job_installs_approved_package(
    request: pytest.FixtureRequest,
) -> None:
    """Route one delegated RayJob and import an operator-approved dependency."""
    wiring: dict[str, Any] = request.getfixturevalue("shared_idp_gated_pair")
    persona, _token = _active_persona_session(wiring["personas"]["U"])
    coordinates, import_names = _delegated_package_config()
    marker = f"eng8454-{uuid.uuid4().hex}"
    script = (
        "import importlib, json\n"
        f"names = {import_names!r}\n"
        "modules = [importlib.import_module(name).__name__ for name in names]\n"
        "payload = {"
        f"'probe': {marker!r}, 'package_imports': modules"
        "}\n"
        "print('KZ_MESH_RUN_ON_JSON::' + json.dumps(payload))\n"
    )

    result = _required_mesh_call(
        lambda: persona.jobs.run(
            entrypoint="python3 -c " + shlex.quote(script),
            target_cluster=wiring["name"],
            timeout_seconds=300,
            recoverable=True,
            delegated_access={
                "datasets": [
                    {
                        "urn": wiring["urn"],
                        "operations": ["discover"],
                    }
                ]
            },
            python_packages=list(coordinates),
        )
    )

    _assert_delegated_result(result, marker, import_names)
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
