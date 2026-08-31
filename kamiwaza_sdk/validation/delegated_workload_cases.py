"""Execution cases for the SDK-owned delegated-workload provider."""

from __future__ import annotations

import shlex
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from kamiwaza_sdk.validation.delegated_workload_spec import DELEGATED_CASE_IDS
from kamiwaza_sdk.validation.federation_cases import RunContext, _issue_token
from kamiwaza_sdk.validation.federation_common import (
    close_client,
    elapsed_ms,
    required_text,
    token_client,
)
from kamiwaza_sdk.validation.models import CaseResult

_CLASSIFICATION = "U"
_RESULT_MARKER_WAIT_SECONDS = 15.0
_RESULT_MARKER_POLL_SECONDS = 1.0


@dataclass(frozen=True)
class _JobRequest:
    target: str
    delegated_access: Mapping[str, Any]
    packages: list[str] | None
    script: str


def run_edge(context: RunContext) -> list[CaseResult]:
    """Run the exact delegated-workload inventory."""

    return [_run_one(context, case_id) for case_id in context.selected.case_ids]


def _run_one(context: RunContext, case_id: str) -> CaseResult:
    started = time.monotonic()
    try:
        if case_id not in DELEGATED_CASE_IDS:
            raise ValueError("delegated-workload case is not registered")
        _run_approved_package_case(context)
    except Exception as exc:
        return CaseResult(
            target_id=context.selected.target_id,
            scenario_id=context.selected.scenario_id,
            case_id=case_id,
            status="failed",
            duration_ms=elapsed_ms(started),
            detail=f"{type(exc).__name__}: validation assertion failed",
        )
    return CaseResult(
        target_id=context.selected.target_id,
        scenario_id=context.selected.scenario_id,
        case_id=case_id,
        status="passed",
        duration_ms=elapsed_ms(started),
        detail=None,
    )


def _run_approved_package_case(context: RunContext) -> None:
    packages = _required_list(context.params, "python_packages")
    imports = _required_list(context.params, "package_imports")
    expected_versions = _required_mapping(context.params, "expected_package_versions")
    token = _issue_token(context, "fed-clr-u")
    persona = token_client(context.initiator_base, token)
    dataset = required_text(context.params, "dataset_urn")
    target = required_text(context.params, "federation_name")
    delegated_access = {"datasets": [{"urn": dataset, "operations": ["discover"]}]}
    baseline_marker = f"kz-delegated-base-{uuid.uuid4().hex}"
    try:
        baseline = _submit(
            persona,
            _JobRequest(
                target=target,
                delegated_access=delegated_access,
                packages=None,
                script=_baseline_script(expected_versions, baseline_marker),
            ),
        )
        _assert_baseline(baseline, baseline_marker, expected_versions)
        delegated_marker = f"kz-delegated-{uuid.uuid4().hex}"
        result = _submit(
            persona,
            _JobRequest(
                target=target,
                delegated_access=delegated_access,
                packages=packages,
                script=_delegated_script(imports, expected_versions, delegated_marker),
            ),
        )
        _assert_delegated(result, delegated_marker, imports, expected_versions)
        _assert_provenance(persona, target, result, context)
    finally:
        close_client(persona)


def _submit(
    persona: Any,
    request: _JobRequest,
) -> Any:
    result = persona.jobs.run(
        entrypoint="python3 -c " + shlex.quote(request.script),
        target_cluster=request.target,
        timeout_seconds=300,
        recoverable=True,
        delegated_access=request.delegated_access,
        **({"python_packages": request.packages} if request.packages else {}),
    )
    return _await_result(persona, result, request.target)


def _await_result(persona: Any, result: Any, target: str) -> Any:
    deadline = time.monotonic() + _RESULT_MARKER_WAIT_SECONDS
    while getattr(result, "result", None) is None:
        if time.monotonic() >= deadline:
            return result
        time.sleep(_RESULT_MARKER_POLL_SECONDS)
        result = persona.jobs.wait(
            str(result.job_id),
            timeout=int(_RESULT_MARKER_WAIT_SECONDS),
            target_cluster=target,
        )
    return result


def _baseline_script(expected_versions: Mapping[str, str], marker: str) -> str:
    names = tuple(expected_versions)
    return (
        "import importlib.metadata, json\n"
        f"packages = {names!r}\n"
        "versions = {}\n"
        "for name in packages:\n"
        "    try:\n"
        "        versions[name] = importlib.metadata.version(name)\n"
        "    except importlib.metadata.PackageNotFoundError:\n"
        "        versions[name] = None\n"
        f"payload = [{{'classification': {_CLASSIFICATION!r}, "
        f"'probe': {marker!r}, "
        "'package_versions': versions}]\n"
        "print('KZ_MESH_RUN_ON_JSON::' + json.dumps(payload))\n"
    )


def _delegated_script(
    imports: list[str], expected_versions: Mapping[str, str], marker: str
) -> str:
    names = tuple(expected_versions)
    return (
        "import importlib, importlib.metadata, json\n"
        f"names = {tuple(imports)!r}\n"
        f"packages = {names!r}\n"
        "modules = [importlib.import_module(name).__name__ for name in names]\n"
        "versions = {name: importlib.metadata.version(name) for name in packages}\n"
        f"payload = [{{'classification': {_CLASSIFICATION!r}, "
        f"'probe': {marker!r}, "
        "'package_imports': modules, 'package_versions': versions}]\n"
        "print('KZ_MESH_RUN_ON_JSON::' + json.dumps(payload))\n"
    )


def _assert_baseline(
    result: Any, marker: str, expected_versions: Mapping[str, str]
) -> None:
    record = _result_record(result)
    if record.get("probe") != marker:
        raise AssertionError("delegated baseline marker did not round-trip")
    installed = record.get("package_versions")
    if not isinstance(installed, Mapping) or not any(
        installed.get(name) != version for name, version in expected_versions.items()
    ):
        raise AssertionError("base image already contains every exact package fixture")


def _assert_delegated(
    result: Any,
    marker: str,
    imports: list[str],
    expected_versions: Mapping[str, str],
) -> None:
    record = _result_record(result)
    if record.get("probe") != marker:
        raise AssertionError("delegated job marker did not round-trip")
    if record.get("package_imports") != imports:
        raise AssertionError("delegated job imported an unexpected package set")
    if record.get("package_versions") != dict(expected_versions):
        raise AssertionError("delegated job installed unexpected package versions")


def _result_record(result: Any) -> Mapping[str, Any]:
    _require_success(result)
    envelope = _result_envelope(result)
    records = _result_records(envelope)
    _require_gate_audit(envelope)
    record = records[0]
    if not isinstance(record, Mapping):
        raise AssertionError("delegated job returned an invalid record")
    if record.get("classification") != _CLASSIFICATION:
        raise AssertionError("delegated job returned an invalid classification")
    return record


def _require_success(result: Any) -> None:
    if getattr(result, "status", None) != "SUCCEEDED":
        raise AssertionError("delegated job did not succeed")


def _result_envelope(result: Any) -> Mapping[str, Any]:
    envelope = result.result
    if not isinstance(envelope, Mapping):
        raise AssertionError("delegated job returned an invalid data envelope")
    return envelope


def _result_records(envelope: Mapping[str, Any]) -> list[Any]:
    records = envelope.get("data")
    if not isinstance(records, list) or len(records) != 1:
        raise AssertionError("delegated job returned an invalid data envelope")
    return records


def _require_gate_audit(envelope: Mapping[str, Any]) -> None:
    metadata = envelope.get("metadata")
    if not isinstance(metadata, Mapping):
        raise AssertionError("delegated job returned no gate audit")
    gate_audit = metadata.get("gate_audit")
    if not isinstance(gate_audit, list) or len(gate_audit) != 1:
        raise AssertionError("delegated job returned no gate audit")


def _assert_provenance(
    persona: Any, target: str, result: Any, context: RunContext
) -> None:
    status = persona._request(
        "GET",
        f"/mesh/{quote(target, safe='')}/api/cluster/jobs/{result.job_id}/status",
    )
    if not isinstance(status, Mapping):
        raise AssertionError("delegated job provenance response is invalid")
    if not _is_mesh_ingress(status, result):
        raise AssertionError("delegated job provenance did not identify mesh ingress")
    if not _source_matches_initiator(status, context):
        raise AssertionError("delegated job provenance source cluster is unexpected")


def _is_mesh_ingress(status: Mapping[str, Any], result: Any) -> bool:
    if str(status.get("id")) != str(result.job_id):
        return False
    if status.get("source") != "mesh":
        return False
    return str(status.get("source_cluster_id")) != str(
        status.get("receiver_cluster_id")
    )


def _source_matches_initiator(status: Mapping[str, Any], context: RunContext) -> bool:
    return str(status.get("source_cluster_id")) == str(
        context.params.get("initiator_cluster_id")
    )


def _required_list(values: Mapping[str, Any], key: str) -> list[str]:
    value = values.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"delegated selection field {key!r} is invalid")
    return list(value)


def _required_mapping(values: Mapping[str, Any], key: str) -> dict[str, str]:
    value = values.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"delegated selection field {key!r} is invalid")
    if any(not _is_string_pair(item) for item in value.items()):
        raise ValueError(f"delegated selection field {key!r} is invalid")
    return dict(value)


def _is_string_pair(item: tuple[object, object]) -> bool:
    name, version = item
    if not isinstance(name, str):
        return False
    return isinstance(version, str)
