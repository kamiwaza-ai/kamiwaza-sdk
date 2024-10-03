# kamiwaza_client/schemas/catalog.py

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

class Dataset(BaseModel):
    paths: List[str]
    platform: str
    name: str
    id: str
    actor: str
    customProperties: Dict[str, Any]
    removed: bool
    tags: List[str]

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