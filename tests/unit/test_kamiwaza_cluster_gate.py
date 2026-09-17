"""T5.6 expand — ClusterAPI execution-gate binding on canonical surface.

WS-M3.2 test migration (T7.15 / ENG-5049). Customer-facing M3 demo surface:

    kz.cluster.set_execution_gate(type, config={}) -> ExecutionGateBinding
    kz.cluster.get_execution_gate()                -> ExecutionGateBinding
    kz.cluster.clear_execution_gate()              -> None

Server-side correlate: §4.2.4 / T2.4 at /api/cluster/execution-gate.
"""

from __future__ import annotations

import pytest


def test_set_execution_gate_puts_to_cluster_endpoint(mock_client) -> None:
    from kamiwaza_sdk.schemas.federation import ExecutionGateBinding
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "PUT",
        "/cluster/execution-gate",
        {
            "type": "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate",
            "config": {},
            "gate_name": "allow_all_execution_gate",
            "kind": "execution",
        },
    )

    binding = ClusterAPI(client=mock_client).set_execution_gate(
        type="kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate",
    )
    assert isinstance(binding, ExecutionGateBinding)
    assert binding.kind == "execution"
    assert binding.gate_name == "allow_all_execution_gate"

    method, path, kwargs = mock_client.calls[0]
    assert method == "PUT"
    assert path == "/cluster/execution-gate"
    assert kwargs.get("json") == {
        "type": "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate",
        "config": {},
    }


def test_set_execution_gate_forwards_config(mock_client) -> None:
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "PUT",
        "/cluster/execution-gate",
        {
            "type": "my_gate.MyExecutionGate",
            "config": {"min_clearance": "S"},
            "gate_name": "my-gate",
            "kind": "execution",
        },
    )

    ClusterAPI(client=mock_client).set_execution_gate(
        type="my_gate.MyExecutionGate", config={"min_clearance": "S"}
    )

    _method, _path, kwargs = mock_client.calls[0]
    assert kwargs.get("json", {}).get("config") == {"min_clearance": "S"}


def test_get_execution_gate_returns_binding(mock_client) -> None:
    from kamiwaza_sdk.schemas.federation import ExecutionGateBinding
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "GET",
        "/cluster/execution-gate",
        {"type": "g.G", "config": {}, "gate_name": "g", "kind": "execution"},
    )

    binding = ClusterAPI(client=mock_client).get_execution_gate()
    assert isinstance(binding, ExecutionGateBinding)


def test_get_execution_gate_raises_on_404_not_configured(mock_client) -> None:
    from kamiwaza_sdk.exceptions import KamiwazaError
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.raise_on(
        "GET",
        "/cluster/execution-gate",
        KamiwazaError("not_configured", status_code=404),
    )

    with pytest.raises(KamiwazaError):
        ClusterAPI(client=mock_client).get_execution_gate()


def test_clear_execution_gate_sends_delete(mock_client) -> None:
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "DELETE", "/cluster/execution-gate", {"deleted": True, "previous_type": "g.G"}
    )
    ClusterAPI(client=mock_client).clear_execution_gate()
