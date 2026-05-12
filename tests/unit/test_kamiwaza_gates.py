"""T5.4 / ENG-4691 — kamiwaza.gates module tests.

Customer-facing surface for gate discovery per design §4.2.11:

    kz.gates.discover(classpath)   -> GateDiscovery

Server-side correlate: POST /api/authz/gates/discover (§4.2.3).

Full surface (set_gate, packages.*) is WS-M3 — this skeleton ships only
the discover() method.
"""

from __future__ import annotations

from typing import Any

import pytest


def test_kamiwaza_exposes_gates_attribute() -> None:
    """client.gates is the entry point for gate discovery."""
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    assert client.gates is not None


def test_gates_is_lazy_loaded() -> None:
    """Lazy-load per .ai/rules/sdk-patterns.md."""
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    a = client.gates
    b = client.gates
    assert a is b


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_discover_posts_to_server_with_classpath(httpx_mock: Any) -> None:
    """kz.gates.discover(classpath) POSTs to /api/authz/gates/discover."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import GateDiscovery

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/authz/gates/discover",
        status_code=200,
        json={
            "name": "AllowAllExecutionGate",
            "kind": "execution",
            "required_attributes": [],
            "config_schema": {},
            "classpath": (
                "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate"
            ),
            "location": "/.../default_gates.py:42",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.gates.discover(
        "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate"
    )

    assert isinstance(result, GateDiscovery)
    assert result.kind == "execution"
    assert result.name == "AllowAllExecutionGate"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_discover_passes_classpath_in_request_body(httpx_mock: Any) -> None:
    """The classpath is sent in the POST body — server's API contract."""
    from kamiwaza.client import Kamiwaza

    classpath = "my_policy.MyGate"

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/authz/gates/discover",
        status_code=200,
        json={
            "name": "my-gate",
            "kind": "execution",
            "required_attributes": [],
            "config_schema": {},
            "classpath": classpath,
            "location": "<unknown>",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.gates.discover(classpath)

    request = httpx_mock.get_requests(method="POST")[0]
    import json

    body = json.loads(request.content)
    assert body == {"classpath": classpath}


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_discover_surfaces_required_attributes_and_schema(
    httpx_mock: Any,
) -> None:
    """Customer-meaningful fields round-trip via the typed model."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/authz/gates/discover",
        status_code=200,
        json={
            "name": "classification-gate",
            "kind": "execution",
            "required_attributes": [
                {"name": "clearance", "kind": "string"},
                {"name": "country", "kind": "string"},
            ],
            "config_schema": {
                "type": "object",
                "properties": {"min_clearance": {"type": "string"}},
            },
            "classpath": "stub.ClassificationGate",
            "location": "<stub>",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.gates.discover("stub.ClassificationGate")

    assert len(result.required_attributes) == 2
    assert result.required_attributes[0]["name"] == "clearance"
    assert result.config_schema["properties"]["min_clearance"]["type"] == "string"
