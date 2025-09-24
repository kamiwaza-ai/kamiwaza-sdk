from typing import List, Dict, Any
from ..schemas.models.downloads import ModelDownloadStatus


class DownloadTracker:
    """
    Utility class for tracking download progress.
    Provides methods for calculating progress and handling download status information.
    """
    
    @staticmethod
    def calculate_overall_progress(status_list: List[ModelDownloadStatus]) -> float:
        """
        Calculate the overall progress percentage from a list of download statuses.
        
        Args:
            status_list (List[ModelDownloadStatus]): A list of download status objects.
            
        Returns:
            float: The overall progress percentage (0-100).
        """
        total_percentage = 0
        active_downloads = 0
        completed_downloads = 0
        
        for status in status_list:
            if status.is_downloading:
                active_downloads += 1
                if status.download_percentage is not None:
                    total_percentage += status.download_percentage
            elif status.download_percentage == 100:
                completed_downloads += 1
                
        # If there are active downloads, calculate overall progress
        if active_downloads > 0:
            return total_percentage / active_downloads
        else:
            return 100 if completed_downloads > 0 else 0
    
    @staticmethod
    def get_active_downloads(status_list: List[ModelDownloadStatus]) -> List[ModelDownloadStatus]:
        """
        Filter the list to get only active downloads.
        
        Args:
            status_list (List[ModelDownloadStatus]): A list of download status objects.
            
        Returns:
            List[ModelDownloadStatus]: A filtered list containing only active downloads.
        """
        return [status for status in status_list if status.is_downloading]
    
    @staticmethod
    def get_completed_downloads(status_list: List[ModelDownloadStatus]) -> List[ModelDownloadStatus]:
        """
        Filter the list to get only completed downloads.
        
        Args:
            status_list (List[ModelDownloadStatus]): A list of download status objects.
            
        Returns:
            List[ModelDownloadStatus]: A filtered list containing only completed downloads.
        """
        return [status for status in status_list if status.download_percentage == 100]
    
    @staticmethod
    def get_pending_downloads(status_list: List[ModelDownloadStatus]) -> List[ModelDownloadStatus]:
        """
        Filter the list to get only pending downloads.
        
        Args:
            status_list (List[ModelDownloadStatus]): A list of download status objects.
            
        Returns:
            List[ModelDownloadStatus]: A filtered list containing only pending downloads.
        """
        return [status for status in status_list if not status.is_downloading and status.download_percentage != 100]
    
    @staticmethod
    def is_download_complete(status_list: List[ModelDownloadStatus]) -> bool:
        """
        Check if all downloads in the list are complete.
        
        Args:
            status_list (List[ModelDownloadStatus]): A list of download status objects.
            
        Returns:
            bool: True if all downloads are complete, False otherwise.
        """
        # A download is complete if no status has is_downloading=True
        return all(not status.is_downloading for status in status_list)
