# kamiwaza_client/services/models.py

from typing import List, Optional, Union, Dict, Any
import time
from uuid import UUID
import platform
from ..exceptions import APIError
from ..schemas.models.model import Model, CreateModel, ModelConfig, CreateModelConfig
from ..schemas.models.model_file import ModelFile, CreateModelFile
from ..schemas.models.model_search import ModelSearchRequest, ModelSearchResponse, HubModelFileSearch
from ..schemas.models.downloads import ModelDownloadRequest, ModelDownloadStatus
from .base_service import BaseService
import difflib

class ModelService(BaseService):
    def __init__(self, client):
        super().__init__(client)
        self._server_info = None  # Cache server info
        # Define known quantization variants
        self._quant_variants = {
            'q6_k': ['q6_k', 'q6_k_l', 'q6_k_m', 'q6_k_s'],
            'q5_k_m': ['q5_k_m', 'q5_k_l', 'q5_k_s'],
            'q4_k_m': ['q4_k_m', 'q4_k_l', 'q4_k_s'],
            'q8_0': ['q8_0']
        }
        # Priority order for fallback
        self._priority_order = ['q6_k', 'q5_k_m', 'q4_k_m', 'q8_0']

    def get_model(self, model_id: Union[str, UUID]) -> Model:
        """Retrieve a specific model by its ID."""
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        response = self.client._request("GET", f"/models/{model_id}")
        return Model.model_validate(response)

    def create_model(self, model: CreateModel) -> Model:
        """Create a new model."""
        response = self.client._request("POST", "/models/", json=model.model_dump())
        return Model.model_validate(response)

    def delete_model(self, model_id: Union[str, UUID]) -> dict:
        """Delete a specific model by its ID."""
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        return self.client._request("DELETE", f"/models/{model_id}")

    def list_models(self, load_files: bool = False) -> List[Model]:
        """List all models, optionally including associated files."""
        response = self.client._request("GET", "/models/", params={"load_files": load_files})
        return [Model.model_validate(item) for item in response]

    def search_models(self, query: str, exact: bool = False, limit: int = 100, hubs_to_search: Optional[List[str]] = None) -> List[Model]:
        """
        Search for models based on a query string.

        Args:
            query (str): The search query.
            exact (bool, optional): Whether to perform an exact match. Defaults to False.
            limit (int, optional): Maximum number of results to return. Defaults to 100.
            hubs_to_search (List[str], optional): List of hubs to search in. Defaults to None (search all hubs).

        Returns:
            List[Model]: A list of matching models.
        """
        search_request = ModelSearchRequest(
            query=query,
            exact=exact,
            limit=limit,
            hubs_to_search=hubs_to_search or ["*"]
        )
        response = self.client._request("POST", "/models/search/", json=search_request.model_dump())
        search_response = ModelSearchResponse.model_validate(response)
        return [result.model for result in search_response.results]

    def _get_exact_quant_match(self, filename: str, quantization: str) -> bool:
        """
        Check if a filename matches exactly a quantization pattern.
        
        Args:
            filename (str): The filename to check
            quantization (str): The quantization pattern to match
            
        Returns:
            bool: True if exact match found, False otherwise
        """
        # Convert both filename and quantization to lowercase for case-insensitive comparison
        filename_lower = filename.lower()
        quantization_lower = quantization.lower()
        
        # If the quantization includes a specific variant (like q6_k_l), match exactly
        if '_' in quantization_lower and any(q for q in sum(self._quant_variants.values(), []) if q == quantization_lower):
            return f"-{quantization_lower}" in filename_lower
            
        # If it's a base quantization (like q6_k), only match the exact base version
        base_pattern = f"-{quantization_lower}"
        
        # Check if it's a base match (exact) or part of a multi-file pattern
        return (base_pattern + ".gguf" in filename_lower or 
                base_pattern + "-" in filename_lower)  # For multi-file patterns like -00001-of-00002

    def initiate_model_download(self, repo_id: str, quantization: str = 'q6_k') -> Dict[str, Any]:
        """
        Initiate the download of a model based on the repo ID and desired quantization.

        Args:
            repo_id (str): The repo ID of the model to download.
            quantization (str): The desired quantization level. Defaults to 'q6_k'.
                              Can include variant (e.g., 'q6_k_l' for large version).

        Returns:
            Dict[str, Any]: A dictionary containing information about the initiated download.
        """
        # Search for the model
        models = self.search_models(repo_id)
        if not models:
            raise ValueError(f"No model found with repo ID: {repo_id}")
        
        model = next((m for m in models if m.repo_modelId == repo_id), None)
        if not model:
            raise ValueError(f"Exact match for repo ID {repo_id} not found in search results")

        # Fetch model files
        files = self.search_hub_model_files(HubModelFileSearch(hub=model.hub, model=model.repo_modelId))
        
        # Try exact match first
        compatible_files = [
            file for file in files 
            if self._get_exact_quant_match(file.name, quantization) and file.name.lower().endswith('.gguf')
        ]
        
        # If no exact match and no specific variant was requested, try variants in size order
        if not compatible_files and '_' not in quantization:
            base_quant = quantization
            if base_quant in self._quant_variants:
                for variant in self._quant_variants[base_quant]:
                    compatible_files = [
                        file for file in files 
                        if self._get_exact_quant_match(file.name, variant) and file.name.lower().endswith('.gguf')
                    ]
                    if compatible_files:
                        break
        
        # If still no match, try fallback quantizations
        if not compatible_files:
            for priority in self._priority_order:
                if priority == quantization:
                    continue
                compatible_files = [
                    file for file in files 
                    if self._get_exact_quant_match(file.name, priority) and file.name.lower().endswith('.gguf')
                ]
                if compatible_files:
                    break
        
        if not compatible_files:
            raise ValueError(f"No compatible files found for repo {repo_id} with quantization {quantization}")

        # Prepare download request
        download_request = ModelDownloadRequest(
            model=model.repo_modelId,
            hub=model.hub,
            files_to_download=[file.name for file in compatible_files]
        )

        # Initiate download
        result = self.client._request("POST", "/models/download/", json=download_request.model_dump())
        
        return {
            "model": model,
            "files": compatible_files,
            "download_request": download_request,
            "result": result
        }

    def check_download_status(self, repo_id: str) -> List[ModelDownloadStatus]:
        """
        Check the download status for a given model.

        Args:
            repo_id (str): The repo ID of the model to check.

        Returns:
            List[ModelDownloadStatus]: A list of download status objects for the model files.
        """
        download_status = self.get_model_files_download_status(repo_id)
        actual_download_status = []
        for status in download_status:
            if status.download:
                actual_download_status.append(status)
            elif status.download_elapsed:
                actual_download_status.append(status)

        return actual_download_status

    def get_model_files_download_status(self, repo_model_id: str) -> List[ModelDownloadStatus]:
        """
        Get the download status of specified model files.

        Args:
            repo_model_id (str): The repo_modelId of the model to check download status for.

        Returns:
            List[ModelDownloadStatus]: A list of ModelDownloadStatus objects for the model files.
        """
        try:
            response = self.client._request("GET", "/model_files/download_status/", params={"model_id": repo_model_id})
            return [ModelDownloadStatus.model_validate(item) for item in response]
        except Exception as e:
            print(f"Exception in get_model_files_download_status: {e}")
            raise


    def get_model_by_repo_id(self, repo_id: str) -> Model:
        """Retrieve a model by its repo_modelId."""
        response = self.client._request("GET", f"/models/repo/{repo_id}")
        return Model.model_validate(response)

    def get_model_memory_usage(self, model_id: Union[str, UUID]) -> int:
        """Get the memory usage of a model."""
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        return self.client._request("GET", f"/models/{model_id}/memory_usage")

    # Model File operations
    def delete_model_file(self, model_file_id: Union[str, UUID]) -> dict:
        """Delete a model file by its ID."""
        try:
            if isinstance(model_file_id, str):
                model_file_id = UUID(model_file_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_file_id}") from e
            
        return self.client._request("DELETE", f"/model_files/{model_file_id}")

    def get_model_file(self, model_file_id: Union[str, UUID]) -> ModelFile:
        """Retrieve a specific model file by its ID."""
        try:
            if isinstance(model_file_id, str):
                model_file_id = UUID(model_file_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_file_id}") from e
            
        response = self.client._request("GET", f"/model_files/{model_file_id}")
        return ModelFile.model_validate(response)
    
    def get_model_files_by_model_id(self, model_id: Union[str, UUID]) -> List[ModelFile]:
        """Retrieve all model files by their model ID."""
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
        """List all model files."""
        response = self.client._request("GET", "/model_files/")
        return [ModelFile.model_validate(item) for item in response]

    def create_model_file(self, model_file: CreateModelFile) -> ModelFile:
        """Create a new model file."""
        response = self.client._request("POST", "/model_files/", json=model_file.model_dump())
        return ModelFile.model_validate(response)

    def search_hub_model_files(self, search_request: Union[dict, HubModelFileSearch]) -> List[ModelFile]:
        """Search for model files in a specific hub.
        
        Args:
            search_request: Either a dictionary containing hub and model information,
                          or a HubModelFileSearch schema object.
        """
        if isinstance(search_request, dict):
            search_request = HubModelFileSearch.model_validate(search_request)
        
        response = self.client._request("POST", "/model_files/search/", json=search_request.model_dump())
        return [ModelFile.model_validate(item) for item in response]

    def get_model_file_memory_usage(self, model_file_id: Union[str, UUID]) -> int:
        """Get the memory usage of a model file."""
        try:
            if isinstance(model_file_id, str):
                model_file_id = UUID(model_file_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_file_id}") from e
            
        return self.client._request("GET", f"/model_files/{model_file_id}/memory_usage")

    # Model Configuration operations
    def create_model_config(self, config: CreateModelConfig) -> ModelConfig:
        """Create a new model configuration."""
        response = self.client._request("POST", "/model_configs/", json=config.model_dump())
        return ModelConfig.model_validate(response)

    def get_model_configs(self, model_id: Union[str, UUID]) -> List[ModelConfig]:
        """Get a list of model configurations for a given model ID."""
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        response = self.client._request("GET", "/model_configs/", params={"model_id": str(model_id)})
        return [ModelConfig.model_validate(item) for item in response]

    def get_model_configs_for_model(self, model_id: Union[str, UUID], default: bool = False) -> List[ModelConfig]:
        """Get a list of model configurations for a given model ID."""
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        response = self.client._request("GET", f"/models/{model_id}/configs", params={"default": default})
        return [ModelConfig.model_validate(item) for item in response]

    def get_model_config(self, model_config_id: Union[str, UUID]) -> ModelConfig:
        """Get a model configuration by its ID."""
        try:
            if isinstance(model_config_id, str):
                model_config_id = UUID(model_config_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_config_id}") from e
            
        response = self.client._request("GET", f"/model_configs/{model_config_id}")
        return ModelConfig.model_validate(response)

    def delete_model_config(self, model_config_id: Union[str, UUID]) -> None:
        """Delete a model configuration by its ID."""
        try:
            if isinstance(model_config_id, str):
                model_config_id = UUID(model_config_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_config_id}") from e
            
        self.client._request("DELETE", f"/model_configs/{model_config_id}")

    def update_model_config(self, model_config_id: Union[str, UUID], config: CreateModelConfig) -> ModelConfig:
        """Update a model configuration by its ID."""
        try:
            if isinstance(model_config_id, str):
                model_config_id = UUID(model_config_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_config_id}") from e
            
        response = self.client._request("PUT", f"/model_configs/{model_config_id}", json=config.model_dump())
        return ModelConfig.model_validate(response)
















    ### This stuff could be moved to a helper class

    def _get_server_os(self) -> str:
        """Get and cache server OS info from cluster hardware"""
        if self._server_info is None:
            try:
                # Get first hardware entry - limit=1 for efficiency
                hardware = self.client.cluster.list_hardware(limit=1)
                if hardware and len(hardware) > 0:
                    self._server_info = {
                        'os': hardware[0].os,
                        'platform': hardware[0].platform,
                        'processors': hardware[0].processors
                    }
                else:
                    raise ValueError("No hardware information available")
            except Exception as e:
                raise APIError(f"Failed to get server info: {str(e)}")
        
        return self._server_info['os']

    def filter_compatible_models(self, model_name: str) -> List[Dict[str, Any]]:
        """Filter models based on server compatibility"""
        server_os = self._get_server_os()
        models = self.search_models(model_name)
        
        # Let server handle compatibility via download endpoint
        # Just organize the model info for the user
        model_info = []
        for model in models:
            files = self.search_hub_model_files(
                HubModelFileSearch(
                    hub=model.hub, 
                    model=model.repo_modelId
                )
            )
            if files:  # If there are any files, include the model
                model_info.append({
                    "model": model,
                    "files": files,
                    "server_platform": self._server_info  # Include server info for reference
                })

        return model_info


    def _filter_files_for_os(self, files: List[ModelFile]) -> List[ModelFile]:
        """
        Filter files that are compatible with the current operating system.

        Args:
            files (List[ModelFile]): List of available model files.

        Returns:
            List[ModelFile]: List of compatible files for the current OS.
        """
        current_os = platform.system()

        if current_os == 'Darwin':  # macOS
            return [file for file in files if file.name.lower().endswith('.gguf')]
        elif current_os == 'Linux':
            return [file for file in files if not file.name.lower().endswith('.gguf')]
        else:
            raise ValueError(f"Unsupported operating system: {current_os}")
