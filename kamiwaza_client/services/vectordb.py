# kamiwaza_client/services/vectordb.py

from typing import List, Optional
from ..schemas.vectordb import CreateVectorDB, VectorDB, InsertVectorsRequest, InsertVectorsResponse, SearchVectorsRequest, SearchResult
from .base_service import BaseService
import logging



class VectorDBService(BaseService):
    
    def __init__(self, client):
        super().__init__(client)
        self.logger = logging.getLogger(__name__)
    
    def create_vectordb(self, vectordb_data: CreateVectorDB) -> VectorDB:
        """Create a new VectorDB instance."""
        response = self.client.post("/vectordb/", json=vectordb_data.model_dump())
        return VectorDB.model_validate(response)

    def get_vectordbs(self, engine: Optional[str] = None) -> List[VectorDB]:
        """Retrieve all VectorDB instances, optionally filtered by engine."""
        params = {"engine": engine} if engine else None
        response = self.client.get("/vectordb/", params=params)
        return [VectorDB.model_validate(item) for item in response]

    def get_vectordb(self, vectordb_id: str) -> VectorDB:
        """Retrieve a specific VectorDB instance by its ID."""
        response = self.client.get(f"/vectordb/{vectordb_id}")
        return VectorDB.model_validate(response)

    def remove_vectordb(self, vectordb_id: str) -> dict:
        """Remove a specific VectorDB instance."""
        return self.client.delete(f"/vectordb/{vectordb_id}")
    
    def insert_vectors(self, insert_request: InsertVectorsRequest) -> InsertVectorsResponse:
        """Insert embeddings into the vector database."""
        self.logger.debug(f"Sending insert request to vectordb service")
        
        # Ensure embeddings are lists of native Python floats
        request_dict = insert_request.model_dump()
        if 'embeddings' in request_dict:
            request_dict['embeddings'] = [
                [float(x) for x in embedding] 
                for embedding in request_dict['embeddings']
            ]
            
        response = self.client.post("/vectordb/insert_vectors", json=request_dict)
        self.logger.debug("Insert request completed successfully")
        return InsertVectorsResponse.model_validate(response)

    def search_vectors(self, search_request: SearchVectorsRequest) -> List[SearchResult]:
        """Search for similar embeddings in the vector database."""
        request_dict = search_request.model_dump()
        if 'query_embedding' in request_dict:
            request_dict['query_embedding'] = [float(x) for x in request_dict['query_embedding']]
            
        response = self.client.post("/vectordb/search_vectors", json=request_dict)
        return [SearchResult.model_validate(item) for item in response]
