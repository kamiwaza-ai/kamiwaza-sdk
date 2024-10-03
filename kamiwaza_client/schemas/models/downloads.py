# kamiwaza_client/schemas/models/downloads.py

from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from uuid import UUID

class ModelDownloadRequest(BaseModel):
    model: str
    version: Optional[str] = None
    hub: Optional[str] = None
    files_to_download: Optional[List[str]] = None

class ModelFileDownloadRequest(BaseModel):
    model: str
    file_name: str
    version: Optional[str] = None
    hub: Optional[str] = None

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