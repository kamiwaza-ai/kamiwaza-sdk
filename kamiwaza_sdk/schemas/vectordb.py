# kamiwaza-sdk/schemas/vectordb.py

import uuid
from pydantic import Field, BaseModel
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime
from uuid import UUID


class CreateVectorDB(BaseModel):
    name: str = Field(..., description="The name of the vectordb instance to register")
    engine: str = Field(..., description="The engine of the vectordb instance, eg Milvus")
    description: str = Field(..., description="The description of the vectordb instance")
    host: str = Field(..., description="The host of the vectordb instance")
    port: int = Field(..., description="The port of the vectordb instance")

class VectorDB(CreateVectorDB):
    id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None

class Connect(BaseModel):
    host: str = Field(..., description="The host to connect to")
    port: int = Field(..., description="The port to connect to")
    username: str = Field(None, description="The username for the connection")
    password: str = Field(None, description="The password for the connection")

class InsertVectorsRequest(BaseModel):
    collection_name: str
    vectors: List[List[float]]
    metadata: List[Dict[str, Any]]
    dimensions: int
    field_list: Optional[List[Tuple[str, str]]] = None

class InsertVectorsResponse(BaseModel):
    rows_inserted: int

class SearchVectorsRequest(BaseModel):
    collection_name: str
    query_vectors: List[List[float]]
    anns_field: str = "embedding"
    search_params: Dict[str, Any] = None
    limit: int = 100
    output_fields: Optional[List[str]] = None

class SearchResult(BaseModel):
    id: Any
    score: float
    metadata: Dict[str, Any]

    @classmethod
    def from_milvus_result(cls, hit, output_fields: Optional[List[str]] = None):
        metadata = {}
        if hit.entity:
            # If output_fields is None or contains "*", get all available fields
            if output_fields is None or "*" in output_fields:
                field_names = hit.entity.keys()
            else:
                field_names = output_fields

            for field_name in field_names:
                if field_name != "embedding":  # Skip embedding field
                    value = hit.entity.get(field_name)
                    if value is not None:
                        metadata[field_name] = value
                        
        return cls(
            id=hit.id,
            score=hit.distance,
            metadata=metadata
        )
