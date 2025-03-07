from typing import List, Optional, Union, Dict, Any
from uuid import UUID
from ...schemas.models.model import Model
from ...schemas.models.model_file import ModelFile, CreateModelFile
from ...schemas.models.model_search import HubModelFileSearch


class ModelFileMixin:
    """Mixin for model file operations."""
    
    def get_model_memory_usage(self, model_id: Union[str, UUID]) -> int:
        """
        Get the memory usage of a model.
        
        Args:
            model_id (Union[str, UUID]): The ID of the model to check memory usage for.
            
        Returns:
            int: The memory usage in bytes.
        """
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        return self.client._request("GET", f"/models/{model_id}/memory_usage")
    
    def delete_model_file(self, model_file_id: Union[str, UUID]) -> dict:
        """
        Delete a model file by its ID.
        
        Args:
            model_file_id (Union[str, UUID]): The ID of the model file to delete.
            
        Returns:
            dict: The response from the API.
        """
        try:
            if isinstance(model_file_id, str):
                model_file_id = UUID(model_file_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_file_id}") from e
            
        return self.client._request("DELETE", f"/model_files/{model_file_id}")
    
    def get_model_file(self, model_file_id: Union[str, UUID]) -> ModelFile:
        """
        Retrieve a model file by its ID.
        
        Args:
            model_file_id (Union[str, UUID]): The ID of the model file to retrieve.
            
        Returns:
            ModelFile: The model file object.
        """
        try:
            if isinstance(model_file_id, str):
                model_file_id = UUID(model_file_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_file_id}") from e
        
        response = self.client._request("GET", f"/model_files/{model_file_id}")
        return ModelFile.model_validate(response)
    
    def get_model_files_by_model_id(self, model_id: Union[str, UUID]) -> List[ModelFile]:
        """
        Retrieve all model files by their model ID.
        
        Args:
            model_id (Union[str, UUID]): The ID of the model to retrieve files for.
            
        Returns:
            List[ModelFile]: A list of model file objects associated with the model.
        """
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        # Get the model which includes the files
        response = self.client._request("GET", f"/models/{model_id}")
        
        # Extract the files from the response
        if "m_files" in response:
            return [ModelFile.model_validate(item) for item in response["m_files"]]
        return []
    
    def list_model_files(self) -> List[ModelFile]:
        """
        List all model files.
        
        Returns:
            List[ModelFile]: A list of all model file objects.
        """
        response = self.client._request("GET", "/model_files/")
        return [ModelFile.model_validate(item) for item in response]
    
    def create_model_file(self, model_file: CreateModelFile) -> ModelFile:
        """
        Create a new model file.
        
        Args:
            model_file (CreateModelFile): The model file object to create.
            
        Returns:
            ModelFile: The created model file object.
        """
        response = self.client._request("POST", "/model_files/", json=model_file.model_dump())
        return ModelFile.model_validate(response)
    
    def search_hub_model_files(self, search_request: Union[dict, HubModelFileSearch]) -> List[ModelFile]:
        """
        Search for model files in a specific hub.
        
        Args:
            search_request (Union[dict, HubModelFileSearch]): Either a dictionary containing hub and model information,
                          or a HubModelFileSearch schema object.
                          
        Returns:
            List[ModelFile]: A list of model file objects matching the search criteria.
        """
        if isinstance(search_request, dict):
            search_request = HubModelFileSearch.model_validate(search_request)
        
        response = self.client._request("POST", "/model_files/search/", json=search_request.model_dump())
        return [ModelFile.model_validate(item) for item in response]
    
    def get_model_file_memory_usage(self, model_file_id: Union[str, UUID]) -> int:
        """
        Get the memory usage of a model file.
        
        Args:
            model_file_id (Union[str, UUID]): The ID of the model file to check memory usage for.
            
        Returns:
            int: The memory usage in bytes.
        """
        try:
            if isinstance(model_file_id, str):
                model_file_id = UUID(model_file_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_file_id}") from e
            
        return self.client._request("GET", f"/model_files/{model_file_id}/memory_usage")
