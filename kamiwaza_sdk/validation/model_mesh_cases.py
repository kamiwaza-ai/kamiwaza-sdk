"""Execution cases for the SDK-owned federated model-mesh provider."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from kamiwaza_sdk.validation.federation_cases import RunContext, _issue_token
from kamiwaza_sdk.validation.federation_common import (
    close_client,
    elapsed_ms,
    optional_text,
    required_text,
    token_client,
)
from kamiwaza_sdk.validation.model_mesh_spec import MODEL_MESH_CASE_IDS
from kamiwaza_sdk.validation.models import CaseResult


def run_edge(context: RunContext) -> list[CaseResult]:
    """Run every planned model-mesh case and retain redacted failures."""

    return [_run_one(context, case_id) for case_id in context.selected.case_ids]


def _run_one(context: RunContext, case_id: str) -> CaseResult:
    started = time.monotonic()
    try:
        handler = {
            "model-grant-authorized": _run_authorized_discovery,
            "model-discovery-stable-identity": _run_stable_discovery,
            "model-remote-chat-provenance": _run_remote_chat,
            "model-unauthorized-negative": _run_unauthorized,
        }.get(case_id)
        if handler is None or case_id not in MODEL_MESH_CASE_IDS:
            raise ValueError("model-mesh case is not registered")
        handler(context)
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


def _run_authorized_discovery(context: RunContext) -> None:
    body = _remote_models(context, "fed-clr-u")
    _assert_model_visible(body, context, "authorized model grant did not expose model")


def _run_stable_discovery(context: RunContext) -> None:
    body = _remote_models(context, "fed-clr-u")
    _assert_model_visible(
        body, context, "remote model discovery omitted stable model identity"
    )
    rows = _model_rows(body)
    repository = required_text(context.params, "model_repository")
    if not any(str(row.get("repo_modelId") or "") == repository for row in rows):
        raise AssertionError("remote model discovery returned an unexpected repository")


def _run_remote_chat(context: RunContext) -> None:
    token = _issue_token(context, "fed-clr-u")
    persona = token_client(context.initiator_base, token)
    name = quote(_mesh_target(context), safe="")
    deployment_id = required_text(context.params, "deployment_id")
    served_model_id = required_text(context.params, "served_model_id")
    try:
        response = persona._request(
            "POST",
            f"/mesh/{name}/runtime/models/{quote(deployment_id, safe='')}/v1/chat/completions",
            json={
                "model": served_model_id,
                "messages": [
                    {"role": "user", "content": "kamiwaza model-mesh validation"}
                ],
                "temperature": 0,
                "max_tokens": 8,
            },
        )
        if not _response_has_content(response):
            raise AssertionError("remote runtime returned no assistant content")
    finally:
        close_client(persona)


def _run_unauthorized(context: RunContext) -> None:
    body = _remote_models(context, "fed-clr-s")
    if _body_status(body) in {401, 403}:
        return
    if any(
        str(row.get("id") or "") == required_text(context.params, "model_id")
        for row in _model_rows(body)
    ):
        raise AssertionError("model without a receiver grant was discoverable")


def _remote_models(context: RunContext, username: str) -> Any:
    token = _issue_token(context, username)
    persona = token_client(context.initiator_base, token)
    name = quote(_mesh_target(context), safe="")
    try:
        return persona._request("GET", f"/mesh/{name}/api/models/")
    except Exception as exc:
        # The negative case is expected to fail at the receiver's authz
        # boundary for a brokered identity with no model viewer tuple. Keep
        # the status-bearing exception so the case can distinguish a correct
        # 401/403 from a transport or server failure; authorized personas
        # still propagate every exception as a failed case.
        if username == "fed-clr-s" and getattr(exc, "status_code", None) in {
            401,
            403,
        }:
            return exc
        raise
    finally:
        close_client(persona)


def _assert_model_visible(body: Any, context: RunContext, message: str) -> None:
    rows = _model_rows(body)
    model_id = required_text(context.params, "model_id")
    if not any(str(row.get("id") or "") == model_id for row in rows):
        raise AssertionError(message)


def _model_rows(body: Any) -> list[Mapping[str, Any]]:
    values: Any = body.get("items") if isinstance(body, Mapping) else body
    if isinstance(body, Mapping) and values is None:
        values = body.get("data")
    if not isinstance(values, list):
        raise AssertionError("remote model discovery returned an invalid collection")
    return [item for item in values if isinstance(item, Mapping)]


def _response_has_content(body: Any) -> bool:
    if not isinstance(body, Mapping):
        return False
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    first = choices[0]
    if not isinstance(first, Mapping):
        return False
    message = first.get("message")
    return isinstance(message, Mapping) and bool(
        str(message.get("content") or "").strip()
    )


def _body_status(body: Any) -> int | None:
    return getattr(body, "status_code", None) if not isinstance(body, Mapping) else None


def _mesh_target(context: RunContext) -> str:
    """Select the owned initiator federation deterministically.

    Federation names are human-readable remote-cluster names and can be
    duplicated after a failed teardown.  The API also accepts the exact
    initiator federation UUID, which is the only stable selector for an owned
    run.  Keep the name fallback for older state snapshots.
    """

    return optional_text(context.params, "initiator_federation_id") or required_text(
        context.params, "federation_name"
    )
