# kamiwaza_client/services/retrieval.py

from typing import List, Dict, Any, Optional, Union
from ..schemas.retrieval import (
    RetrieveDatasetsRequest,
    RetrieveDatasetsResponse
)
from .base_service import BaseService
from ..exceptions import APIError
import logging

class RetrievalService(BaseService):
    """Client service for data retrieval operations."""
    
    def __init__(self, client):
        """Initialize the retrieval service with a client."""
        super().__init__(client)
        self.logger = logging.getLogger(__name__)

    def retrieve_datasets(
        self,
        query: str,
        platform: Optional[str] = None,
        environment: Optional[str] = None,
        owners: Optional[List[str]] = None,
        container: Optional[str] = None,
        location: Optional[str] = None,
        pathspec: Optional[Dict[str, Any]] = None,
        iterator: Optional[bool] = False,
        ray_mode: Optional[str] = 'read_binary_files',
        ray_kwargs: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Retrieve datasets based on query and filters.
        
        Args:
            query: Search query for datasets
            platform: Specific platform to search
            environment: Environment filter
            owners: List of owner usernames to filter by
            container: Container name or URN
            location: Location code
            pathspec: Path specification for filtering
            iterator: Whether to return an iterator
            ray_mode: Mode for Ray data processing
            ray_kwargs: Additional arguments for Ray
            
        Returns:
            Dictionary containing retrieved datasets
        """
        self.logger.debug(f"Retrieving datasets with query: {query}")
        
        request = RetrieveDatasetsRequest(
            query=query,
            platform=platform,
            environment=environment,
            owners=owners,
            container=container,
            location=location,
            pathspec=pathspec,
            iterator=iterator,
            ray_mode=ray_mode,
            ray_kwargs=ray_kwargs
        )
        
        try:
            response = self.client.post(
                "/retrieval/datasets",
                json=request.model_dump()
            )
            return response
        except Exception as e:
            self.logger.error(f"Failed to retrieve datasets: {str(e)}")
            raise APIError(f"Failed to retrieve datasets: {str(e)}")
