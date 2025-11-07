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
        self.client.post("/auth/tuples", json=payload, expect_json=False)

    def delete_tuple(
        self,
        relationship: RelationshipTupleDelete,
        *,
        tenant_id: Optional[str] = None,
    ) -> None:
        payload = relationship.model_dump(exclude_none=True)
        if tenant_id and "tenant_id" not in payload:
            payload["tenant_id"] = tenant_id
        self.client.delete("/auth/tuples", json=payload, expect_json=False)

    def delete_object(
        self,
        relationship: RelationshipObjectDelete,
        *,
        tenant_id: Optional[str] = None,
    ) -> None:
        payload = relationship.model_dump(exclude_none=True)
        if tenant_id and "tenant_id" not in payload:
            payload["tenant_id"] = tenant_id
        self.client.delete("/auth/tuples/object", json=payload, expect_json=False)

    def check_access(self, request: CheckRequest) -> CheckResponse:
        response = self.client.post("/auth/check", json=request.model_dump())
        return CheckResponse.model_validate(response)
