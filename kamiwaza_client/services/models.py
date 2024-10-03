# kamiwaza_client/services/models.py

from typing import List, Optional
from uuid import UUID
from ..schemas.models.model import Model, CreateModel, ModelConfig, CreateModelConfig
from ..schemas.models.model_file import ModelFile, CreateModelFile
from ..schemas.models.model_search import ModelSearchRequest, ModelSearchResponse, HubModelFileSearch
from ..schemas.models.downloads import ModelDownloadRequest, ModelDownloadStatus
from .base_service import BaseService

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

    def search_models(self, search_request: ModelSearchRequest) -> ModelSearchResponse:
        """Search for models based on given criteria."""
        response = self.client._request("POST", "/models/search/", json=search_request.model_dump())
        return ModelSearchResponse.model_validate(response)

    def download_model(self, download_request: ModelDownloadRequest) -> dict:
        """Download specified files associated with a model."""
        return self.client._request("POST", "/models/download/", json=download_request.model_dump())

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

    def get_model_files_download_status(self, model_ids: List[UUID]) -> List[ModelDownloadStatus]:
        """Get the download status of specified model files."""
        response = self.client._request("GET", "/model_files/download_status/", params={"model_ids": [str(id) for id in model_ids]})
        return [ModelDownloadStatus.model_validate(item) for item in response]

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