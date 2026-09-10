"""Closed v1 adapters for the neutral document resource contract."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping


_DOCUMENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_MAX_MUTATION_BYTES = 1_048_576


class DocumentCanonicalizer:
    adapter_id = "conformance-document-canonicalizer:v1"

    def canonicalize(self, resource_id: object) -> str:
        if not isinstance(resource_id, str):
            raise ValueError("document resource ID is invalid")
        if _DOCUMENT_ID.fullmatch(resource_id) is None:
            raise ValueError("document resource ID is invalid")
        return f"document:{resource_id}"

    def request_digest(self, request: Mapping[str, object]) -> str:
        try:
            encoded = json.dumps(
                dict(request),
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("document request is not canonical JSON") from exc
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


class DocumentEntitlementAdapter:
    adapter_id = "conformance-document-entitlement:v1"

    def authorize(self, context: Mapping[str, object]) -> bool:
        return context.get("entitled") is True


class DocumentQuotaAdapter:
    adapter_id = "conformance-document-quota:v1"

    def reserve(self, context: Mapping[str, object]) -> Mapping[str, object]:
        action = context.get("action")
        body_bytes = context.get("body_bytes", 0)
        if action not in {"read", "mutate"}:
            raise ValueError("document quota action is invalid")
        if type(body_bytes) is not int or not 0 <= body_bytes <= _MAX_MUTATION_BYTES:
            raise ValueError("document quota input is invalid")
        return {
            "document_operations": 1,
            "mutation_bytes": body_bytes if action == "mutate" else 0,
        }


class DocumentBrokerAdapter:
    adapter_id = "conformance-document-broker:v1"

    async def execute(self, operation: Mapping[str, object]) -> Mapping[str, object]:
        if operation.get("operation_id") != "document.export":
            raise ValueError("document broker operation is unknown")
        if not isinstance(operation.get("resource_id"), str):
            raise ValueError("document broker resource is invalid")
        return {"operation_id": "document.export", "status": "queued"}


class DocumentResultNormalizer:
    adapter_id = "conformance-document-result:v1"
    _safe_fields = ("id", "status", "title", "version")

    def normalize(self, result: object) -> Mapping[str, object]:
        if not isinstance(result, Mapping):
            raise ValueError("document result is invalid")
        return {name: result[name] for name in self._safe_fields if name in result}


__all__ = (
    "DocumentBrokerAdapter",
    "DocumentCanonicalizer",
    "DocumentEntitlementAdapter",
    "DocumentQuotaAdapter",
    "DocumentResultNormalizer",
)
