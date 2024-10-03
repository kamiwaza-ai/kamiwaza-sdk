# kamiwaza_client/schemas/vectordb.py

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from uuid import UUID

class CreateVectorDB(BaseModel):
    name: str = Field(description="The name of the vectordb instance to register")
    engine: str = Field(description="The engine of the vectordb instance, eg Milvus")
    description: str = Field(description="The description of the vectordb instance")
    host: str = Field(description="The host of the vectordb instance")
    port: int = Field(description="The port of the vectordb instance")

class VectorDB(CreateVectorDB):
    id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None

    model_config = {
        "from_attributes": True
    }