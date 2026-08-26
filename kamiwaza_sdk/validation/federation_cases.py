"""Execution cases for the owned shared-IdP federation provider."""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests  # type: ignore[import-untyped]

from kamiwaza_sdk.services.federation_credentials import federation_credential_headers
from kamiwaza_sdk.validation.federation_common import (
    close_client,
    elapsed_ms,
    required_text,
    token_client,
)
from kamiwaza_sdk.validation.federation_fixture import (
    KNOWN,
    PERSONAS,
    TENANT_NEGATIVE_PERSONAS,
    UNONBOARDED_PERSONA,
    records,
)
from kamiwaza_sdk.validation.federation_spec import SHARED_REALM_CLIENT_ID
from kamiwaza_sdk.validation.models import CaseResult, ResolvedScenario
from kamiwaza_sdk.validation.provider import ProviderContractError


@dataclass(frozen=True)
class RunContext:
    selected: ResolvedScenario
    params: Mapping[str, Any]
    initiator: Any
    receiver: Any
    admin: Any
    password: str
    initiator_base: str


@dataclass(frozen=True)
class RetrievalRequest:
    persona: Any
    base_url: str
    token: str
    federation_name: str
    dataset_urn: str
    job_id: Any = None
    credential_headers: Mapping[str, str] | None = None


@dataclass(frozen=True)
class TenantDenialRequest:
    initiator: Any
    base_url: str
    token: str
    federation_name: str
    dataset_urn: str
    expected_status: int


def run_edge(
    context: RunContext,
    *,
    mesh_retrieve: Callable[[RetrievalRequest], tuple[list[dict[str, Any]], list[dict[str, Any]]]]
    | None = None,
    assert_tenant_denial: Callable[[TenantDenialRequest], None] | None = None,
    make_client: Callable[[str, str], Any] | None = None,
) -> list[CaseResult]:
    return [
        _run_one_case(
            context,
            case_id,
            mesh_retrieve=mesh_retrieve,
            assert_tenant_denial=assert_tenant_denial,
            make_client=make_client,
        )
        for case_id in context.selected.case_ids
    ]


def _run_one_case(
    context: RunContext,
    case_id: str,
    *,
    mesh_retrieve: Callable[[RetrievalRequest], tuple[list[dict[str, Any]], list[dict[str, Any]]]]
    | None,
    assert_tenant_denial: Callable[[TenantDenialRequest], None] | None,
    make_client: Callable[[str, str], Any] | None,
) -> CaseResult:
    started = time.monotonic()
    try:
        _dispatch_case(
            context,
            case_id,
            mesh_retrieve=mesh_retrieve,
            assert_tenant_denial=assert_tenant_denial,
            make_client=make_client,
        )
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


def _dispatch_case(
    context: RunContext,
    case_id: str,
    *,
    mesh_retrieve: Callable[[RetrievalRequest], tuple[list[dict[str, Any]], list[dict[str, Any]]]]
    | None,
    assert_tenant_denial: Callable[[TenantDenialRequest], None] | None,
    make_client: Callable[[str, str], Any] | None,
) -> None:
    if case_id.startswith("retrieval-clearance-"):
        _run_clearance_case(
            context,
            case_id.rsplit("-", 1)[-1].upper(),
            mesh_retrieve=mesh_retrieve,
            make_client=make_client,
        )
        return
    if case_id.startswith("retrieval-invalid-tenant-"):
        _run_tenant_case(
            context,
            case_id.removeprefix("retrieval-invalid-tenant-"),
            assert_tenant_denial=assert_tenant_denial,
        )
        return
    handler = {
        "dataset-list-authorized-fixture": _run_dataset_case,
        "job-reaches-receiver-marker": _run_job_case,
        "unonboarded-user-rejected": _run_unonboarded_case,
    }.get(case_id)
    if handler is None:
        raise ProviderContractError("provider case is not registered")
    if handler is _run_dataset_case:
        _run_dataset_case(context, make_client=make_client)
    elif handler is _run_job_case:
        _run_job_case(context, make_client=make_client)
    else:
        _run_unonboarded_case(context, make_client=make_client)


def _run_clearance_case(
    context: RunContext,
    clearance: str,
    *,
    mesh_retrieve: Callable[[RetrievalRequest], tuple[list[dict[str, Any]], list[dict[str, Any]]]]
    | None,
    make_client: Callable[[str, str], Any] | None,
) -> None:
    username = PERSONAS[clearance]
    token = _issue_token(context, username)
    persona = (make_client or token_client)(context.initiator_base, token)
    try:
        rows, audits = (mesh_retrieve or _mesh_retrieve)(
            RetrievalRequest(
                persona=persona,
                base_url=context.initiator_base,
                token=token,
                federation_name=required_text(context.params, "federation_name"),
                dataset_urn=required_text(context.params, "dataset_urn"),
            )
        )
    finally:
        close_client(persona)
    included, allowed = KNOWN[clearance]
    expected = [row for row in records() if row["classification"] in allowed]
    if len(rows) != included or _sort_rows(rows) != _sort_rows(expected):
        raise AssertionError("gated retrieval returned unexpected rows")
    if not audits:
        raise AssertionError("gated retrieval emitted no gate audit")


def _run_tenant_case(
    context: RunContext,
    case_name: str,
    *,
    assert_tenant_denial: Callable[[TenantDenialRequest], None] | None,
) -> None:
    username, _attrs = TENANT_NEGATIVE_PERSONAS[case_name]
    token = _issue_token(context, username)
    expected_status = 403 if case_name == "canonical-nondefault" else 401
    (assert_tenant_denial or _assert_tenant_denial)(
        TenantDenialRequest(
            initiator=context.initiator,
            base_url=context.initiator_base,
            token=token,
            federation_name=required_text(context.params, "federation_name"),
            dataset_urn=required_text(context.params, "dataset_urn"),
            expected_status=expected_status,
        )
    )


def _run_dataset_case(
    context: RunContext, *, make_client: Callable[[str, str], Any] | None
) -> None:
    token = _issue_token(context, PERSONAS["U"])
    persona = (make_client or token_client)(context.initiator_base, token)
    try:
        datasets = persona.catalog.datasets.list(
            target_cluster=required_text(context.params, "federation_name")
        )
        urn = required_text(context.params, "dataset_urn")
        if [str(item.urn) for item in datasets] != [urn]:
            raise AssertionError("mesh dataset listing was not authorization scoped")
    finally:
        close_client(persona)


def _run_job_case(
    context: RunContext, *, make_client: Callable[[str, str], Any] | None
) -> None:
    token = _issue_token(context, PERSONAS["U"])
    persona = (make_client or token_client)(context.initiator_base, token)
    marker = f"kamiwaza-validation-{context.selected.target_id}"
    script = (
        "import json; print('KZ_MESH_RUN_ON_JSON::' + json.dumps({'probe': "
        + repr(marker)
        + "}))"
    )
    try:
        result = persona.jobs.run(
            entrypoint="python3 -c " + shlex.quote(script),
            target_cluster=required_text(context.params, "federation_name"),
            timeout_seconds=120,
            recoverable=True,
        )
        if getattr(result, "status", None) != "SUCCEEDED":
            raise AssertionError("mesh job did not succeed")
        probe = getattr(result, "probe", None)
        if probe is None and isinstance(getattr(result, "result", None), dict):
            probe = result.result.get("probe")
        if probe != marker:
            raise AssertionError("mesh job marker did not round-trip")
    finally:
        close_client(persona)


def _run_unonboarded_case(
    context: RunContext, *, make_client: Callable[[str, str], Any] | None
) -> None:
    token = _issue_token(context, UNONBOARDED_PERSONA)
    persona = (make_client or token_client)(context.initiator_base, token)
    name = required_text(context.params, "federation_name")
    try:
        try:
            persona._request("GET", f"/mesh/{quote(name, safe='')}/api/cluster/diagnose")
        except Exception as exc:
            if getattr(exc, "status_code", None) != 403:
                raise
            if _error_reason(exc) != "unauthorized_brokered_user":
                raise AssertionError("unexpected unonboarded-user denial")
            return
        raise AssertionError("unonboarded shared-IDP user was admitted")
    finally:
        close_client(persona)


def _issue_token(context: RunContext, username: str) -> str:
    return context.admin.ropc_token(
        required_text(context.params, "realm"),
        SHARED_REALM_CLIENT_ID,
        username,
        context.password,
    )


def _sort_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(rows, key=lambda row: str(row.get("id", "")))


def _mesh_retrieve(request: RetrievalRequest) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    credential_headers = federation_credential_headers(request.federation_name)
    job = request.persona._request(
        "POST",
        f"/mesh/{quote(request.federation_name, safe='')}/api/retrieval/jobs",
        json={"dataset_urn": request.dataset_urn},
        **({"headers": credential_headers} if credential_headers else {}),
    )
    job_id = _retrieval_job_id(job)
    if not job_id:
        raise RuntimeError("mesh retrieval returned no job ID")
    response = _retrieval_stream(
        RetrievalRequest(
            persona=request.persona,
            base_url=request.base_url,
            token=request.token,
            federation_name=request.federation_name,
            dataset_urn=request.dataset_urn,
            job_id=job_id,
            credential_headers=credential_headers,
        )
    )
    try:
        return _collect_retrieval_stream(response)
    finally:
        response.close()


def _retrieval_job_id(job: Any) -> Any:
    if isinstance(job, Mapping):
        return job.get("job_id") or job.get("id")
    return getattr(job, "job_id", None)


def _retrieval_stream(request: RetrievalRequest) -> Any:
    headers = request.credential_headers or {}
    response = requests.get(
        f"{request.base_url}/mesh/{quote(request.federation_name, safe='')}/"
        f"api/retrieval/jobs/{quote(str(request.job_id), safe='')}/stream",
        headers={
            "Authorization": f"Bearer {request.token}",
            "Accept": "text/event-stream",
            **headers,
        },
        stream=True,
        verify=getattr(request.persona.session, "verify", True),
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"mesh retrieval stream returned HTTP {response.status_code}")
    return response


def _collect_retrieval_stream(response: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    event: str | None = None
    data_lines: list[str] = []
    for raw in response.iter_lines(decode_unicode=True):
        if raw:
            event, data_lines = _parse_sse_line(raw, event, data_lines)
        else:
            rows, audits = _consume_sse_chunk(event, data_lines, rows, audits)
            event, data_lines = None, []
    return rows, audits


def _parse_sse_line(raw: str, event: str | None, data_lines: list[str]) -> tuple[str | None, list[str]]:
    if raw.startswith("event:"):
        return raw[6:].strip(), data_lines
    if raw.startswith("data:"):
        return event, [*data_lines, raw[5:].lstrip()]
    return event, data_lines


def _consume_sse_chunk(
    event: str | None,
    data_lines: Sequence[str],
    rows: list[dict[str, Any]],
    audits: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if event != "chunk" or not data_lines:
        return rows, audits
    payload = json.loads("\n".join(data_lines))
    rows.extend(_payload_rows(payload))
    audits.extend(_payload_audits(payload))
    return rows, audits


def _payload_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    rows = payload.get("data") or payload.get("records") or payload.get("rows") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _payload_audits(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    metadata = payload.get("metadata")
    audit = metadata.get("gate_audit") if isinstance(metadata, Mapping) else None
    if isinstance(audit, list):
        return [item for item in audit if isinstance(item, dict)]
    return [audit] if isinstance(audit, dict) else []


def _assert_tenant_denial(request: TenantDenialRequest) -> None:
    response = _tenant_denial_response(request)
    try:
        if response.status_code != request.expected_status:
            raise AssertionError("tenant-negative status did not match contract")
        try:
            payload = response.json()
        except ValueError:
            raise AssertionError("tenant-negative response was not JSON") from None
    finally:
        response.close()
    reason = payload.get("detail") if isinstance(payload, Mapping) else None
    expected_reason = "mesh_tenant_not_admitted" if request.expected_status == 403 else "tenant_required"
    if reason != expected_reason:
        raise AssertionError("tenant-negative reason did not match contract")


def _tenant_denial_response(request: TenantDenialRequest) -> Any:
    headers = {
        "Authorization": f"Bearer {request.token}",
        **federation_credential_headers(request.federation_name),
    }
    return request.initiator.session.post(
        f"{request.base_url}/mesh/{quote(request.federation_name, safe='')}/"
        "api/retrieval/jobs",
        json={"dataset_urn": request.dataset_urn},
        headers=headers,
        verify=request.initiator.session.verify,
        timeout=120,
    )


def _error_reason(exc: Exception) -> str | None:
    body = getattr(exc, "response_data", None) or getattr(exc, "body", None)
    if isinstance(body, Mapping):
        detail = body.get("detail", body)
        if isinstance(detail, Mapping) and isinstance(detail.get("reason"), str):
            return detail["reason"]
    return None
