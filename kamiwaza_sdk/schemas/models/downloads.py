# kamiwaza_sdk/schemas/models/downloads.py

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
    is_downloading: bool
    download_percentage: Optional[int] = None
    download_elapsed: Optional[str] = None  # Changed back to string format (MM:SS)
    download_remaining: Optional[str] = None  # Added back
    download_throughput: Optional[str] = None  # Added back
    download: Optional[bool] = None
    dl_requested_at: Optional[datetime] = None
    download_pid: Optional[int] = None
    storage_location: Optional[str] = None  # Added back
    download_node: Optional[str] = None  # Added back
    download_speed: Optional[float] = None  # Additional field for calculated download speed in bytes/sec
    download_eta: Optional[int] = None  # Additional field for calculated time remaining in seconds

    model_config = {
        "from_attributes": True
    }

    def __str__(self):
        parts = [f"ModelDownloadStatus: {self.name}"]
        
        if self.id:
            parts.append(f"ID: {self.id}")
        if self.m_id:
            parts.append(f"Model ID: {self.m_id}")
            
        # Show download status details if downloading
        if self.is_downloading:
            parts.append(f"Is Downloading: {self.is_downloading}")
            
            # Show percentage with nice formatting
            if self.download_percentage is not None:
                progress_str = f"Download Progress: {self.download_percentage}%"
                
                # Add speed if available - prefer provided throughput over calculated speed
                if self.download_throughput:
                    progress_str += f" ({self.download_throughput})"
                elif hasattr(self, 'download_speed') and self.download_speed:
                    speed_str = self._format_speed(self.download_speed)
                    progress_str += f" ({speed_str})"
                    
                # Add remaining time if available - prefer provided value over calculated
                if self.download_remaining:
                    progress_str += f", {self.download_remaining} remaining"
                elif hasattr(self, 'download_eta') and self.download_eta:
                    eta_str = self._format_time(self.download_eta)
                    progress_str += f", {eta_str} remaining"
                    
                parts.append(progress_str)
                
            # Add elapsed time if available
            if self.download_elapsed:
                parts.append(f"Download time: {self.download_elapsed}")
        else:
            # Show completion status
            if self.download_elapsed:
                parts.append("Download complete")
                parts.append(f"Total download time: {self.download_elapsed}")
            elif self.download is False:
                parts.append("Download failed or cancelled")
                
        return "\n".join(parts)

    def _format_speed(self, speed_in_bytes):
        """Format download speed in human-readable format"""
        if speed_in_bytes < 1024:
            return f"{speed_in_bytes:.2f} B/s"
        elif speed_in_bytes < 1024 * 1024:
            return f"{speed_in_bytes/1024:.2f} KB/s"
        else:
            return f"{speed_in_bytes/(1024*1024):.2f} MB/s"
            
    def _format_time(self, seconds):
        """Format time in human-readable format"""
        if seconds < 60:
            return f"{seconds} seconds"
        elif seconds < 3600:
            minutes = seconds // 60
            sec = seconds % 60
            return f"{minutes}:{sec:02d} minutes"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}:{minutes:02d} hours"

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())
