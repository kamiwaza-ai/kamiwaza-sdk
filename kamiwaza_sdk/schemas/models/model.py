# kamiwaza_sdk/schemas/models/model.py

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
    available_quantizations: List[str] = []

    def __str__(self):
        # Create parts list with only non-None values
        parts = [f"Model: {self.name}"]
        if self.repo_modelId:
            parts.append(f"Repo ID: {self.repo_modelId}")
        if self.id:
            parts.append(f"ID: {self.id}")
        if self.version:
            parts.append(f"Version: {self.version}")
        if self.author:
            parts.append(f"Author: {self.author}")
        if self.created_timestamp:
            parts.append(f"Created: {self.created_timestamp}")
        
        # Show file information
        if hasattr(self, 'm_files') and self.m_files:
            parts.append(f"Files: {len(self.m_files)} available")
            
            # Show file sizes by type
            file_types = {}
            for file in self.m_files:
                ext = file.name.split('.')[-1].lower() if file.name and '.' in file.name else 'unknown'
                if ext not in file_types:
                    file_types[ext] = []
                file_types[ext].append(file)
            
            for ext, files in file_types.items():
                parts.append(f"  {ext.upper()} files: {len(files)}")
        
        # Show available quantizations
        if hasattr(self, 'available_quantizations') and self.available_quantizations:
            parts.append("Available quantizations:")
            for quant in sorted(self.available_quantizations):
                parts.append(f"  - {quant}")
        
        # Always show downloading files count
        if hasattr(self, 'files_being_downloaded') and len(self.files_being_downloaded) > 0:
            parts.append(f"Files: {len(self.files_being_downloaded)} downloading")
        else:
            parts.append("Files: 0 downloading")
            
        return "\n".join(parts)

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
