# kamiwaza_client/services/vectordb.py

from typing import List, Optional
from ..schemas.vectordb import CreateVectorDB, VectorDB
from .base_service import BaseService

class VectorDBService(BaseService):
    
    def create_vectordb(self, vectordb_data: CreateVectorDB) -> VectorDB:
        """Create a new VectorDB instance."""
        response = self.client.post("/vectordb/vectordb/", json=vectordb_data.model_dump())
        return VectorDB.model_validate(response)

    def get_vectordbs(self, engine: Optional[str] = None) -> List[VectorDB]:
        """Retrieve all VectorDB instances, optionally filtered by engine."""
        params = {"engine": engine} if engine else None
        response = self.client.get("/vectordb/vectordb/", params=params)
        return [VectorDB.model_validate(item) for item in response]

    def get_vectordb(self, vectordb_id: str) -> VectorDB:
        """Retrieve a specific VectorDB instance by its ID."""
        response = self.client.get(f"/vectordb/vectordb/{vectordb_id}")
        return VectorDB.model_validate(response)

    def remove_vectordb(self, vectordb: str) -> dict:
        """Remove a specific VectorDB instance."""
        return self.client.delete(f"/vectordb/vectordb/{vectordb}")