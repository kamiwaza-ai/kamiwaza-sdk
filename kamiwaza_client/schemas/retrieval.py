# kamiwaza_client/schemas/retrieval.py

from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class RetrieveRelevantChunksRequest(BaseModel):
    collections: List[str]
    query: str
    catalog_urns: Optional[List[str]] = None
    max_results: int = 100
    # Additional parameters as needed

class ChunkData(BaseModel):
    source: str
    offset: int
    data: str

class RetrieveRelevantChunksResponse(BaseModel):
    chunks: List[ChunkData]
