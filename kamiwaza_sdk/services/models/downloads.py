from typing import List, Optional, Union, Dict, Any, Set
from uuid import UUID
import time
from datetime import datetime
import sys
from ...schemas.models.model import Model
from ...schemas.models.model_file import ModelFile
from ...schemas.models.model_search import HubModelFileSearch
from ...schemas.models.downloads import ModelDownloadRequest, ModelDownloadStatus
from ...utils.download_tracker import DownloadTracker
from ...utils.progress_formatter import ProgressFormatter


class ModelDownloadMixin:
    """Mixin for model download functionality."""
    
    def initiate_model_download(self, repo_id: str, quantization: str = 'q6_k') -> Dict[str, Any]:
        """
        Initiate the download of a model based on the repo ID.
        
        This method adapts its behavior based on the model repository structure:
        - If multiple quantization variants are available, it will use the specified
          quantization parameter (defaulting to 'q6_k' if not specified)
        - If no quantization variants are detected, it will download all necessary
          model files regardless of the quantization parameter
        - If the requested files are already downloaded, it will skip the download
          and return information about the existing files
        
        Args:
            repo_id (str): The repo ID of the model to download.
            quantization (str, optional): The desired quantization level when multiple
                                         options are available. Defaults to 'q6_k'.
        
        Returns:
            Dict[str, Any]: A dictionary containing information about the initiated download.
        """
        # Search for the model with files included
        models = self.search_models(repo_id, load_files=True)
        if not models:
            raise ValueError(f"No model found with repo ID: {repo_id}")
        
        model = next((m for m in models if m.repo_modelId == repo_id), None)
        if not model:
            raise ValueError(f"Exact match for repo ID {repo_id} not found in search results")

        # Get files from the model
        files = model.m_files if hasattr(model, 'm_files') and model.m_files else []
        
        if not files:
            # If files weren't loaded with the model, fetch them directly
            files = self.search_hub_model_files(HubModelFileSearch(hub=model.hub, model=model.repo_modelId))
            model.m_files = files
        
        # Check if the model has multiple quantization options
        has_multiple_quants = self.quant_manager.has_multiple_quantizations(files)
        
        if has_multiple_quants:
            # Model has multiple quantizations - use the specified one or default
            compatible_files = self.quant_manager.filter_files_by_quantization(files, quantization)
            
            if not compatible_files:
                # If no compatible files found, extract and show available quantizations
                available_quants = set()
                for file in files:
                    if file.name:
                        quant = self.quant_manager.detect_quantization(file.name)
                        if quant:
                            available_quants.add(quant)
                
                error_msg = f"No compatible files found for model {repo_id} with quantization {quantization}"
                if available_quants:
                    error_msg += f"\nAvailable quantizations: {', '.join(sorted(available_quants))}"
                raise ValueError(error_msg)
                
            # Store the filtered files back to the model for future reference
            model.m_files = compatible_files
            
        else:
            # Model doesn't have multiple quantizations - use all files
            compatible_files = files
        
        # Check which files are already downloaded
        already_downloaded_files = []
        files_to_download = []
        
        for file in compatible_files:
            if hasattr(file, 'download') and file.download:
                already_downloaded_files.append(file)
            else:
                files_to_download.append(file.name)
        
        # If all files are already downloaded, return without initiating a new download
        if not files_to_download and already_downloaded_files:
            print(f"All requested files for model {repo_id} are already downloaded.")
            return {
                "model": model,
                "files": already_downloaded_files,
                "download_request": None,
                "result": {
                    "result": True,
                    "message": "Files already downloaded",
                    "files": [file.id for file in already_downloaded_files]
                }
            }
        
        # Send the download request for files that need to be downloaded
        if files_to_download:
            download_request = ModelDownloadRequest(
                model=model.repo_modelId,
                hub=model.hub,
                files_to_download=files_to_download
            )
            result = self.client._request("POST", "/models/download/", json=download_request.model_dump())
        else:
            # This should not happen, but just in case
            download_request = None
            result = {
                "result": True,
                "message": "No files to download",
                "files": []
            }
        
        # Create an enhanced output dictionary with better string representation
        result_dict = {
            "model": model,
            "files": compatible_files,
            "download_request": download_request,
            "result": result
        }
        
        # Add an enhanced string representation for better output
        class EnhancedDeploymentResult(dict):
            def __str__(self):
                output = []
                # Get model info
                model = self.get('model')
                if model:
                    output.append(f"Download initiated for: {model.name}")
                    if hasattr(model, 'repo_modelId') and model.repo_modelId:
                        output.append(f"Repo ID: {model.repo_modelId}")
                    if hasattr(model, 'id') and model.id:
                        output.append(f"Model ID: {model.id}")
                    output.append("")
                
                # Add files section
                files = self.get('files', [])
                already_downloaded = [f for f in files if hasattr(f, 'download') and f.download]
                to_download = [f for f in files if not (hasattr(f, 'download') and f.download)]
                
                if already_downloaded:
                    output.append("Already downloaded files:")
                    for file in already_downloaded:
                        size_str = ""
                        if hasattr(file, 'size') and file.size:
                            size_str = f" ({self._format_size(file.size)})"
                        output.append(f"- {file.name}{size_str}")
                    output.append("")
                
                if to_download:
                    output.append("Files to download:")
                    for file in to_download:
                        size_str = ""
                        if hasattr(file, 'size') and file.size:
                            size_str = f" ({self._format_size(file.size)})"
                        output.append(f"- {file.name}{size_str}")
                    output.append("")
                
                result = self.get('result', {})
                if result:
                    if result.get('result', False):
                        output.append("Download status: Success")
                    else:
                        output.append("Download status: Failed")
                    if 'message' in result:
                        output.append(f"Message: {result['message']}")
                
                return "\n".join(output)
                
            def _format_size(self, size_in_bytes):
                """Format size in human-readable format"""
                if not size_in_bytes:
                    return "unknown size"
                if size_in_bytes < 1024:
                    return f"{size_in_bytes} B"
                elif size_in_bytes < 1024 * 1024:
                    return f"{size_in_bytes/1024:.2f} KB"
                elif size_in_bytes < 1024 * 1024 * 1024:
                    return f"{size_in_bytes/(1024*1024):.2f} MB"
                else:
                    return f"{size_in_bytes/(1024*1024*1024):.2f} GB"
                
        return EnhancedDeploymentResult(result_dict)
    
    def check_download_status(self, repo_id: str) -> List[ModelDownloadStatus]:
        """
        Check the download status for a given model.

        Args:
            repo_id (str): The repo ID of the model to check.

        Returns:
            List[ModelDownloadStatus]: A list of download status objects for the model files.
        """
        try:
            download_status = self.get_model_files_download_status(repo_id)
            actual_download_status = []
            for status in download_status:
                if status.download or status.download_elapsed:
                    actual_download_status.append(status)

            # If we have status items, wrap them in an enhanced list for better display
            if actual_download_status:
                class EnhancedStatusList(list):
                    def __str__(self):
                        if not self:
                            return "No downloads in progress or completed for this model."
                        
                        # Get the model ID if available
                        model_id = self[0].m_id if self[0].m_id else "Unknown"
                        
                        # Create summary header
                        output = [
                            f"Download Status for: {repo_id}",
                            f"Model ID: {model_id}",
                            ""
                        ]
                        
                        # Add files section
                        output.append("Files:")
                        
                        # Track overall progress
                        total_percentage = 0
                        active_downloads = 0
                        completed_downloads = 0
                        
                        # Add each file's status
                        for status in self:
                            file_line = f"- {status.name}: "
                            
                            if status.is_downloading:
                                active_downloads += 1
                                if status.download_percentage is not None:
                                    total_percentage += status.download_percentage
                                    file_line += f"{status.download_percentage}% complete"
                                    
                                    # Add speed if available
                                    if status.download_throughput:
                                        file_line += f" ({status.download_throughput})"
                                    
                                    # Add remaining time if available
                                    if status.download_remaining:
                                        file_line += f", {status.download_remaining} remaining"
                                
                                # Add elapsed time if available
                                if status.download_elapsed:
                                    file_line += f" [elapsed: {status.download_elapsed}]"
                            elif status.download_elapsed:
                                completed_downloads += 1
                                file_line += f"Download complete [took: {status.download_elapsed}]"
                            else:
                                file_line += "Pending"
                                
                            output.append(file_line)
                        
                        # Add overall progress summary if we have active downloads
                        if active_downloads > 0:
                            avg_percentage = total_percentage / active_downloads
                            output.append("")
                            output.append(f"Overall Progress: {avg_percentage:.1f}%")
                            
                        # Add completed summary if we have any completed files
                        if completed_downloads > 0:
                            output.append("")
                            output.append(f"Completed Files: {completed_downloads}/{len(self)}")
                            
                        return "\n".join(output)
                    
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
                            return f"{seconds:.0f} seconds"
                        elif seconds < 3600:
                            minutes = seconds // 60
                            secs = seconds % 60
                            return f"{minutes:.0f}m {secs:.0f}s"
                        else:
                            hours = seconds // 3600
                            minutes = (seconds % 3600) // 60
                            return f"{hours:.0f}h {minutes:.0f}m"
                
                return EnhancedStatusList(actual_download_status)
            
            return actual_download_status
        except Exception as e:
            print(f"Error getting download status: {e}")
            return []
    
    def get_model_files_download_status(self, repo_model_id: str) -> List[ModelDownloadStatus]:
        """
        Get the download status of specified model files.
        
        Note: Need to investigate the difference between this method and check_download_status
        to potentially consolidate functionality.

        Args:
            repo_model_id (str): The repo_modelId of the model to check download status for.

        Returns:
            List[ModelDownloadStatus]: A list of ModelDownloadStatus objects for the model files.
        """
        try:
            response = self.client._request("GET", "/model_files/download_status/", params={"model_id": repo_model_id})
            
            # Create status objects with proper validation
            results = []
            for item in response:
                try:
                    status = ModelDownloadStatus.model_validate(item)
                    results.append(status)
                except Exception as e:
                    print(f"Warning: Failed to parse download status item: {e}")
            
            return results
        except Exception as e:
            print(f"Error getting model files download status: {e}")
            return []
    
    def wait_for_download(
        self,
        repo_id: str, 
        polling_interval: int = 5, 
        timeout: Optional[int] = None, 
        show_progress: bool = True
    ) -> List[ModelDownloadStatus]:
        """
        Wait for model downloads to complete, showing progress.
        
        Args:
            repo_id (str): The repository ID of the model
            polling_interval (int): Seconds between status checks (default: 5)
            timeout (Optional[int]): Maximum seconds to wait (None = wait indefinitely)
            show_progress (bool): Whether to show download progress (default: True)
            
        Returns:
            List[ModelDownloadStatus]: List of final download status objects
            
        Raises:
            TimeoutError: If downloads don't complete within timeout
        """
        # Initialize variables
        start_time = datetime.now()
        last_status_list = []
        
        try:
            while True:
                # Check if we've hit the timeout
                if timeout is not None:
                    elapsed_seconds = (datetime.now() - start_time).total_seconds()
                    if elapsed_seconds > timeout:
                        raise TimeoutError(f"Download timeout after {timeout} seconds")
                
                # Check current status
                status_list = self.check_download_status(repo_id)
                
                # If we have status, update our last known status
                if status_list:
                    last_status_list = status_list
                
                # Calculate elapsed time
                elapsed_seconds = (datetime.now() - start_time).total_seconds()
                
                # Display progress if requested
                if show_progress and status_list:
                    # Use DownloadTracker and ProgressFormatter instead of private methods
                    overall_progress = DownloadTracker.calculate_overall_progress(status_list)
                    self._display_progress(status_list, overall_progress, elapsed_seconds)
                
                # Check if all downloads are complete
                # This means either:
                # 1. No downloads are found (empty status_list), or
                # 2. All downloads in status_list have is_downloading=False
                if not status_list or all(not status.is_downloading for status in status_list):
                    if show_progress:
                        # Get the model to display final information
                        model = self.get_model_by_repo_id(repo_id)
                        if model and hasattr(model, 'm_files') and model.m_files:
                            print("\nDownload complete for:", repo_id)
                            print(f"Total download time: {ProgressFormatter.format_elapsed_time(elapsed_seconds)}")
                            print("Files downloaded:")
                            for file in model.m_files:
                                if hasattr(file, 'download') and file.download:
                                    size_str = f" ({ProgressFormatter.format_size(file.size)})" if hasattr(file, 'size') and file.size else ""
                                    print(f"- {file.name}{size_str}")
                            
                            # Show model ID if available
                            if hasattr(model, 'id') and model.id:
                                print(f"Model ID: {model.id}")
                    
                    # Return the last known status list
                    return last_status_list
                
                # Wait before next status check
                time.sleep(polling_interval)
                
        except KeyboardInterrupt:
            print("\nDownload monitoring interrupted by user")
            return last_status_list
        except Exception as e:
            print(f"\nError monitoring download: {str(e)}")
            return last_status_list
    
    def _display_progress(self, status_list, overall_progress, elapsed_seconds):
        """Display download progress for wait_for_download method"""
        # Clear previous line if not the first output
        sys.stdout.write("\r" + " " * 80 + "\r")
        
        # Format the progress string using ProgressFormatter
        progress_str = f"Overall: {overall_progress:.1f}% "
        progress_str += f"[{ProgressFormatter.format_elapsed_time(elapsed_seconds)}]"
        
        # Count active and completed downloads
        active = sum(1 for s in status_list if s.is_downloading)
        completed = sum(1 for s in status_list if hasattr(s, 'download_percentage') and s.download_percentage == 100)
        total = len(status_list)
        
        # Add counts to the progress string
        progress_str += f" | Active: {active}, Completed: {completed}, Total: {total}"
        
        # Add file-specific info for active downloads
        active_files = [s for s in status_list if s.is_downloading]
        if active_files:
            file_info = active_files[0].name
            if hasattr(active_files[0], 'download_percentage') and active_files[0].download_percentage is not None:
                file_info += f": {active_files[0].download_percentage}%"
                if active_files[0].download_throughput:
                    file_info += f" ({active_files[0].download_throughput})"
            progress_str += f" | {file_info}"
        
        # Output the progress
        sys.stdout.write(progress_str)
        sys.stdout.flush()
        
    def download_and_deploy_model(self, repo_id: str, quantization: str = 'q6_k', 
                                wait_for_download: bool = True, 
                                timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Download a model and deploy it in a single operation.
        
        This method handles both the download and deployment of a model, with these steps:
        1. Initiate model download
        2. Wait for download to complete (optional)
        3. Deploy the model
        
        Args:
            repo_id (str): The repository ID of the model to download and deploy
            quantization (str, optional): The quantization format to use. Defaults to 'q6_k'.
            wait_for_download (bool, optional): Whether to wait for download completion. Defaults to True.
            timeout (Optional[int], optional): Maximum seconds to wait for download. Defaults to None.
            
        Returns:
            Dict[str, Any]: A dictionary with information about the deployment.
        """
        import time
        
        try:
            # Step 1: Initiate download for the model
            print(f"Initiating download for {repo_id} with quantization {quantization}...")
            download_result = self.initiate_model_download(repo_id, quantization)
            
            # Step 2: Wait for download to complete if requested
            if wait_for_download:
                # Wait a moment for download to start
                time.sleep(2)
                
                # Check if there are active downloads
                status_list = self.check_download_status(repo_id)
                
                if status_list:
                    print(f"Waiting for download to complete...")
                    # Use our simplified wait_for_download method
                    status_list = self.wait_for_download(repo_id, timeout=timeout)
                    
                    # Add a small delay after download completes to ensure file system is ready
                    time.sleep(3)
                else:
                    # If no active downloads, check if the files are already downloaded
                    # This requires the model to be in our database now
                    try:
                        model = self.get_model_by_repo_id(repo_id)
                        if model and hasattr(model, 'm_files') and model.m_files:
                            files = model.m_files
                            # Use quant_manager to filter files by quantization
                            from ...utils.quant_manager import QuantizationManager
                            quant_manager = QuantizationManager()
                            
                            # Only consider GGUF files
                            gguf_files = [f for f in files if f.name and f.name.lower().endswith('.gguf')]
                            
                            # Filter by quantization
                            target_files = quant_manager.filter_files_by_quantization(gguf_files, quantization)
                            
                            if target_files:
                                downloaded_files = [f for f in target_files if hasattr(f, 'download') and f.download]
                                if downloaded_files and len(downloaded_files) == len(target_files):
                                    print(f"Model files for {repo_id} are already downloaded.")
                                else:
                                    print(f"Warning: Some files may not be fully downloaded. Proceeding anyway...")
                            else:
                                print(f"Warning: No files found matching quantization {quantization}. Proceeding anyway...")
                    except Exception as e:
                        print(f"Note: Could not verify download status: {str(e)}. Proceeding anyway...")
            
            # Step 3: Get the model (should be in our database now)
            model = self.get_model_by_repo_id(repo_id)
            
            if not model:
                raise ValueError(f"Could not find model {repo_id} in the database after download.")
            
            # Step 4: Deploy the model
            print(f"Deploying model {repo_id}...")
            
            # Add retry logic for deployment
            max_deploy_retries = 3
            deploy_retry_count = 0
            deploy_base_delay = 5  # seconds
            
            while deploy_retry_count < max_deploy_retries:
                try:
                    # Add a small delay before first deployment attempt
                    if deploy_retry_count == 0:
                        time.sleep(2)
                    
                    deployment_id = self.client.serving.deploy_model(repo_id=repo_id)
                    break  # Deployment successful, exit the retry loop
                except Exception as e:
                    deploy_retry_count += 1
                    
                    if deploy_retry_count >= max_deploy_retries:
                        # All retries failed
                        raise ValueError(f"Failed to deploy model after {max_deploy_retries} attempts: {str(e)}")
                    
                    # Calculate delay with exponential backoff
                    deploy_retry_delay = deploy_base_delay * (2 ** (deploy_retry_count - 1))
                    print(f"Deployment attempt {deploy_retry_count} failed: {str(e)}")
                    print(f"Retrying in {deploy_retry_delay} seconds...")
                    
                    # Wait before retrying
                    time.sleep(deploy_retry_delay)
            
            # Model is deployed
            print(f"Model {repo_id} successfully deployed!")
            
            # Get files from the model
            files = model.m_files if hasattr(model, 'm_files') and model.m_files else []
            
            # Create result dictionary with deployment information
            result = {
                "model": model,
                "target_files": [f for f in files if f.name and f.name.lower().endswith('.gguf')],
                "downloading_files": [f for f in files if hasattr(f, 'is_downloading') and f.is_downloading],
                "downloaded_files": [f for f in files if hasattr(f, 'download') and f.download],
                "pending_files": [],
                "total_progress": 100 if files else 0,
                "all_downloaded": bool(files),
                "any_downloading": False,
                "deployment_id": deployment_id
            }
            
            # Simple custom string representation
            class EnhancedDeploymentResult(dict):
                def __str__(self):
                    model_name = self["model"].name if self["model"].name else self["model"].repo_modelId
                    return f"Model {model_name} downloaded and deployed successfully. Deployment ID: {self['deployment_id']}"
                
                # Use ProgressFormatter for any formatting if needed
                def _format_details(self):
                    """Format additional details about the deployment if needed."""
                    files = self.get("downloaded_files", [])
                    if not files:
                        return "No files"
                    
                    total_size = sum(getattr(f, 'size', 0) or 0 for f in files)
                    return f"{len(files)} files, {ProgressFormatter.format_size(total_size)} total"
            
            return EnhancedDeploymentResult(result)
            
        except Exception as e:
            print(f"Error downloading and deploying model: {str(e)}")
            # Re-raise the exception to let the caller handle it
            raise
    
    def get_model_download_status(self, repo_id: str, 
                               quantization: Optional[str] = None) -> Dict[str, Any]:
        """
        Get comprehensive download status for a model, including file filtering by quantization.
        
        Args:
            repo_id (str): The repository ID of the model
            quantization (Optional[str], optional): The quantization format to filter by. Defaults to None.
            
        Returns:
            Dict[str, Any]: A dictionary with comprehensive status information
        """
        try:
            # Get the model
            model = self.get_model_by_repo_id(repo_id)
            
            if not model:
                return {
                    "error": f"Model {repo_id} not found",
                    "found": False
                }
            
            # Get files from the model
            files = model.m_files if hasattr(model, 'm_files') and model.m_files else []
            
            if not files:
                # If files weren't loaded with the model, fetch them directly
                files = self.search_hub_model_files(HubModelFileSearch(hub=model.hub, model=model.repo_modelId))
                model.m_files = files
            
            # Filter files by quantization if specified
            if quantization:
                # Use quant_manager to filter files by quantization
                from kamiwaza_sdk.utils.quant_manager import QuantizationManager
                quant_manager = QuantizationManager()
                
                # Only consider GGUF files
                gguf_files = [f for f in files if f.name and f.name.lower().endswith('.gguf')]
                
                # Filter by quantization
                target_files = quant_manager.filter_files_by_quantization(gguf_files, quantization)
            else:
                # If no quantization specified, include all GGUF files
                target_files = files
            
            # Analyze download status
            downloading_files = [f for f in target_files if hasattr(f, 'is_downloading') and f.is_downloading]
            downloaded_files = [f for f in target_files if hasattr(f, 'download') and f.download]
            pending_files = [f for f in target_files if f not in downloading_files and f not in downloaded_files]
            
            # Calculate overall progress
            total_progress = 0
            if downloading_files:
                for file in downloading_files:
                    total_progress += getattr(file, 'download_percentage', 0) or 0
                total_progress /= len(downloading_files)
            elif downloaded_files and len(downloaded_files) == len(target_files):
                total_progress = 100
            
            return {
                "model": model,
                "target_files": target_files,
                "downloading_files": downloading_files,
                "downloaded_files": downloaded_files,
                "pending_files": pending_files,
                "total_progress": total_progress,
                "all_downloaded": len(downloaded_files) == len(target_files) and len(target_files) > 0,
                "any_downloading": len(downloading_files) > 0,
                "found": True
            }
            
        except Exception as e:
            return {
                "error": str(e),
                "found": False
            }
