# kamiwaza_client/schemas/catalog.py

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

class Dataset(BaseModel):
    urn: Optional[str] = Field(None, description="Dataset URN")
    id: str = Field(..., description="Unique identifier for the dataset")
    platform: str = Field(..., description="Platform identifier")
    environment: str = Field(..., description="Environment (e.g., PROD, DEV)")
    paths: Optional[List[str]] = Field(None, description="List of dataset paths")
    name: Optional[str] = Field(None, description="Dataset name")
    actor: Optional[str] = Field(None, description="Actor who created/modified the dataset")
    customProperties: Optional[Dict[str, Any]] = Field(None, description="Custom metadata properties")
    removed: Optional[bool] = Field(None, description="Soft deletion flag")
    tags: Optional[List[str]] = Field(None, description="Dataset tags")

    model_config = {
        "extra": "allow"
    }


class Container(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Lineage(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Tags(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Terms(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Ownership(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Domains(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Deprecation(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Description(BaseModel):
    model_config = {
        "extra": "allow"
    }

class CustomProperties(BaseModel):
    model_config = {
        "extra": "allow"
    }

class MLSystems(BaseModel):
    model_config = {
        "extra": "allow"
    }