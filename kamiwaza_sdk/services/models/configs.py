from typing import List, Optional, Union, Dict, Any
from uuid import UUID
from ...schemas.models.model import Model, ModelConfig, CreateModelConfig


class ModelConfigMixin:
    """Mixin for model configuration operations."""
    
    def create_model_config(self, config: CreateModelConfig) -> ModelConfig:
        """
        Create a new model configuration.
        
        Args:
            config (CreateModelConfig): The model configuration object to create.
            
        Returns:
            ModelConfig: The created model configuration object.
        """
        response = self.client._request("POST", "/model_configs/", json=config.model_dump(mode="json"))
        return ModelConfig.model_validate(response)
    
    def get_model_configs(self, model_id: Union[str, UUID]) -> List[ModelConfig]:
        """
        Get a list of model configurations for a given model ID.
        
        Args:
            model_id (Union[str, UUID]): The ID of the model to retrieve configurations for.
            
        Returns:
            List[ModelConfig]: A list of model configuration objects associated with the model.
        """
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        response = self.client._request("GET", "/model_configs/", params={"model_id": str(model_id)})
        return [ModelConfig.model_validate(item) for item in response]
    
    def get_model_configs_for_model(self, model_id: Union[str, UUID], default: bool = False) -> List[ModelConfig]:
        """
        Get a list of model configurations for a given model ID, with option to filter for default configurations.
        
        Args:
            model_id (Union[str, UUID]): The ID of the model to retrieve configurations for.
            default (bool, optional): Whether to retrieve only default configurations. Defaults to False.
            
        Returns:
            List[ModelConfig]: A list of model configuration objects associated with the model.
        """
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        response = self.client._request("GET", f"/models/{model_id}/configs", params={"default": default})
        return [ModelConfig.model_validate(item) for item in response]
    
    def get_model_config(self, model_config_id: Union[str, UUID]) -> ModelConfig:
        """
        Get a model configuration by its ID.
        
        Args:
            model_config_id (Union[str, UUID]): The ID of the model configuration to retrieve.
            
        Returns:
            ModelConfig: The model configuration object.
        """
        try:
            if isinstance(model_config_id, str):
                model_config_id = UUID(model_config_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_config_id}") from e
            
        response = self.client._request("GET", f"/model_configs/{model_config_id}")
        return ModelConfig.model_validate(response)
    
    def delete_model_config(self, model_config_id: Union[str, UUID]) -> None:
        """
        Delete a model configuration by its ID.
        
        Args:
            model_config_id (Union[str, UUID]): The ID of the model configuration to delete.
        """
        try:
            if isinstance(model_config_id, str):
                model_config_id = UUID(model_config_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_config_id}") from e
            
        self.client._request("DELETE", f"/model_configs/{model_config_id}")
    
    def update_model_config(self, model_config_id: Union[str, UUID], config: CreateModelConfig) -> ModelConfig:
        """
        Update a model configuration by its ID.
        
        Args:
            model_config_id (Union[str, UUID]): The ID of the model configuration to update.
            config (CreateModelConfig): The updated model configuration object.
            
        Returns:
            ModelConfig: The updated model configuration object.
        """
        try:
            if isinstance(model_config_id, str):
                model_config_id = UUID(model_config_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_config_id}") from e
            
        response = self.client._request("PUT", f"/model_configs/{model_config_id}", json=config.model_dump(mode="json"))
        return ModelConfig.model_validate(response)
