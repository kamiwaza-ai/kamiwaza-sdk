# kamiwaza_client/services/vectors.py

from typing import List, Dict, Any
from ..schemas.vectordb import Insert, DropSchema, AddSchema, SearchVector
from .base_service import BaseService

class VectorService(BaseService):
    
    def insert_vector(self, insert_data: Insert) -> Dict[str, Any]:
        """Insert a vector into the specified collection."""
        response = self.client.post("/vectors/insert", json=insert_data.model_dump())
        return response

    def drop_schema(self, collection_name: str) -> Dict[str, Any]:
        """Drop the schema for the specified collection."""
        response = self.client.delete(f"/vectors/schema/{collection_name}")
        return response

    def add_schema(self, add_schema_data: AddSchema | Dict[str, Any]) -> Dict[str, Any]:
        """Add a new schema for a collection."""
        if isinstance(add_schema_data, dict):
            # If it's already a dict, use it directly
            schema_data = add_schema_data
        else:
            # If it's an AddSchema object, convert it to a dict
            schema_data = add_schema_data.model_dump()
        
        response = self.client.post("/vectors/schema", json=schema_data)
        return response

    def search_vector(self, search_vector_data: SearchVector) -> List[Dict[str, Any]]:
        """Search for similar vectors in the specified collection."""
        response = self.client.post("/vectors/search", json=search_vector_data.model_dump())
        return response

