# kamiwaza_client/schemas/models/model_search.py

from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
from .model import Model

class ModelSearchRequest(BaseModel):
    query: str
    hubs_to_search: Optional[List[str]] = None
    exact: bool = False
    limit: int = 100

class ModelSearchResult(BaseModel):
    id: Optional[UUID] = None
    model: Model

class ModelSearchResponse(BaseModel):
    results: List[ModelSearchResult]
    total_results: int

class HubModelFileSearch(BaseModel):
    hub: str
    model: str
    version: Optional[str] = None