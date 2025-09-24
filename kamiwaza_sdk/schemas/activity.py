# kamiwaza_sdk/schemas/activity.py

from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime

class CreateActivity(BaseModel):
    user_id: Optional[str] = Field(default=None, description="The id of the user")
    module: Optional[str] = Field(default=None, description="The module of the activity")
    apicall: Optional[str] = Field(default=None, description="The API/method call of the activity")
    action: Optional[str] = Field(default=None, description="The action of the activity")

class Activity(CreateActivity):
    id: Optional[UUID] = Field(default=None, description="The id of the activity")
    created_at: Optional[datetime] = Field(default=None, description="The creation time of the activity")

    model_config = {
        "from_attributes": True
    }