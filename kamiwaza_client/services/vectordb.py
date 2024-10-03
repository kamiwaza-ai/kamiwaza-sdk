# kamiwaza_client/services/vectordb.py

from typing import Dict, List, Optional
from uuid import UUID
from .base_service import BaseService

class VectorDBService(BaseService):
    
    def create_vectordb(self, vectordb_data: Dict) -> Dict:
        """Create a new VectorDB instance."""
        return self.client.post("/vectordb/vectordb/", json=vectordb_data)

    def get_vectordbs(self, engine: Optional[str] = None) -> List[Dict]:
        """Retrieve all VectorDB instances, optionally filtered by engine."""
        params = {"engine": engine} if engine else None
        return self.client.get("/vectordb/vectordb/", params=params)

    def get_vectordb(self, vectordb_id: str) -> Dict:
        """Retrieve a specific VectorDB instance by its ID."""
        return self.client.get(f"/vectordb/vectordb/{vectordb_id}")

    def remove_vectordb(self, vectordb: str) -> Dict:
        """Remove a specific VectorDB instance."""
        return self.client.delete(f"/vectordb/vectordb/{vectordb}")