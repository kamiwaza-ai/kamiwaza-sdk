"""Client for ReBAC authorization endpoints."""

from __future__ import annotations

from typing import Optional

from .base_service import BaseService
from ..schemas.authz import (
    CheckRequest,
    CheckResponse,
    RelationshipObjectDelete,
    RelationshipTuple,
    RelationshipTupleDelete,
)


class AuthzService(BaseService):
    """Wrapper around the ReBAC authorization API."""

    def upsert_tuple(
        self,
        relationship: RelationshipTuple,
        *,
        tenant_id: Optional[str] = None,
    ) -> None:
        payload = relationship.model_dump(exclude_none=True)
        if tenant_id and "tenant_id" not in payload:
            payload["tenant_id"] = tenant_id
        headers = self._tenant_headers(tenant_id)
        self.client.post("/auth/tuples", json=payload, headers=headers, expect_json=False)

    def delete_tuple(
        self,
        relationship: RelationshipTupleDelete,
        *,
        tenant_id: Optional[str] = None,
    ) -> None:
        payload = relationship.model_dump(exclude_none=True)
        if tenant_id and "tenant_id" not in payload:
            payload["tenant_id"] = tenant_id
        headers = self._tenant_headers(tenant_id)
        self.client.delete("/auth/tuples", json=payload, headers=headers, expect_json=False)

    def delete_object(
        self,
        relationship: RelationshipObjectDelete,
        *,
        tenant_id: Optional[str] = None,
    ) -> None:
        payload = relationship.model_dump(exclude_none=True)
        if tenant_id and "tenant_id" not in payload:
            payload["tenant_id"] = tenant_id
        headers = self._tenant_headers(tenant_id)
        self.client.delete("/auth/tuples/object", json=payload, headers=headers, expect_json=False)

    def check_access(
        self,
        request: CheckRequest,
        *,
        tenant_id: Optional[str] = None,
    ) -> CheckResponse:
        headers = self._tenant_headers(tenant_id)
        response = self.client.post("/auth/check", json=request.model_dump(), headers=headers)
        return CheckResponse.model_validate(response)

    @staticmethod
    def _tenant_headers(tenant_id: Optional[str]) -> Optional[dict[str, str]]:
        if tenant_id:
            return {"X-Tenant-Id": tenant_id}
        return None
