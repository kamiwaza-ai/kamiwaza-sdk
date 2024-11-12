# kamiwaza-sdk/kamiwaza_client/schemas/vectordb.py

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

class Insert(BaseModel):
    vector: Any = Field(..., description="The vector to insert")
    metadata: List[Tuple] = Field(..., description="The metadata for the vector")
    collection: str = Field(..., description="The collection to insert the vector into")

class DropSchema(BaseModel):
    collection_name: str = Field(..., description="The name of the collection to drop")

class AddSchema(BaseModel):
    collection_name: str = Field(..., description="The name of the collection to add")
    dims: int = Field(..., description="The dimensions of the collection")
    fieldlist: List[Tuple[str, str]] = Field(None, description="The list of fields for the collection")
    index_params: Dict = Field(None, description="The index parameters for the collection")

class SearchVector(BaseModel):
    collection_name: str = Field(..., description="The name of the collection to search in")
    data: List[List[float]] = Field(..., description="The data to search for")
    anns_field: str = Field("embedding", description="The field of the collection to search in")
    param: Dict = Field(None, description="The parameters for the search")
    limit: int = Field(100, description="The maximum number of top records to return")
