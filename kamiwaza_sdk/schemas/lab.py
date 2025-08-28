# kamiwaza_sdk/schemas/lab.py

from pydantic import BaseModel, Field
from typing import Optional, Dict
from uuid import UUID

class CreateLabRequest(BaseModel):
    username: str = Field(description="The username for the lab")
    resources: Optional[Dict[str, str]] = Field(default=None, description="Optional resources for the lab")

class Lab(BaseModel):
    id: UUID = Field(description="The unique identifier of the lab")
    username: str = Field(description="The username associated with the lab")
    container_id: str = Field(description="The container ID of the lab")
    status: str = Field(description="The current status of the lab")

class LabResponse(BaseModel):
    lab: Lab = Field(description="The lab details")

    model_config = {
        "from_attributes": True
    }