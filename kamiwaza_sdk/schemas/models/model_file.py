# kamiwaza_sdk/schemas/models/model_file.py
from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional
from datetime import datetime
from uuid import UUID

class StorageType(str, Enum):
    FILE = 'file'
    S3 = 's3'
    SCRATCH = 'scratch'

    def __str__(self):
        return self.value

class CreateModelFile(BaseModel):
    """Object with fields required to create a ModelFile"""
    name: str = Field(..., description="The name of the model file")
    size: Optional[int] = Field(None, description="The size of the model file in bytes")
    storage_type: Optional[StorageType] = Field(None, description="The type of storage where the file is located (file or s3)")
    storage_host: str = Field(default="localhost", description="Host where the file is stored")
    storage_location: Optional[str] = Field(None, description="The location path or key where the file is stored")

    model_config = {
        "from_attributes": True,
        "populate_by_name": True
    }

    def __str__(self):
        return f"CreateModelFile: {self.name} (Size: {self.size}, Type: {self.storage_type})"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class ModelFile(CreateModelFile):
    """A model file, after creation (meaning it includes an RESTful id)"""
    id: Optional[UUID] = Field(None, description="Primary key for the model file.")
    hub: Optional[str] = Field(None, description="The hub where the model file is located. eg, 'HubsHf'")
    m_id: Optional[UUID] = Field(None, alias="model_id", description="The id of the model (model is a reserved word)")
    checksum: Optional[str] = Field(None, description="The checksum of the model file for verification.")
    checksum_type: Optional[str] = Field(None, description="The type of checksum used to verify the model file - for hf, typically sha1 for git-sha or sha256 for kamiwaza/git-lfs")
    created_timestamp: Optional[datetime] = Field(None, description="The timestamp when the model file was created in the database.")
    is_downloading: Optional[bool] = Field(None, description="Indicates whether the download is or was in progress.")
    download_pid: Optional[int] = Field(None, description="The process ID (PID) of the download process.")
    download: Optional[bool] = Field(
        None,
        description="Indicates whether the file has been flagged by a user for downloading or not. "
                   "This accounts for models that have various weight files for different quants, etc."
    )
    dl_requested_at: Optional[datetime] = Field(None, description="The time the download was requested.")
    download_node: Optional[str] = Field(None, description="The node where the download is happening.")
    download_percentage: Optional[int] = Field(None, description="The percentage of the download that has been completed.")
    download_elapsed: Optional[str] = Field(None, description="The time elapsed during the download.")
    download_remaining: Optional[str] = Field(None, description="The time remaining during the download.")
    download_throughput: Optional[str] = Field(None, description="The download throughput (human readable, units vary)")
    storage_host: Optional[str] = Field(
        None,
        description="Host where the file is stored. Uses localhost for community edition, actual hostname for clustered mode"
    )

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,
        "extra": "ignore"  # Ignore extra attributes
    }

    def __str__(self):
        parts = [f"ModelFile: {self.name}"]
        
        if self.id:
            parts.append(f"ID: {self.id}")
        
        # Format size in human-readable format if available
        if self.size:
            size_str = self._format_size(self.size)
            parts.append(f"Size: {size_str}")
            
        if self.storage_type:
            parts.append(f"Storage Type: {self.storage_type}")
            
        # Only show download info if it's downloading
        if self.is_downloading:
            parts.append(f"Is Downloading: {self.is_downloading}")
            if self.download_percentage is not None:
                parts.append(f"Download Progress: {self.download_percentage}%")
                
        return "\n".join(parts)

    def _format_size(self, size_in_bytes):
        """Format size in human-readable format"""
        if size_in_bytes < 1024:
            return f"{size_in_bytes} B"
        elif size_in_bytes < 1024 * 1024:
            return f"{size_in_bytes/1024:.2f} KB"
        elif size_in_bytes < 1024 * 1024 * 1024:
            return f"{size_in_bytes/(1024*1024):.2f} MB"
        else:
            return f"{size_in_bytes/(1024*1024*1024):.2f} GB"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())