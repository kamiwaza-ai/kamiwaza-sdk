# kamiwaza_sdk/schemas/models/model_family.py

from pydantic import BaseModel
from datetime import datetime
from uuid import UUID

class CreateModelFamily(BaseModel):
    name: str

    def __str__(self):
        return f"CreateModelFamily: {self.name}"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class ModelFamily(CreateModelFamily):
    id: UUID
    created_timestamp: datetime

    def __str__(self):
        return f"ModelFamily: {self.name} (ID: {self.id}, Created: {self.created_timestamp})"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())
