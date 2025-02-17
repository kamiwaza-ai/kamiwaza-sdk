# kamiwaza_client/schemas/models/model.py

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
from .model_file import ModelFile

class CreateModel(BaseModel):
    repo_modelId: Optional[str] = None
    modelfamily: Optional[str] = None
    purpose: Optional[str] = None
    name: str
    version: Optional[str] = None
    author: Optional[str] = None
    source_repository: Optional[str] = None
    sha_repository: Optional[str] = None
    hub: Optional[str] = None
    description: Optional[str] = None
    quantization_details: Optional[str] = None
    private: Optional[bool] = None
    m_files: List[ModelFile] = []
    modelcard: Optional[str] = None

    def __str__(self):
        return (
            f"CreateModel: {self.name}\n"
            f"Repo Model ID: {self.repo_modelId}\n"
            f"Version: {self.version}\n"
            f"Author: {self.author}"
        )

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class Model(CreateModel):
    id: Optional[UUID] = None
    created_timestamp: Optional[datetime] = None
    modified_timestamp: Optional[datetime] = None
    files_being_downloaded: List[ModelFile] = []

    def __str__(self):
        # Calculate the width based on the longest content
        width = max(
            len(self.name or ""),
            len(self.repo_modelId or ""),
            40  # minimum width
        ) + 4  # padding

        return (
            f"┌{'─' * width}┐\n"
            f"│ {self.name or 'Unnamed':<{width-2}} │\n"
            f"├{'─' * width}┤\n"
            f"│ 🔗 {(self.repo_modelId or 'No repo ID'):<{width-4}} │\n"
            f"│ 📚 Version: {(self.version or 'N/A'):<{width-12}} │\n"
            f"│ 📦 Files downloading: {len(self.files_being_downloaded):<{width-21}} │\n"
            f"└{'─' * width}┘"
        )

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class CreateModelConfig(BaseModel):
    m_id: UUID = Field(description="Foreign key to the associated model")
    m_file_id: Optional[UUID] = Field(default=None, description="Foreign key to the associated model file")
    name: Optional[str] = Field(default=None, description="Name of the model configuration")
    default: bool = Field(description="Whether this is the default model configuration for the model")
    description: Optional[str] = Field(default=None, description="Description of the model configuration and purpose")
    config: Dict[str, Any] = Field(default_factory=dict, description="Key-value pairs for model configuration parameters")
    system_config: Dict[str, Any] = Field(default_factory=dict, description="Key-value pairs for system configuration parameters")

    def __str__(self):
        return f"CreateModelConfig: {self.name} (Default: {self.default}, Model ID: {self.m_id})"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class ModelConfig(CreateModelConfig):
    id: UUID = Field(description="Unique identifier for the DBModelConfig entry")
    kamiwaza_version: Optional[str] = Field(default=None, description="Kamiwaza version at creation of configuration")
    created_at: datetime = Field(description="Timestamp of creation")
    modified_at: Optional[datetime] = Field(default=None, description="Timestamp of last modification")

    def __str__(self):
        return (
            f"ModelConfig: {self.name}\n"
            f"ID: {self.id}\n"
            f"Model ID: {self.m_id}\n"
            f"Default: {self.default}\n"
            f"Created: {self.created_at}\n"
            f"Kamiwaza Version: {self.kamiwaza_version}"
        )

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())
