# kamiwaza_client/schemas/models/model_family.py

from pydantic import BaseModel
from datetime import datetime
from uuid import UUID

class CreateModelFamily(BaseModel):
    name: str

class ModelFamily(CreateModelFamily):
    id: UUID
    created_timestamp: datetime