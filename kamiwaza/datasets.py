"""T5.6 / ENG-4742 — kamiwaza.datasets module.

Customer-facing surface for catalog datasets + attribute-gate binding
per design §4.2.5 / §4.2.11:

    kz.datasets.create(name, platform, **kwargs) -> DatasetRef
    kz.datasets.get(urn)                          -> DatasetRef
    kz.datasets.delete(urn)                       -> None
    kz.datasets.set_gate(urn, type, config={})    -> AttributeGateBinding
    kz.datasets.get_gate(urn)                     -> AttributeGateBinding
    kz.datasets.clear_gate(urn)                   -> None

Scope: the M3-shaped slice setup.py reaches for. The legacy
``kamiwaza_sdk`` namespace ships the broader catalog surface (schema
mutations, container relationships, secrets, …); this module is the
minimal M3 path to bind attribute gates from setup.py without bouncing
between SDK namespaces.

Server-side correlates:
    POST   /api/catalog/datasets/                       (create)
    GET    /api/catalog/datasets/by-urn?urn=...         (get)
    DELETE /api/catalog/datasets/by-urn?urn=...         (delete)
    PUT    /api/catalog/datasets/{urn}/gate             (set_gate / T2.5)
    GET    /api/catalog/datasets/{urn}/gate             (get_gate)
    DELETE /api/catalog/datasets/{urn}/gate             (clear_gate)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from kamiwaza.models import AttributeGateBinding, DatasetRef


class DatasetsAPI:
    """Catalog datasets + attribute-gate binding on the local cluster."""

    def __init__(self, client: Any) -> None:
        # ``Any`` avoids a runtime cycle (client lazy-imports this module).
        self._client = client

    # ─── create / get / delete ────────────────────────────────────────

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
        """Create a dataset in the catalog.

        Args:
            name: Dataset name (forms part of the URN).
            platform: Storage platform (``file``, ``s3``, ``postgres``, ...).
            environment: PROD / DEV / TEST. Defaults server-side to PROD.
            properties: Free-form catalog properties — typically the
                ``path``, connection-specific fields, and (post-M3) the
                ``gate`` nested binding (which T2.5's set_gate writes
                via the dedicated endpoint instead).
            tags: Optional dataset tags.
            description: Optional description.

        Returns:
            DatasetRef — the minimal shape (URN + identifying fields).
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
        response = self._client._request("POST", "/api/catalog/datasets/", json=body)
        return DatasetRef.model_validate(response)

    def get(self, urn: str) -> DatasetRef:
        """Read a dataset by URN.

        Raises:
            KamiwazaError: 404 when the URN is unknown.
        """
        response = self._client._request(
            "GET", "/api/catalog/datasets/by-urn", params={"urn": urn}
        )
        return DatasetRef.model_validate(response)

    def delete(self, urn: str) -> None:
        """Delete the dataset by URN."""
        self._client._request(
            "DELETE", "/api/catalog/datasets/by-urn", params={"urn": urn}
        )

    # ─── gate binding (M3-specific surface) ──────────────────────────

    def set_gate(
        self,
        urn: str,
        *,
        type: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> AttributeGateBinding:
        """Bind an AttributeGate to a dataset (PUT ``/api/catalog/datasets/{urn}/gate``).

        Server validates ``type`` is an AttributeGate subclass and
        validates ``config`` against the gate's ``config_schema()``.

        Args:
            urn: Dataset URN.
            type: AttributeGate classpath, e.g.
                ``"my_gate.ClassificationGate"``.
            config: Per-gate config dict. Defaults to ``{}`` for gates
                with no configurable surface.

        Returns:
            AttributeGateBinding — the persisted shape.

        Raises:
            KamiwazaError: 400 wrong_kind when ``type`` is an
                ExecutionGate; 400 schema_validation_failed when
                config violates the gate's schema; 404 dataset_not_found
                when the URN is unknown.
        """
        body = {"type": type, "config": dict(config) if config else {}}
        response = self._client._request(
            "PUT", f"/api/catalog/datasets/{urn}/gate", json=body
        )
        return AttributeGateBinding.model_validate(response)

    def get_gate(self, urn: str) -> AttributeGateBinding:
        """Read the AttributeGate binding for a dataset.

        Raises:
            KamiwazaError: 404 not_configured when no gate is bound;
                404 dataset_not_found when the URN itself is unknown.
        """
        response = self._client._request("GET", f"/api/catalog/datasets/{urn}/gate")
        return AttributeGateBinding.model_validate(response)

    def clear_gate(self, urn: str) -> None:
        """Remove the AttributeGate binding for a dataset."""
        self._client._request("DELETE", f"/api/catalog/datasets/{urn}/gate")
