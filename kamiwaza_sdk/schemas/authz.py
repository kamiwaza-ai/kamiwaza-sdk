"""Pydantic models for ReBAC authorization endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SubjectModel(BaseModel):
    namespace: str = Field(..., description="Subject namespace", examples=["user", "group"])
    id: str = Field(..., description="Subject identifier")


class ObjectModel(BaseModel):
    namespace: str = Field(..., description="Object namespace", examples=["model", "dataset"])
    id: str = Field(..., description="Object identifier")


class RelationshipTuple(BaseModel):
    subject: SubjectModel
    relation: str = Field(..., description="Relation identifier")
    object: ObjectModel
    tenant_id: Optional[str] = Field(
        default=None,
        description="Optional tenant identifier override",
    )


class RelationshipTupleDelete(BaseModel):
    subject: SubjectModel
    relation: Optional[str] = Field(
        default=None,
        description="Optional relation filter when deleting a tuple",
    )
    object: ObjectModel
    tenant_id: Optional[str] = Field(
        default=None,
        description="Optional tenant override when deleting a tuple",
    )


class RelationshipObjectDelete(BaseModel):
    object: ObjectModel
    tenant_id: Optional[str] = Field(
        default=None,
        description="Optional tenant override when clearing object tuples",
    )


class CheckRequest(BaseModel):
    subject: SubjectModel
    relation: str = Field(..., description="Relation to evaluate")
    object: ObjectModel


class CheckResponse(BaseModel):
    allow: bool
    decision_id: str
    reason: str
