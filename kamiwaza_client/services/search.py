# kamiwaza_client/services/search.py

from typing import List, Dict, Any, Optional, Union
from .base_service import BaseService
import logging


class SearchService(BaseService):
    """Client service for the Kamiwaza search API."""
    
    def __init__(self, client):
        """Initialize the search service with a client."""
        super().__init__(client)
        self.logger = logging.getLogger(__name__)
    
    def search_documents(
        self, 
        query: str, 
        collection_name: str, 
        limit: int = 5,
        retrieve_content: bool = True,
        filter_criteria: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Search for documents based on semantic similarity to the query.
        
        Args:
            query: The text query to search for
            collection_name: Vector database collection to search in
            limit: Maximum number of results to return
            retrieve_content: Whether to retrieve content immediately
            filter_criteria: Optional filters for metadata fields
            output_fields: Metadata fields to include in results
            
        Returns:
            Dictionary containing search results with matching chunks and metadata
        """
        self.logger.debug(f"Searching for '{query}' in collection '{collection_name}'")
        
        payload = {
            "query": query,
            "collection_name": collection_name,
            "limit": limit,
            "retrieve_content": retrieve_content
        }
        
        if filter_criteria is not None:
            payload["filter_criteria"] = filter_criteria
            
        if output_fields is not None:
            payload["output_fields"] = output_fields
        
        return self.client.post("/search/search", json=payload)
    
    def retrieve_chunk_content(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Retrieve actual content for search results.
        
        Args:
            chunks: List of chunk dictionaries with metadata from search results
            
        Returns:
            List of chunks with content populated
        """
        self.logger.debug(f"Retrieving content for {len(chunks)} chunks")
        
        payload = {
            "chunks": chunks,
            "include_content": True
        }
        
        return self.client.post("/search/retrieve", json=payload) 