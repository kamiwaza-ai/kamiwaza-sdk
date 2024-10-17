# kamiwaza_client/schemas/models/downloads.py

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID

class ModelDownloadRequest(BaseModel):
    model: str
    version: Optional[str] = None
    hub: Optional[str] = None
    files_to_download: Optional[List[str]] = None

    def __str__(self):
        return f"ModelDownloadRequest: Model: {self.model}, Version: {self.version}, Hub: {self.hub}"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class ModelFileDownloadRequest(BaseModel):
    model: str
    file_name: str
    version: Optional[str] = None
    hub: Optional[str] = None

    def __str__(self):
        return f"ModelFileDownloadRequest: Model: {self.model}, File: {self.file_name}, Version: {self.version}, Hub: {self.hub}"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class ModelDownloadStatus(BaseModel):
    id: UUID
    m_id: UUID
    name: str
    download: bool
    is_downloading: bool
    storage_location: Optional[str] = None
    download_node: Optional[str] = None
    download_percentage: Optional[int] = None
    download_elapsed: Optional[str] = None
    download_remaining: Optional[str] = None
    download_throughput: Optional[str] = None
    dl_requested_at: Optional[datetime] = None
    download_pid: Optional[int] = None

    model_config = {
        "from_attributes": True
    }

    def __str__(self):
        return (
            f"ModelDownloadStatus: {self.name}\n"
            f"ID: {self.id}\n"
            f"Model ID: {self.m_id}\n"
            f"Is Downloading: {self.is_downloading}\n"
            f"Download Progress: {self.download_percentage}%"
        )

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())
