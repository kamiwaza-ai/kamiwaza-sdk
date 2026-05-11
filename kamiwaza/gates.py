"""T5.4 / ENG-4691 — kamiwaza.gates module.

Customer-facing surface for gate discovery per design §4.2.11:

    kz.gates.discover(classpath)   -> GateDiscovery

WS-M2 ships only ``discover()``. Full surface (set_gate, clear_gate,
packages.*) is WS-M3.

Server-side correlate: POST /api/authz/gates/discover (§4.2.3).
"""

from __future__ import annotations

from typing import Any

from kamiwaza.models import GateDiscovery


class GatesAPI:
    """Gate discovery on the local cluster."""

    def __init__(self, client: Any) -> None:
        # client is a kamiwaza.client.Kamiwaza instance — Any avoids a
        # runtime cycle (client lazy-imports this module).
        self._client = client

    def discover(self, classpath: str) -> GateDiscovery:
        """Reflect on a Gate class by classpath; return its metadata.

        Per design §4.2.3: imports the class server-side, classifies it
        as execution or attribute gate, returns name + required_attributes
        + config_schema. The authoring guide's "discover before bind"
        workflow uses this to validate gate configuration before writing
        the binding to runtime config.

        Args:
            classpath: Dotted fully-qualified classpath, e.g.
                ``"my_policy.MyExecutionGate"``.

        Returns:
            GateDiscovery — typed reflection payload.

        Raises:
            KamiwazaError: 404 classpath_unimportable when the module or
                class can't be loaded; 400 not_a_gate when the loaded
                class isn't an AttributeGate / ExecutionGate subclass.
        """
        response = self._client._request(
            "POST",
            "/api/authz/gates/discover",
            json={"classpath": classpath},
        )
        return GateDiscovery.model_validate(response)
