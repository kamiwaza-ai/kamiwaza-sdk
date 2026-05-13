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

    kz.datasets.create(name, platform, **kwargs) -> str (URN)
    kz.datasets.get(urn)                          -> DatasetRef
    kz.datasets.delete(urn)                       -> None
    kz.datasets.set_gate(urn, type, config={})    -> AttributeGateBinding
    kz.datasets.get_gate(urn)                     -> AttributeGateBinding
    kz.datasets.clear_gate(urn)                   -> None

Server-side correlates at /api/catalog/datasets/.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..schemas.federation import AttributeGateBinding, DatasetRef
from .base_service import BaseService


def _encode_urn(urn: str) -> str:
    """URL-encode a URN for safe inclusion as a path segment.

    DataHub URNs commonly contain ``/`` characters
    (e.g. ``urn:li:dataset:(urn:li:dataPlatform:file,/var/tmp/docs,PROD)``),
    which would split the URN across multiple path segments without
    encoding. Matches the existing pattern at
    ``kamiwaza_sdk.services.catalog._encode_path_segment``.
    """
    return quote(urn, safe="")


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
    ) -> str:
        """Create a dataset in the catalog. Returns the created dataset's URN.

        Server response per OpenAPI: ``201`` with body ``type: string`` —
        a bare URN, not a Dataset object. Mirrors the convention used by
        the legacy ``kamiwaza_sdk.services.catalog.DatasetClient.create``
        (which also returns ``str(response)``). Customers can subsequently
        ``kz.datasets.get(urn)`` to fetch the typed ``DatasetRef`` if
        needed.

        H4 (PR feedback C1): the previous version of this method called
        ``DatasetRef.model_validate(response)`` on the string response,
        which Pydantic would have raised ValidationError for on the
        first real call.
        """
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
        return str(response)

    def get(self, urn: str) -> DatasetRef:
        """Read a dataset by URN."""
        response = self.client._request(
            "GET", "/catalog/datasets/by-urn", params={"urn": urn}
        )
        return DatasetRef.model_validate(response)

    def delete(self, urn: str) -> None:
        """Delete the dataset by URN."""
        self.client._request("DELETE", "/catalog/datasets/by-urn", params={"urn": urn})

    # ─── gate binding (M3-specific surface) ──────────────────────────────

    def set_gate(
        self,
        urn: str,
        *,
        type: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> AttributeGateBinding:
        """Bind an AttributeGate to a dataset (PUT /datasets/{urn}/gate).

        H1 (PR feedback): URN is URL-encoded so DataHub URNs containing
        ``/`` (common in file-platform datasets) don't split into extra
        path segments and mis-route.
        """
        body = {"type": type, "config": dict(config) if config else {}}
        response = self.client._request(
            "PUT", f"/catalog/datasets/{_encode_urn(urn)}/gate", json=body
        )
        return AttributeGateBinding.model_validate(response)

    def get_gate(self, urn: str) -> AttributeGateBinding:
        """Read the AttributeGate binding for a dataset (URN URL-encoded)."""
        response = self.client._request(
            "GET", f"/catalog/datasets/{_encode_urn(urn)}/gate"
        )
        return AttributeGateBinding.model_validate(response)

    def clear_gate(self, urn: str) -> None:
        """Remove the AttributeGate binding for a dataset (URN URL-encoded)."""
        self.client._request("DELETE", f"/catalog/datasets/{_encode_urn(urn)}/gate")
