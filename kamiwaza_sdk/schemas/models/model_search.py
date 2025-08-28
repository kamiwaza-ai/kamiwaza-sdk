# kamiwaza_sdk/schemas/models/model_search.py

from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
from .model import Model

class ModelSearchRequest(BaseModel):
    query: str
    hubs_to_search: Optional[List[str]] = None
    exact: bool = False
    limit: int = 100

    def __str__(self):
        return f"ModelSearchRequest: Query: '{self.query}', Exact: {self.exact}, Limit: {self.limit}"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class ModelSearchResult(BaseModel):
    id: Optional[UUID] = None
    model: Model

    def __str__(self):
        return f"ModelSearchResult: ID: {self.id}, Model: {self.model.name}"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class ModelSearchResponse(BaseModel):
    results: List[ModelSearchResult]
    total_results: int

    def __str__(self):
        return f"ModelSearchResponse: Total Results: {self.total_results}, Results: {len(self.results)}"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class HubModelFileSearch(BaseModel):
    hub: str
    model: str
    version: Optional[str] = None

    def __str__(self):
        return f"HubModelFileSearch: Hub: {self.hub}, Model: {self.model}, Version: {self.version}"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())
