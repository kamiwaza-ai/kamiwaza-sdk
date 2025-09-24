# kamiwaza_sdk/services/vectordb.py

from typing import List, Optional, Dict, Any
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

    def insert(
        self,
        vectors: List[List[float]],
        metadata: List[Dict[str, Any]],
        collection_name: str = "default",
        field_list: Optional[List[tuple[str, str]]] = None
    ) -> InsertVectorsResponse:
        """
        Simplified method to insert vectors and metadata into the vector database.
        """
        dimensions = len(vectors[0]) if vectors else 0
        
        # Define known autofields that Milvus expects
        autofields = {
            'model_name': 'str',
            'source': 'str', 
            'catalog_urn': 'str',
            'offset': 'int',
            'filename': 'str'  # Add filename explicitly as it's required
        }
        
        # Ensure all metadata entries have the required autofields
        if metadata and len(metadata) > 0:
            # Get all unique keys from all metadata entries
            all_keys = set()
            for entry in metadata:
                all_keys.update(entry.keys())
                
            # Add all autofields to the set of keys
            all_keys.update(autofields.keys())
                
            # Ensure each entry has all keys with appropriate defaults
            for entry in metadata:
                for key in all_keys:
                    if key not in entry:
                        if key in autofields:
                            # Use appropriate default based on field type
                            entry[key] = "" if autofields[key] == 'str' else 0
                        else:
                            # Default for unknown fields
                            entry[key] = ""
        
        # If field_list not provided, generate it from the metadata and autofields
        if field_list is None:
            if metadata:
                # Use all keys from the metadata plus required autofields
                field_list = []
                all_field_keys = set(metadata[0].keys()).union(autofields.keys())
                
                for key in all_field_keys:
                    # Use autofield type if it's a known field, otherwise infer
                    if key in autofields:
                        field_list.append((key, autofields[key]))
                    else:
                        field_list.append((key, self._infer_field_type(metadata[0][key])))
            else:
                # If no metadata, just use autofields
                field_list = [(key, ftype) for key, ftype in autofields.items()]
        
        self.logger.debug(f"Using field_list: {field_list}")
        self.logger.debug(f"First metadata entry: {metadata[0] if metadata else 'None'}")
        
        request = InsertVectorsRequest(
            collection_name=collection_name,
            vectors=vectors,
            metadata=metadata,
            dimensions=dimensions,
            field_list=field_list
        )
        
        return self.insert_vectors(request)
    
    def search(
        self,
        query_vector: List[float],
        collection_name: str = "default",
        limit: int = 5,
        metric_type: str = "IP",
        output_fields: Optional[List[str]] = None,
        nprobe: int = 10
    ) -> List[SearchResult]:
        """
        Simplified method to search for similar vectors.
        
        Args:
            query_vector: Vector to search for
            collection_name: Name of collection to search in
            limit: Maximum number of results to return
            metric_type: Distance metric to use ("IP" or "L2")
            output_fields: Metadata fields to return. If None, returns ["source", "offset"]
            nprobe: Number of clusters to search
        """
        # Set default output fields if none specified
        if output_fields is None:
            output_fields = ["source", "offset"]  # Default fields we need
            
        search_params = {
            "metric_type": metric_type,
            "params": {"nprobe": nprobe}
        }
        
        request = SearchVectorsRequest(
            collection_name=collection_name,
            query_vectors=[query_vector],
            search_params=search_params,
            limit=limit,
            output_fields=output_fields  # Pass the specific fields we want
        )
        
        results = self.search_vectors(request)
        return results

    def _infer_field_type(self, value: Any) -> str:
        """Helper method to infer field type from value."""
        if isinstance(value, str):
            return "str"
        elif isinstance(value, int):
            return "int"
        elif isinstance(value, float):
            return "float"
        elif isinstance(value, bool):
            return "bool"
        else:
            return "str"  # Default to string for unknown types

    def list_collections(self) -> List[str]:
        """List all collections in the vector database.
        
        Returns:
            List[str]: List of collection names in the vector database
        """
        response = self.client.get("/vectordb/collections")
        return response

    def drop_collection(self, collection_name: str) -> dict:
        """Drop a collection from the vector database.
        
        Args:
            collection_name: Name of the collection to drop
            
        Returns:
            dict: Response message indicating success or failure
        """
        response = self.client.delete(f"/vectordb/collections/{collection_name}")
        return response
