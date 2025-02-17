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
        # Create progress bar
        bar_width = 30
        percentage = self.download_percentage or 0
        filled = int(bar_width * percentage / 100)
        bar = f"[{'█' * filled}{'▒' * (bar_width - filled)}]"
        
        # Format status line
        status = "📥 Downloading" if self.is_downloading else "✅ Complete" if percentage == 100 else "⏸️ Paused"
        
        # Build the output
        output = [
            f"📦 {self.name}",
            f"{status}  {bar} {percentage}%"
        ]
        
        # Add speed and time info if available
        if self.is_downloading and self.download_throughput:
            speed_line = f"🚀 Speed: {self.download_throughput}"
            if self.download_remaining:
                speed_line += f"  |  ⏱️ ETA: {self.download_remaining}"
            output.append(speed_line)
            
        return "\n".join(output)

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())
