# kamiwaza_client/services/models.py

from typing import Dict, List, Optional, Union
from uuid import UUID

class ModelService:
    def __init__(self, client):
        self.client = client

    def get_model(self, model_id: UUID) -> Dict:
        """Retrieve a specific model by its ID."""
        return self.client._request("GET", f"/models/{model_id}")

    def create_model(self, model_data: Dict) -> Dict:
        """Create a new model."""
        return self.client._request("POST", "/models/", json=model_data)

    def delete_model(self, model_id: UUID) -> Dict:
        """Delete a specific model by its ID."""
        return self.client._request("DELETE", f"/models/{model_id}")

    def list_models(self, load_files: bool = False) -> List[Dict]:
        """List all models, optionally including associated files."""
        return self.client._request("GET", "/models/", params={"load_files": load_files})

    def search_models(self, query: str, hubs_to_search: Optional[List[str]] = None,
                      exact: bool = False, limit: int = 100) -> Dict:
        """Search for models based on given criteria."""
        data = {
            "query": query,
            "hubs_to_search": hubs_to_search,
            "exact": exact,
            "limit": limit
        }
        return self.client._request("POST", "/models/search/", json=data)

    def download_model(self, model: str, version: Optional[str] = None,
                       hub: Optional[str] = None, files_to_download: Optional[List[str]] = None) -> Dict:
        """Download specified files associated with a model."""
        data = {
            "model": model,
            "version": version,
            "hub": hub,
            "files_to_download": files_to_download
        }
        return self.client._request("POST", "/models/download/", json=data)

    def get_model_memory_usage(self, model_id: UUID) -> int:
        """Get the memory usage of a model."""
        return self.client._request("GET", f"/models/{model_id}/memory_usage")

    # Model File operations
    def delete_model_file(self, model_file_id: UUID) -> Dict:
        """Delete a model file by its ID."""
        return self.client._request("DELETE", f"/model_files/{model_file_id}")

    def get_model_file(self, model_file_id: UUID) -> Dict:
        """Retrieve a specific model file by its ID."""
        return self.client._request("GET", f"/model_files/{model_file_id}")

    def list_model_files(self) -> List[Dict]:
        """List all model files."""
        return self.client._request("GET", "/model_files/")

    def create_model_file(self, model_file_data: Dict) -> Dict:
        """Create a new model file."""
        return self.client._request("POST", "/model_files/", json=model_file_data)

    def search_hub_model_files(self, hub: str, model: str, version: Optional[str] = None) -> List[Dict]:
        """Search for model files in a specific hub."""
        data = {
            "hub": hub,
            "model": model,
            "version": version
        }
        return self.client._request("POST", "/model_files/search/", json=data)

    def get_model_file_memory_usage(self, model_file_id: UUID) -> int:
        """Get the memory usage of a model file."""
        return self.client._request("GET", f"/model_files/{model_file_id}/memory_usage")

    def get_model_files_download_status(self, model_ids: List[UUID]) -> List[Dict]:
        """Get the download status of specified model files."""
        return self.client._request("GET", "/model_files/download_status/", params={"model_ids": model_ids})

    # Model Configuration operations
    def create_model_config(self, config_data: Dict) -> Dict:
        """Create a new model configuration."""
        return self.client._request("POST", "/model_configs/", json=config_data)

    def get_model_configs(self, model_id: UUID) -> List[Dict]:
        """Get a list of model configurations for a given model ID."""
        return self.client._request("GET", "/model_configs/", params={"model_id": str(model_id)})

    def get_model_configs_for_model(self, model_id: UUID, default: bool = False) -> List[Dict]:
        """Get a list of model configurations for a given model ID."""
        return self.client._request("GET", f"/models/{model_id}/configs", params={"default": default})

    def get_model_config(self, model_config_id: UUID) -> Dict:
        """Get a model configuration by its ID."""
        return self.client._request("GET", f"/model_configs/{model_config_id}")

    def delete_model_config(self, model_config_id: UUID) -> None:
        """Delete a model configuration by its ID."""
        return self.client._request("DELETE", f"/model_configs/{model_config_id}")

    def update_model_config(self, model_config_id: UUID, config_data: Dict) -> Dict:
        """Update a model configuration by its ID."""
        return self.client._request("PUT", f"/model_configs/{model_config_id}", json=config_data)
