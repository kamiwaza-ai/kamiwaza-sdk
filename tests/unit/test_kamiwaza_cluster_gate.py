"""T5.6 expand — kamiwaza.cluster execution-gate binding tests.

Adds set/get/clear execution-gate methods to ClusterAPI for the M3 demo:

    kz.cluster.set_execution_gate(type, config={}) -> ExecutionGateBinding
    kz.cluster.get_execution_gate()                -> ExecutionGateBinding
    kz.cluster.clear_execution_gate()              -> None

Server-side correlate: §4.2.4 / T2.4 at /api/cluster/execution-gate.
"""

from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_set_execution_gate_puts_to_cluster_endpoint(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import ExecutionGateBinding

    httpx_mock.add_response(
        method="PUT",
        url="https://kamiwaza.test/api/cluster/execution-gate",
        status_code=200,
        json={
            "type": "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate",
            "config": {},
            "gate_name": "allow_all_execution_gate",
            "kind": "execution",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    binding = client.cluster.set_execution_gate(
        type="kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate",
    )
    assert isinstance(binding, ExecutionGateBinding)
    assert binding.kind == "execution"
    assert binding.gate_name == "allow_all_execution_gate"

    request = httpx_mock.get_requests(method="PUT")[0]
    body = json.loads(request.content)
    assert body == {
        "type": "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate",
        "config": {},
    }


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_set_execution_gate_forwards_config(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="PUT",
        url="https://kamiwaza.test/api/cluster/execution-gate",
        status_code=200,
        json={
            "type": "my_gate.MyExecutionGate",
            "config": {"min_clearance": "S"},
            "gate_name": "my-gate",
            "kind": "execution",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.cluster.set_execution_gate(
        type="my_gate.MyExecutionGate", config={"min_clearance": "S"}
    )

    request = httpx_mock.get_requests(method="PUT")[0]
    body = json.loads(request.content)
    assert body["config"] == {"min_clearance": "S"}


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_get_execution_gate_returns_binding(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import ExecutionGateBinding

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/execution-gate",
        status_code=200,
        json={
            "type": "g.G",
            "config": {},
            "gate_name": "g",
            "kind": "execution",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    binding = client.cluster.get_execution_gate()
    assert isinstance(binding, ExecutionGateBinding)


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_get_execution_gate_raises_on_404_not_configured(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import KamiwazaError

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/execution-gate",
        status_code=404,
        json={"detail": {"reason": "not_configured"}},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    with pytest.raises(KamiwazaError):
        client.cluster.get_execution_gate()


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_clear_execution_gate_sends_delete(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="DELETE",
        url="https://kamiwaza.test/api/cluster/execution-gate",
        status_code=200,
        json={"deleted": True, "previous_type": "g.G"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.cluster.clear_execution_gate()
