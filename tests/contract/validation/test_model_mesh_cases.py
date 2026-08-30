"""Contract tests for exact federated model-mesh execution routes."""

from __future__ import annotations

from typing import Any

import pytest

from kamiwaza_sdk.validation import model_mesh_cases
from kamiwaza_sdk.validation.federation_cases import RunContext
from kamiwaza_sdk.validation.model_mesh_spec import (
    MODEL_MESH_CASE_IDS,
    MODEL_MESH_SCENARIO_ID,
)
from kamiwaza_sdk.validation.models import ResolvedScenario

pytestmark = pytest.mark.contract


class _Admin:
    def ropc_token(
        self, realm: str, client_id: str, username: str, password: str
    ) -> str:
        del realm, client_id, password
        return f"token:{username}"


class _Denied(Exception):
    status_code = 403


class _Persona:
    def __init__(
        self, username: str, calls: list[tuple[str, str, dict[str, Any]]]
    ) -> None:
        self.username = username
        self.calls = calls

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if method == "GET":
            return {
                "items": [
                    {
                        "id": "model-123",
                        "repo_modelId": "Qwen/Qwen3-0.6B-GGUF",
                    }
                ]
            }
        if self.username == "fed-clr-s":
            raise _Denied("model invoke denied")
        return {"choices": [{"message": {"content": "mesh response"}}]}

    def close(self) -> None:
        return None


def _context(calls: list[tuple[str, str, dict[str, Any]]]) -> RunContext:
    selected = ResolvedScenario(
        target_id="mesh-edge:sha256:test",
        cluster_id="edge-a",
        cluster_ids=("edge-a", "edge-b"),
        scenario_id=MODEL_MESH_SCENARIO_ID,
        required=True,
        case_ids=MODEL_MESH_CASE_IDS,
        redacted_parameters={
            "realm": "realm-1",
            "federation_name": "fed-edge",
            "initiator_federation_id": "initiator-fed-123",
            "model_id": "model-123",
            "model_repository": "Qwen/Qwen3-0.6B-GGUF",
            "deployment_id": "deploy-123",
            "served_model_id": "served-qwen",
        },
    )
    return RunContext(
        selected=selected,
        params=selected.redacted_parameters,
        initiator=object(),
        receiver=object(),
        admin=_Admin(),
        password="persona-password",
        initiator_base="https://edge-a.test/api",
    )


def test_model_mesh_cases_use_exact_catalog_and_runtime_chat_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def make_client(base_url: str, token: str) -> _Persona:
        assert base_url == "https://edge-a.test/api"
        return _Persona(token.removeprefix("token:"), calls)

    monkeypatch.setattr(model_mesh_cases, "token_client", make_client)
    evidence = model_mesh_cases.run_edge(_context(calls))

    assert [item.case_id for item in evidence] == list(MODEL_MESH_CASE_IDS)
    assert all(item.status == "passed" for item in evidence)
    assert [(method, path) for method, path, _kwargs in calls] == [
        ("GET", "/mesh/initiator-fed-123/api/models/"),
        ("GET", "/mesh/initiator-fed-123/api/models/"),
        (
            "POST",
            "/mesh/initiator-fed-123/runtime/models/deploy-123/v1/chat/completions",
        ),
        (
            "POST",
            "/mesh/initiator-fed-123/runtime/models/deploy-123/v1/chat/completions",
        ),
    ]
    chat_payload = calls[2][2]["json"]
    assert chat_payload["model"] == "served-qwen"
    assert chat_payload["messages"][0]["role"] == "user"
