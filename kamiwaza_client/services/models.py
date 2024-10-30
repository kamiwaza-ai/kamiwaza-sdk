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
    def get_model(self, model_id: UUID) -> Model:
        """Retrieve a specific model by its ID."""
        response = self.client._request("GET", f"/models/{model_id}")
        return Model.model_validate(response)

    def create_model(self, model: CreateModel) -> Model:
        """Create a new model."""
        response = self.client._request("POST", "/models/", json=model.model_dump())
        return Model.model_validate(response)

    def delete_model(self, model_id: UUID) -> dict:
        """Delete a specific model by its ID."""
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

    def initiate_model_download(self, repo_id: str, quantization: str = 'q6_k') -> Dict[str, Any]:
        """
        Initiate the download of a model based on the repo ID and desired quantization.

        Args:
            repo_id (str): The repo ID of the model to download.
            quantization (str): The desired quantization level. Defaults to 'q6_k'.

        Returns:
            Dict[str, Any]: A dictionary containing information about the initiated download.
        """
        # TODO: Replace this priority scheme with user-configurable options in the future
        priority_order = ['q6_k', 'q5_k_m', 'q4_k_m', 'q8_0']

        # Search for the model
        models = self.search_models(repo_id)
        if not models:
            raise ValueError(f"No model found with repo ID: {repo_id}")
        
        model = next((m for m in models if m.repo_modelId == repo_id), None)
        if not model:
            raise ValueError(f"Exact match for repo ID {repo_id} not found in search results")

        # Fetch model files
        files = self.search_hub_model_files(HubModelFileSearch(hub=model.hub, model=model.repo_modelId))
        
        # Filter files based on quantization and priority
        compatible_files = [
            file for file in files 
            if quantization in file.name.lower() and file.name.lower().endswith('.gguf')
        ]
        
        if not compatible_files:
            for priority in priority_order:
                compatible_files = [
                    file for file in files 
                    if priority in file.name.lower() and file.name.lower().endswith('.gguf')
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

    def get_model_memory_usage(self, model_id: UUID) -> int:
        """Get the memory usage of a model."""
        return self.client._request("GET", f"/models/{model_id}/memory_usage")

    # Model File operations
    def delete_model_file(self, model_file_id: UUID) -> dict:
        """Delete a model file by its ID."""
        return self.client._request("DELETE", f"/model_files/{model_file_id}")

    def get_model_file(self, model_file_id: UUID) -> ModelFile:
        """Retrieve a specific model file by its ID."""
        response = self.client._request("GET", f"/model_files/{model_file_id}")
        return ModelFile.model_validate(response)
    
    def get_model_files_by_model_id(self, model_id: UUID) -> List[ModelFile]:
        """Retrieve all model files by their model ID."""
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

    def search_hub_model_files(self, search_request: HubModelFileSearch) -> List[ModelFile]:
        """Search for model files in a specific hub."""
        response = self.client._request("POST", "/model_files/search/", json=search_request.model_dump())
        return [ModelFile.model_validate(item) for item in response]

    def get_model_file_memory_usage(self, model_file_id: UUID) -> int:
        """Get the memory usage of a model file."""
        return self.client._request("GET", f"/model_files/{model_file_id}/memory_usage")

    # Model Configuration operations
    def create_model_config(self, config: CreateModelConfig) -> ModelConfig:
        """Create a new model configuration."""
        response = self.client._request("POST", "/model_configs/", json=config.model_dump())
        return ModelConfig.model_validate(response)

    def get_model_configs(self, model_id: UUID) -> List[ModelConfig]:
        """Get a list of model configurations for a given model ID."""
        response = self.client._request("GET", "/model_configs/", params={"model_id": str(model_id)})
        return [ModelConfig.model_validate(item) for item in response]

    def get_model_configs_for_model(self, model_id: UUID, default: bool = False) -> List[ModelConfig]:
        """Get a list of model configurations for a given model ID."""
        response = self.client._request("GET", f"/models/{model_id}/configs", params={"default": default})
        return [ModelConfig.model_validate(item) for item in response]

    def get_model_config(self, model_config_id: UUID) -> ModelConfig:
        """Get a model configuration by its ID."""
        response = self.client._request("GET", f"/model_configs/{model_config_id}")
        return ModelConfig.model_validate(response)

    def delete_model_config(self, model_config_id: UUID) -> None:
        """Delete a model configuration by its ID."""
        self.client._request("DELETE", f"/model_configs/{model_config_id}")

    def update_model_config(self, model_config_id: UUID, config: CreateModelConfig) -> ModelConfig:
        """Update a model configuration by its ID."""
        response = self.client._request("PUT", f"/model_configs/{model_config_id}", json=config.model_dump())
        return ModelConfig.model_validate(response)
















    ### This stuff could be moved to a helper class

    def filter_compatible_models(self, model_name: str, version: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Search for models matching the given name and filter those compatible with the current operating system.

        Args:
            model_name (str): The name of the model to search for.
            version (Optional[str]): Specific version of the model, if needed.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries containing compatible models and their files.
        """
        compatible_models = []
        models = self.search_models(model_name)

        for model in models:
            files = self.search_hub_model_files(HubModelFileSearch(hub=model.hub, model=model.repo_modelId, version=version))
            compatible_files = self._filter_files_for_os(files)
            if compatible_files:
                compatible_models.append({"model": model, "files": compatible_files})

        if not compatible_models:
            raise ValueError(f"No compatible models found for '{model_name}' on this operating system")

        return compatible_models

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
