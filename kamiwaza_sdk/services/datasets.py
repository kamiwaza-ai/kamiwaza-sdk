"""T7.9 / ENG-5043 — Federation-aware DatasetsAPI on the canonical surface.

WS-M3.2 service migration. Brings the M3-specific catalog datasets +
attribute-gate binding surface from ``kamiwaza/datasets.py`` (T5.6 /
ENG-4742) into ``kamiwaza_sdk.services.datasets``.

Module name disambiguation: the existing ``kamiwaza_sdk.services.catalog``
covers the broader legacy catalog surface (schema mutations, container
relationships, secrets, ...). This module is the **minimal M3-shaped
slice** setup.py reaches for — just dataset create/get/delete plus the
attribute-gate binding endpoints (set_gate / get_gate / clear_gate).

Customer-facing API:

    kz.datasets.create(name, platform, **kwargs) -> DatasetRef
    kz.datasets.get(urn)                          -> DatasetRef
    kz.datasets.delete(urn)                       -> None
    kz.datasets.set_gate(urn, type, config={})    -> AttributeGateBinding
    kz.datasets.get_gate(urn)                     -> AttributeGateBinding
    kz.datasets.clear_gate(urn)                   -> None

Server-side correlates at /api/catalog/datasets/.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..schemas.federation import AttributeGateBinding, DatasetRef
from .base_service import BaseService


class DatasetsAPI(BaseService):
    """Catalog datasets + attribute-gate binding on the local cluster."""

    def create(
        self,
        *,
        name: str,
        platform: str,
        environment: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> DatasetRef:
        """Create a dataset in the catalog."""
        body: Dict[str, Any] = {"name": name, "platform": platform}
        if environment is not None:
            body["environment"] = environment
        if properties is not None:
            body["properties"] = dict(properties)
        if tags is not None:
            body["tags"] = list(tags)
        if description is not None:
            body["description"] = description
        response = self.client._request("POST", "/catalog/datasets/", json=body)
        return DatasetRef.model_validate(response)

    def get(self, urn: str) -> DatasetRef:
        """Read a dataset by URN."""
        response = self.client._request(
            "GET", "/catalog/datasets/by-urn", params={"urn": urn}
        )
        return DatasetRef.model_validate(response)

    def delete(self, urn: str) -> None:
        """Delete the dataset by URN."""
        self.client._request(
            "DELETE", "/catalog/datasets/by-urn", params={"urn": urn}
        )

    # ─── gate binding (M3-specific surface) ──────────────────────────────

    def set_gate(
        self,
        urn: str,
        *,
        type: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> AttributeGateBinding:
        """Bind an AttributeGate to a dataset (PUT /datasets/{urn}/gate)."""
        body = {"type": type, "config": dict(config) if config else {}}
        response = self.client._request(
            "PUT", f"/catalog/datasets/{urn}/gate", json=body
        )
        return AttributeGateBinding.model_validate(response)

    def get_gate(self, urn: str) -> AttributeGateBinding:
        """Read the AttributeGate binding for a dataset."""
        response = self.client._request("GET", f"/catalog/datasets/{urn}/gate")
        return AttributeGateBinding.model_validate(response)

    def clear_gate(self, urn: str) -> None:
        """Remove the AttributeGate binding for a dataset."""
        self.client._request("DELETE", f"/catalog/datasets/{urn}/gate")
