"""WS-M2 demo-bullet platform scenarios (ENG-6949).

Lifts the API-level outcomes of ``kamiwaza-smoke.py`` ``cmd_m2`` / ``cmd_diagnose``
/ ``cmd_capabilities`` / ``cmd_gates_discover`` / ``cmd_operations`` into the SDK
live suite as the cohesive WS-M2 demo-bullet contract.

Already covered elsewhere and NOT duplicated here:
- cluster CRUD/read endpoints -> ``test_cluster_live.py``
- the recoverable-job flow -> ``test_jobs_recoverable_drop_recovery.py``
- the WS-M3 subject/gate lifecycle -> ``test_m3_walkthrough_live.py``

This file adds the M2 *scenario* surfaces those do not exercise as typed live
SDK calls: ``cluster.diagnose``, the typed ``cluster.capabilities`` M2-field
contract, ``gates.discover``, and ``cluster.operations``. The dataset auto-grant
(ENG-4506 ReBAC bridge) arm of cmd_m2 needs ReBAC tuple inspection
(kubectl/psql) and stays operator-only in the smoke script.

skip-not-fail: the ``live_kamiwaza_client`` fixture skips when no deployment is
reachable; each call additionally skips on 403/404/501 so a baseline that does
not expose a given M2 surface skips rather than fails.
"""

from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

# The platform's always-importable execution gate (cmd_gates_discover default).
_ALLOW_ALL_GATE = "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate"


def _call_or_skip(fn, *args):
    """Invoke a live M2 surface, skip-not-fail when the deployment lacks it."""
    try:
        return fn(*args)
    except APIError as exc:
        status = getattr(exc, "status_code", None)
        if status in (403, 404, 501):
            pytest.skip(f"M2 surface not available on this deployment: {status}")
        raise


def test_m2_diagnose(live_kamiwaza_client) -> None:
    """cmd_diagnose: cluster.diagnose() returns a structured diagnostics run."""
    result = _call_or_skip(live_kamiwaza_client.cluster.diagnose)
    assert result.cluster_id, "diagnose should report a cluster_id"
    assert isinstance(result.issues, list)


def test_m2_capabilities_demo_bullet_fields(live_kamiwaza_client) -> None:
    """cmd_capabilities: the M2 demo-bullet fields are present and typed."""
    caps = _call_or_skip(live_kamiwaza_client.cluster.capabilities)
    assert isinstance(caps.federation_count, int)
    assert isinstance(caps.active_deployments, int)
    assert isinstance(caps.ray_ready, bool)


def test_m2_gates_discover(live_kamiwaza_client) -> None:
    """cmd_gates_discover: gates.discover instantiates the platform gate."""
    discovery = _call_or_skip(live_kamiwaza_client.gates.discover, _ALLOW_ALL_GATE)
    assert discovery.kind in ("execution", "attribute")
    assert discovery.name, "discovered gate should report a name"


def test_m2_operations_unified_shape(live_kamiwaza_client) -> None:
    """cmd_operations: operations() returns the unified jobs + retrievals view."""
    ops = _call_or_skip(live_kamiwaza_client.cluster.operations)
    assert isinstance(ops.jobs, list)
    assert isinstance(ops.retrievals, list)
