# kamiwaza_client/schemas/catalog.py

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

class Dataset(BaseModel):
    urn: Optional[str] = None
    id: str
    platform: str
    environment: str
    paths: Optional[List[str]] = None
    name: Optional[str] = None
    actor: Optional[str] = None
    customProperties: Optional[Dict[str, Any]] = None
    removed: Optional[bool] = None
    tags: Optional[List[str]] = None

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