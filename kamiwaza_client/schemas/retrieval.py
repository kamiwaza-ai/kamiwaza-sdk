# kamiwaza_client/schemas/retrieval.py

from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class RetrieveDatasetsRequest(BaseModel):
    """Request model for retrieving datasets."""
    query: str
    platform: Optional[str] = None
    environment: Optional[str] = None
    owners: Optional[List[str]] = None
    container: Optional[str] = None
    location: Optional[str] = None
    pathspec: Optional[Dict[str, Any]] = None
    iterator: Optional[bool] = False
    ray_mode: Optional[str] = 'read_binary_files'
    ray_kwargs: Optional[Dict[str, Any]] = None

class DatasetItem(BaseModel):
    """Individual dataset item."""
    # Fields will vary based on the actual dataset structure
    id: Optional[str] = None
    name: Optional[str] = None
    path: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class RetrieveDatasetsResponse(BaseModel):
    """Response model for dataset retrieval."""
    datasets: List[Dict[str, Any]]
