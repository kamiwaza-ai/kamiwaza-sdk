# kamiwaza_client/schemas/models/model_file.py

from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional
from datetime import datetime
from uuid import UUID

class StorageType(str, Enum):
    FILE = 'file'
    S3 = 's3'

class CreateModelFile(BaseModel):
    name: str
    size: Optional[int] = None
    storage_type: Optional[StorageType] = None
    storage_location: Optional[str] = None

class ModelFile(CreateModelFile):
    id: Optional[UUID] = Field(default=None, description="Primary key for the model file.")
    hub: Optional[str] = Field(default=None, description="The hub where the model file is located.")
    m_id: Optional[UUID] = Field(default=None, description="The id of the model")
    checksum: Optional[str] = Field(default=None, description="The checksum of the model file for verification.")
    checksum_type: Optional[str] = Field(default=None, description="The type of checksum used to verify the model file")
    created_timestamp: Optional[datetime] = Field(default=None, description="The timestamp when the model file was created in the database.")
    is_downloading: Optional[bool] = Field(default=None, description="Indicates whether the download is or was in progress.")
    download_pid: Optional[int] = Field(default=None, description="The process ID (PID) of the download process.")
    download: Optional[bool] = Field(default=None, description="Indicates whether the file has been flagged by a user for downloading or not.")
    dl_requested_at: Optional[datetime] = Field(default=None, description="The time the download was requested.")
    download_node: Optional[str] = Field(default=None, description="The node where the download is happening.")
    download_percentage: Optional[int] = Field(default=None, description="The percentage of the download that has been completed.")
    download_elapsed: Optional[str] = Field(default=None, description="The time elapsed during the download.")
    download_remaining: Optional[str] = Field(default=None, description="The time remaining during the download.")
    download_throughput: Optional[str] = Field(default=None, description="The download throughput")
