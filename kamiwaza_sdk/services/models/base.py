import json
from typing import List, Optional, Union, Dict, Any
from uuid import UUID
from pydantic import BaseModel
from ...schemas.models.model import Model, CreateModel
from ...schemas.models.external_endpoint import (
    AWSBedrockChatEndpoint,
    AWSTranscribeEndpoint,
)
from ...schemas.guide import ModelGuide
from ...utils.quant_manager import QuantizationManager
from ..base_service import BaseService
from .search import ModelSearchMixin
from .downloads import ModelDownloadMixin
from .files import ModelFileMixin
from .configs import ModelConfigMixin
from .compatibility import CompatibilityMixin
from ...model_selector import ModelAutoSelector


class ModelService(BaseService, 
                  ModelSearchMixin,
                  ModelDownloadMixin, 
                  ModelFileMixin, 
                  ModelConfigMixin,
                  CompatibilityMixin):
    """
    Service for managing models in the Kamiwaza platform.
    
    This service provides comprehensive model management functionality, including:
    - Model CRUD operations
    - Model search and discovery
    - Model file management
    - Download management
    - Model configuration
    - Compatibility checks
    """
    
    def __init__(self, client):
        """
        Initialize the ModelService.
        
        Args:
            client: The Kamiwaza client instance.
        """
        super().__init__(client)
        self._server_info = None  # Cache server info
        self.quant_manager = QuantizationManager()
        
    def get_model(self, model_id: Union[str, UUID]) -> Model:
        """
        Retrieve a specific model by its ID.
        
        Args:
            model_id (Union[str, UUID]): The ID of the model to retrieve.
            
        Returns:
            Model: The model object.
            
        Raises:
            ValueError: If the model_id is not a valid UUID.
        """
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        response = self.client._request("GET", f"/models/{model_id}")
        return Model.model_validate(response)

    def create_model(self, model: CreateModel) -> Model:
        """
        Create a new model.
        
        Args:
            model (CreateModel): The model object to create.
            
        Returns:
            Model: The created model object.
        """
        response = self.client._request("POST", "/models/", json=model.model_dump())
        return Model.model_validate(response)

    def register_external_model(
        self,
        *,
        name: str,
        endpoint: Union[AWSBedrockChatEndpoint, AWSTranscribeEndpoint],
        credential: Union[BaseModel, Dict[str, Any], str],
        force_replace_credentials: bool = False,
        description: Optional[str] = None,
        modelfamily: Optional[str] = None,
        config_name: Optional[str] = None,
    ) -> Model:
        """Register an external (vendor-hosted) model via ``POST /models/``.

        Wraps the platform's external-endpoint registration: the ``endpoint``
        blob plus an inline ``credential_secret`` (the plaintext credential,
        serialized to JSON) is submitted under ``default_config.system_config``.
        The platform validates it, stores the credential as an encrypted Catalog
        secret, strips the plaintext, and persists only the resolved URN.

        Args:
            name: Model name shown in the platform.
            endpoint: Protocol-specific endpoint config (Bedrock chat or
                Transcribe). The ``protocol`` discriminator is carried on it.
            credential: Inline credential — a typed credential model (e.g.
                ``AWSBedrockIamCredential``), a dict, or a pre-serialized JSON
                string. Always stored encrypted server-side.
            force_replace_credentials: When the deterministic Catalog secret
                name already exists, rotate the stored value in place instead
                of failing with 409. Makes re-registration idempotent.
            description: Optional model description.
            modelfamily: Optional model family label.
            config_name: Optional name for the default config row.

        Returns:
            Model: The created model (credential normalized to a URN).
        """
        blob: Dict[str, Any] = endpoint.model_dump(exclude_none=True)
        blob["credential_secret"] = self._serialize_credential(credential)

        # The blob lives in ``system_config`` (not ``config``): the platform
        # JSON-stringifies system_config values before persisting them, whereas
        # config values are stored raw and a nested dict fails to adapt at the
        # DB layer. This also matches where the platform normalizes credentials.
        default_config: Dict[str, Any] = {
            "default": True,
            "name": config_name or f"{name} External Endpoint",
            "config": {},
            "system_config": {"external_endpoint": blob},
        }
        model = CreateModel(
            name=name,
            description=description,
            modelfamily=modelfamily,
            default_config=default_config,
        )

        params = (
            {"force_replace_credentials": True} if force_replace_credentials else None
        )
        response = self.client._request(
            "POST",
            "/models/",
            params=params,
            json=model.model_dump(exclude_none=True),
        )
        return Model.model_validate(response)

    @staticmethod
    def _serialize_credential(
        credential: Union[BaseModel, Dict[str, Any], str],
    ) -> str:
        """Normalize a credential into the inline JSON string the platform expects."""
        if isinstance(credential, str):
            return credential
        if isinstance(credential, BaseModel):
            return credential.model_dump_json(exclude_none=True)
        return json.dumps(credential)

    def delete_model(self, model_id: Union[str, UUID]) -> dict:
        """
        Delete a specific model by its ID.
        
        Args:
            model_id (Union[str, UUID]): The ID of the model to delete.
            
        Returns:
            dict: The response from the API.
            
        Raises:
            ValueError: If the model_id is not a valid UUID.
        """
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        return self.client._request("DELETE", f"/models/{model_id}")

    def list_models(self, load_files: bool = False) -> List[Model]:
        """
        List all models, optionally including associated files.

        Args:
            load_files (bool, optional): Whether to include file information. Defaults to False.
            
        Returns:
            List[Model]: A list of model objects.
        """
        response = self.client._request("GET", "/models/", params={"load_files": load_files})
        return [Model.model_validate(item) for item in response]

    def get_model_by_repo_id(self, repo_id: str) -> Optional[Model]:
        """
        Retrieve a model by its repo_modelId by searching through the models list.

        Args:
            repo_id (str): The repository ID of the model to retrieve.

        Returns:
            Model: The model object, or None if not found.
        """
        models = self.list_models()
        for model in models:
            if model.repo_modelId == repo_id:
                return model
        return None

    def auto_selector(self) -> ModelAutoSelector:
        """Return a helper that can recommend models/variants based on guide data."""

        return ModelAutoSelector(self)

    # Guide helpers --------------------------------------------------

    def list_guides(self) -> List[ModelGuide]:
        response = self.client.get("/guide/")
        return [ModelGuide.model_validate(item) for item in response]

    def import_guides(self, *, replace: bool = False) -> dict:
        params = {"replace": replace} if replace else None
        return self.client.post("/guide/import", params=params)

    def refresh_guides(self) -> dict:
        return self.client.post("/guide/refresh")
