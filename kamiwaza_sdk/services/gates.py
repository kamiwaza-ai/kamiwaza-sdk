"""T7.10 / ENG-5044 — Gate discovery on the canonical surface.

WS-M3.2 service migration. Brings the gate-discovery surface from
``kamiwaza/gates.py`` (T5.4 / ENG-4691) into ``kamiwaza_sdk.services.gates``
per design v0.3.7 §4.2.11.

Customer-facing API (M2 scope):

    kz.gates.discover(classpath)   -> GateDiscovery

Full gates surface (set_gate / clear_gate / packages.*) ships in WS-M5.
Server-side correlate: POST /api/authz/gates/discover (§4.2.3).
"""

from __future__ import annotations

from ..schemas.federation import GateDiscovery
from .base_service import BaseService


class GatesAPI(BaseService):
    """Gate discovery on the local cluster."""

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
        response = self.client._request(
            "POST",
            "/authz/gates/discover",
            json={"classpath": classpath},
        )
        return GateDiscovery.model_validate(response)
