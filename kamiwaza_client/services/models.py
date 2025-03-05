# kamiwaza_client/services/models.py

from typing import List, Optional, Union, Dict, Any
import time
from uuid import UUID
import platform
from ..exceptions import APIError
from ..schemas.models.model import Model, CreateModel, ModelConfig, CreateModelConfig
from ..schemas.models.model_file import ModelFile, CreateModelFile
from ..schemas.models.model_search import ModelSearchRequest, ModelSearchResponse, HubModelFileSearch
from ..schemas.models.downloads import ModelDownloadRequest, ModelDownloadStatus
from .base_service import BaseService
import difflib
import re
from ..utils.quant_manager import QuantizationManager

class ModelService(BaseService):
    def __init__(self, client):
        super().__init__(client)
        self._server_info = None  # Cache server info
        self.quant_manager = QuantizationManager()
        
        # For backwards compatibility, keep references to these attributes
        self._quant_variants = self.quant_manager._quant_variants
        self._priority_order = self.quant_manager._priority_order
        
    def get_model(self, model_id: Union[str, UUID]) -> Model:
        """Retrieve a specific model by its ID."""
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        response = self.client._request("GET", f"/models/{model_id}")
        return Model.model_validate(response)

    def create_model(self, model: CreateModel) -> Model:
        """Create a new model."""
        response = self.client._request("POST", "/models/", json=model.model_dump())
        return Model.model_validate(response)

    def delete_model(self, model_id: Union[str, UUID]) -> dict:
        """Delete a specific model by its ID."""
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        return self.client._request("DELETE", f"/models/{model_id}")

    def list_models(self, load_files: bool = False) -> List[Model]:
        """List all models, optionally including associated files."""
        response = self.client._request("GET", "/models/", params={"load_files": load_files})
        return [Model.model_validate(item) for item in response]

    def search_models(self, query: str, exact: bool = False, limit: int = 100, hubs_to_search: Optional[List[str]] = None, load_files: bool = True) -> List[Model]:
        """
        Search for models based on a query string.

        Args:
            query (str): The search query.
            exact (bool, optional): Whether to perform an exact match. Defaults to False.
            limit (int, optional): Maximum number of results to return. Defaults to 100.
            hubs_to_search (List[str], optional): List of hubs to search in. Defaults to None (search all hubs).
            load_files (bool, optional): Whether to load file information for each model. Defaults to True.

        Returns:
            List[Model]: A list of matching models.
        """
        search_request = ModelSearchRequest(
            query=query,
            exact=exact,
            limit=limit,
            hubs_to_search=hubs_to_search or ["*"]
        )
        response = self.client._request("POST", "/models/search/", json=search_request.model_dump())
        search_response = ModelSearchResponse.model_validate(response)
        result_models = [result.model for result in search_response.results]
        
        # Load file information for each model if requested
        if load_files and result_models:
            for model in result_models:
                try:
                    # Search for files for this model
                    if model.repo_modelId and model.hub:
                        files = self.search_hub_model_files(
                            HubModelFileSearch(hub=model.hub, model=model.repo_modelId)
                        )
                        # Add files to the model
                        model.m_files = files
                        
                        # Extract quantization information using the QuantizationManager
                        quants = set()
                        for file in files:
                            if file.name:
                                quant = self.quant_manager.detect_quantization(file.name)
                                if quant:
                                    quants.add(quant)
                        
                        # Store available quantizations in the model for display
                        model.available_quantizations = sorted(list(quants))
                except Exception as e:
                    print(f"Error loading files for model {model.repo_modelId}: {e}")
        
        # Add a summary line at the beginning when printing
        if result_models:
            original_models = result_models.copy()
            class EnhancedModelList(list):
                def __str__(self):
                    count = len(self)
                    if count == 0:
                        return "No models found matching your query."
                    else:
                        summary = f"Found {count} model{'s' if count > 1 else ''} matching '{query}':\n"
                        model_strings = [str(model) for model in self]
                        return summary + "\n\n".join(model_strings)
                        
            enhanced_models = EnhancedModelList(original_models)
            return enhanced_models
        
        return result_models

    def _get_exact_quant_match(self, filename: str, quantization: str) -> bool:
        """
        Check if a filename matches exactly a quantization pattern.
        
        Args:
            filename (str): The filename to check
            quantization (str): The quantization pattern to match
            
        Returns:
            bool: True if exact match found, False otherwise
        """
        # Use the QuantizationManager for matching
        return self.quant_manager.match_quantization(filename, quantization)

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
        else:
            # Model doesn't have multiple quantizations - use all model files
            # Filter to only include model files (exclude metadata, etc.)
            compatible_files = [
                file for file in files 
                if hasattr(file, 'name') and file.name and (
                    file.name.lower().endswith('.gguf') or 
                    file.name.lower().endswith('.safetensors') or
                    file.name.lower().endswith('.bin')
                )
            ]
            
            if not compatible_files:
                raise ValueError(f"No model files found for {repo_id}. Available files: {[f.name for f in files if hasattr(f, 'name')]}")
            
            # Log that we're ignoring quantization parameter
            if quantization != 'q6_k':  # Only log if user explicitly specified a quantization
                print(f"Note: Model {repo_id} doesn't have multiple quantization options. "
                      f"Ignoring specified quantization '{quantization}' and downloading all model files.")
        
        # Check if files are already downloaded
        files_to_download = []
        already_downloaded_files = []
        
        # Get model files directly from the model object
        for file in compatible_files:
            # Check if the file has the download attribute and it's True
            if file.download:
                # File is already downloaded
                already_downloaded_files.append(file)
            else:
                # File needs to be downloaded
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
        
        # Add custom string representation to the result dictionary
        class EnhancedDownloadResult(dict):
            def __str__(self):
                model_name = self["model"].name if self["model"].name else self["model"].repo_modelId
                status = self["result"].get("message", "Unknown status")
                
                # Format the file information
                files_info = []
                total_size = 0
                for file in self["files"]:
                    size_bytes = file.size if file.size else 0
                    total_size += size_bytes
                    size_formatted = self._format_size(size_bytes)
                    files_info.append(f"- {file.name} ({size_formatted})")
                
                # Create the formatted output
                if status == "Files already downloaded":
                    output = [
                        f"Model files for {model_name} are already downloaded",
                        f"Status: {status}",
                        "Files:"
                    ]
                    output.extend(files_info)
                    output.append("")
                    output.append(f"Total size: {self._format_size(total_size)}")
                    output.append("No download needed - files are ready to use")
                else:
                    output = [
                        f"Download initiated for: {model_name}",
                        f"Status: {status}",
                        "Files:"
                    ]
                    output.extend(files_info)
                    output.append("")
                    output.append(f"Total size: {self._format_size(total_size)}")
                    output.append("Use check_download_status() to monitor progress")
                
                return "\n".join(output)
                
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
        
        return EnhancedDownloadResult(result_dict)

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
                                    
                                    # Add speed if available - prefer API throughput
                                    if status.download_throughput:
                                        file_line += f" ({status.download_throughput}"
                                    elif hasattr(status, 'download_speed') and status.download_speed:
                                        speed_str = self._format_speed(status.download_speed)
                                        file_line += f" ({speed_str}"
                                    else:
                                        file_line += " ("
                                        
                                    # Add remaining time
                                    if status.download_remaining:
                                        file_line += f", {status.download_remaining} remaining)"
                                    elif hasattr(status, 'download_eta') and status.download_eta:
                                        eta_str = self._format_time(status.download_eta)
                                        file_line += f", {eta_str} remaining)"
                                    else:
                                        file_line += ")"
                            else:
                                if status.download_percentage == 100:
                                    completed_downloads += 1
                                    file_line += "Download complete"
                                else:
                                    file_line += "Not downloading"
                                    
                            output.append(file_line)
                        
                        # Add overall progress
                        output.append("")
                        if active_downloads > 0:
                            overall_progress = total_percentage / active_downloads
                            output.append(f"Overall progress: {overall_progress:.1f}% complete")
                        elif completed_downloads == len(self):
                            output.append("All downloads complete")
                        
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
                            return f"{seconds} seconds"
                        elif seconds < 3600:
                            minutes = seconds // 60
                            sec = seconds % 60
                            return f"{minutes}:{sec:02d} minutes"
                        else:
                            hours = seconds // 3600
                            minutes = (seconds % 3600) // 60
                            return f"{hours}:{minutes:02d} hours"
                
                return EnhancedStatusList(actual_download_status)
                
            return actual_download_status
        except Exception as e:
            print(f"Error checking download status: {e}")
            return []

    def get_model_files_download_status(self, repo_model_id: str) -> List[ModelDownloadStatus]:
        """
        Get the download status of specified model files.

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
                    print(f"Error parsing download status: {e}")
                    # Handle specific fields that might cause validation errors
                    if "download_elapsed" in str(e) or "download_remaining" in str(e) or "download_throughput" in str(e):
                        print(f"Using fallback parsing for item with id {item.get('id', 'unknown')}")
                        # Try a manual conversion
                        try:
                            # Create a modified copy of the item
                            modified_item = item.copy()
                            # Ensure these fields are strings
                            if "download_elapsed" in modified_item and not isinstance(modified_item["download_elapsed"], str):
                                modified_item["download_elapsed"] = str(modified_item["download_elapsed"])
                            if "download_remaining" in modified_item and not isinstance(modified_item["download_remaining"], str):
                                modified_item["download_remaining"] = str(modified_item["download_remaining"])
                            if "download_throughput" in modified_item and not isinstance(modified_item["download_throughput"], str):
                                modified_item["download_throughput"] = str(modified_item["download_throughput"])
                                
                            # Try validation again
                            status = ModelDownloadStatus.model_validate(modified_item)
                            results.append(status)
                        except Exception as e2:
                            print(f"Fallback parsing also failed: {e2}")
                
            return results
        except Exception as e:
            print(f"Exception in get_model_files_download_status: {e}")
            return []

    def get_model_memory_usage(self, model_id: Union[str, UUID]) -> int:
        """Get the memory usage of a model."""
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        return self.client._request("GET", f"/models/{model_id}/memory_usage")

    # Model File operations
    def delete_model_file(self, model_file_id: Union[str, UUID]) -> dict:
        """Delete a model file by its ID."""
        try:
            if isinstance(model_file_id, str):
                model_file_id = UUID(model_file_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_file_id}") from e
            
        return self.client._request("DELETE", f"/model_files/{model_file_id}")

    def get_model_file(self, model_file_id: Union[str, UUID]) -> ModelFile:
        """Retrieve a model file by its ID."""
        try:
            if isinstance(model_file_id, str):
                model_file_id = UUID(model_file_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_file_id}") from e
        
        response = self.client._request("GET", f"/model_files/{model_file_id}")
        return ModelFile.model_validate(response)
    
    def get_model_files_by_model_id(self, model_id: Union[str, UUID]) -> List[ModelFile]:
        """Retrieve all model files by their model ID."""
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        # Get the model which includes the files
        response = self.client._request("GET", f"/models/{model_id}")
        
        # Extract the files from the response
        if "m_files" in response:
            return [ModelFile.model_validate(item) for item in response["m_files"]]
        return []

    def list_model_files(self) -> List[ModelFile]:
        """List all model files."""
        response = self.client._request("GET", "/model_files/")
        return [ModelFile.model_validate(item) for item in response]
    
    def get_model_by_repo_id(self, repo_id: str) -> Model:
        """Retrieve a model by its repo_modelId by searching through the models list."""
        models = self.list_models()
        for model in models:
            if model.repo_modelId == repo_id:
                return model
        return None

    def create_model_file(self, model_file: CreateModelFile) -> ModelFile:
        """Create a new model file."""
        response = self.client._request("POST", "/model_files/", json=model_file.model_dump())
        return ModelFile.model_validate(response)

    def search_hub_model_files(self, search_request: Union[dict, HubModelFileSearch]) -> List[ModelFile]:
        """Search for model files in a specific hub.
        
        Args:
            search_request: Either a dictionary containing hub and model information,
                          or a HubModelFileSearch schema object.
        """
        if isinstance(search_request, dict):
            search_request = HubModelFileSearch.model_validate(search_request)
        
        response = self.client._request("POST", "/model_files/search/", json=search_request.model_dump())
        return [ModelFile.model_validate(item) for item in response]

    def get_model_file_memory_usage(self, model_file_id: Union[str, UUID]) -> int:
        """Get the memory usage of a model file."""
        try:
            if isinstance(model_file_id, str):
                model_file_id = UUID(model_file_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_file_id}") from e
            
        return self.client._request("GET", f"/model_files/{model_file_id}/memory_usage")

    # Model Configuration operations
    def create_model_config(self, config: CreateModelConfig) -> ModelConfig:
        """Create a new model configuration."""
        response = self.client._request("POST", "/model_configs/", json=config.model_dump())
        return ModelConfig.model_validate(response)

    def get_model_configs(self, model_id: Union[str, UUID]) -> List[ModelConfig]:
        """Get a list of model configurations for a given model ID."""
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        response = self.client._request("GET", "/model_configs/", params={"model_id": str(model_id)})
        return [ModelConfig.model_validate(item) for item in response]

    def get_model_configs_for_model(self, model_id: Union[str, UUID], default: bool = False) -> List[ModelConfig]:
        """Get a list of model configurations for a given model ID."""
        try:
            if isinstance(model_id, str):
                model_id = UUID(model_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_id}") from e
            
        response = self.client._request("GET", f"/models/{model_id}/configs", params={"default": default})
        return [ModelConfig.model_validate(item) for item in response]

    def get_model_config(self, model_config_id: Union[str, UUID]) -> ModelConfig:
        """Get a model configuration by its ID."""
        try:
            if isinstance(model_config_id, str):
                model_config_id = UUID(model_config_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_config_id}") from e
            
        response = self.client._request("GET", f"/model_configs/{model_config_id}")
        return ModelConfig.model_validate(response)

    def delete_model_config(self, model_config_id: Union[str, UUID]) -> None:
        """Delete a model configuration by its ID."""
        try:
            if isinstance(model_config_id, str):
                model_config_id = UUID(model_config_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_config_id}") from e
            
        self.client._request("DELETE", f"/model_configs/{model_config_id}")

    def update_model_config(self, model_config_id: Union[str, UUID], config: CreateModelConfig) -> ModelConfig:
        """Update a model configuration by its ID."""
        try:
            if isinstance(model_config_id, str):
                model_config_id = UUID(model_config_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format: {model_config_id}") from e
            
        response = self.client._request("PUT", f"/model_configs/{model_config_id}", json=config.model_dump())
        return ModelConfig.model_validate(response)
















    ### This stuff could be moved to a helper class

    def _get_server_os(self) -> str:
        """Get and cache server OS info from cluster hardware"""
        if self._server_info is None:
            try:
                # Get first hardware entry - limit=1 for efficiency
                hardware = self.client.cluster.list_hardware(limit=1)
                if hardware and len(hardware) > 0:
                    self._server_info = {
                        'os': hardware[0].os,
                        'platform': hardware[0].platform,
                        'processors': hardware[0].processors
                    }
                else:
                    raise ValueError("No hardware information available")
            except Exception as e:
                raise APIError(f"Failed to get server info: {str(e)}")
        
        return self._server_info['os']

    def filter_compatible_models(self, model_name: str) -> List[Dict[str, Any]]:
        """Filter models based on server compatibility"""
        server_os = self._get_server_os()
        models = self.search_models(model_name)
        
        # Let server handle compatibility via download endpoint
        # Just organize the model info for the user
        model_info = []
        for model in models:
            files = self.search_hub_model_files(
                HubModelFileSearch(
                    hub=model.hub, 
                    model=model.repo_modelId
                )
            )
            if files:  # If there are any files, include the model
                model_info.append({
                    "model": model,
                    "files": files,
                    "server_platform": self._server_info  # Include server info for reference
                })

        return model_info


    def _filter_files_for_os(self, files: List[ModelFile]) -> List[ModelFile]:
        """
        Filter files that are compatible with the current operating system.

        Args:
            files (List[ModelFile]): List of available model files.

        Returns:
            List[ModelFile]: List of compatible files for the current OS.
        """
        current_os = platform.system()

        if current_os == 'Darwin':  # macOS
            return [file for file in files if file.name.lower().endswith('.gguf')]
        elif current_os == 'Linux':
            return [file for file in files if not file.name.lower().endswith('.gguf')]
        else:
            raise ValueError(f"Unsupported operating system: {current_os}")

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
        import time
        import sys
        import re
        from datetime import datetime, timedelta
        
        # Initialize variables
        start_time = datetime.now()
        elapsed_seconds = 0
        previous_percentages = {}
        download_speeds = {}
        last_status_list = []
        
        # Add retry logic for when no downloads are found
        max_empty_retries = 5
        empty_retry_count = 0
        empty_retry_delay = 2  # seconds
        
        # Track high water mark for download percentage
        highest_percentage_seen = 0
        
        try:
            while True:
                # Check current status
                status_list = self.check_download_status(repo_id)
                
                if status_list:
                    # Reset retry counter when we find active downloads
                    empty_retry_count = 0
                    last_status_list = status_list
                    
                    # Update highest percentage seen
                    for status in status_list:
                        if status.download_percentage is not None and status.download_percentage > highest_percentage_seen:
                            highest_percentage_seen = status.download_percentage
                
                if not status_list:
                    # No active downloads found - implement retry logic
                    empty_retry_count += 1
                    
                    # If we've seen high download percentages (like 90%+) and then downloads disappear,
                    # it's likely the download just completed but the system hasn't updated yet
                    if highest_percentage_seen >= 90:
                        if show_progress:
                            print(f"\nDownload appears to be completing (reached {highest_percentage_seen}%). Waiting for system to finalize...")
                        
                        # Wait longer to allow the system to update file status
                        completion_wait = 10  # seconds
                        time.sleep(completion_wait)
                        
                        # Refresh the model to get updated file status
                        model = self.get_model_by_repo_id(repo_id)
                        
                        if model and hasattr(model, 'm_files') and model.m_files:
                            # Check if files are now marked as downloaded
                            all_downloaded = all(file.download for file in model.m_files if hasattr(file, 'download'))
                            
                            if all_downloaded:
                                if show_progress:
                                    print("\nDownload complete for:", repo_id)
                                    print(f"Total download time: {self._format_elapsed_time(elapsed_seconds)}")
                                    print("Files downloaded:")
                                    for file in model.m_files:
                                        size_str = f" ({self._format_size(file.size)})" if hasattr(file, 'size') and file.size else ""
                                        print(f"- {file.name}{size_str}")
                                    
                                    # Show model ID if available
                                    if hasattr(model, 'id') and model.id:
                                        print(f"Model ID: {model.id}")
                                
                                # Return the last status list we had
                                return last_status_list if last_status_list else []
                    
                    if empty_retry_count >= max_empty_retries:
                        # After multiple retries, check if files are actually downloaded
                        model = self.get_model_by_repo_id(repo_id)
                        if model and hasattr(model, 'm_files') and model.m_files:
                            all_downloaded = all(file.download for file in model.m_files if hasattr(file, 'download'))
                            if all_downloaded:
                                if show_progress:
                                    print("\nAll files appear to be already downloaded for:", repo_id)
                                    print("Files:")
                                    for file in model.m_files:
                                        size_str = f" ({self._format_size(file.size)})" if hasattr(file, 'size') and file.size else ""
                                        print(f"- {file.name}{size_str}")
                                    
                                    # Show model ID if available
                                    if hasattr(model, 'id') and model.id:
                                        print(f"Model ID: {model.id}")
                                
                                return last_status_list if last_status_list else []
                            else:
                                # Files exist but not all are downloaded
                                # Instead of just warning, let's wait a bit longer
                                if show_progress:
                                    print(f"\nDownload status unclear for {repo_id}. Waiting additional time for system to update...")
                                
                                # Wait longer to allow the system to update file status
                                additional_wait = 15  # seconds
                                time.sleep(additional_wait)
                                
                                # Refresh the model to get updated file status
                                model = self.get_model_by_repo_id(repo_id)
                                
                                if model and hasattr(model, 'm_files') and model.m_files:
                                    # Check again if files are now marked as downloaded
                                    all_downloaded = all(file.download for file in model.m_files if hasattr(file, 'download'))
                                    
                                    if all_downloaded:
                                        if show_progress:
                                            print("\nDownload complete for:", repo_id)
                                            print(f"Total download time: {self._format_elapsed_time(elapsed_seconds)}")
                                            print("Files downloaded:")
                                            for file in model.m_files:
                                                size_str = f" ({self._format_size(file.size)})" if hasattr(file, 'size') and file.size else ""
                                                print(f"- {file.name}{size_str}")
                                            
                                            # Show model ID if available
                                            if hasattr(model, 'id') and model.id:
                                                print(f"Model ID: {model.id}")
                                        
                                        return last_status_list if last_status_list else []
                                    else:
                                        if show_progress:
                                            print(f"Warning: Some files for {repo_id} are not marked as downloaded, but no active downloads were found.")
                                            print("This may indicate an issue with the download process.")
                                            print("Assuming download is complete and proceeding...")
                                        
                                        return last_status_list if last_status_list else []
                        
                        if show_progress:
                            print(f"No active downloads found for {repo_id} after {max_empty_retries} retries")
                        
                        return last_status_list if last_status_list else []
                    
                    # Wait before retrying
                    if show_progress:
                        print(f"No active downloads found yet for {repo_id}, retrying in {empty_retry_delay} seconds... (attempt {empty_retry_count}/{max_empty_retries})")
                    time.sleep(empty_retry_delay)
                    continue
                
                # Calculate overall progress
                total_percentage = 0
                active_downloads = 0
                completed_downloads = 0
                
                # Check if all downloads are complete
                all_completed = True
                for status in status_list:
                    if status.is_downloading:
                        all_completed = False
                        active_downloads += 1
                        if status.download_percentage is not None:
                            total_percentage += status.download_percentage
                            
                            # Use API-provided values if available, otherwise calculate
                            if not hasattr(status, 'download_speed') or not status.download_speed:
                                # Try to parse download_throughput if available (e.g., "6.12MB/s")
                                if status.download_throughput:
                                    try:
                                        # Extract number and unit from throughput string
                                        match = re.match(r'([\d.]+)([KMG]B)/s', status.download_throughput)
                                        if match:
                                            value, unit = match.groups()
                                            value = float(value)
                                            # Convert to bytes/sec based on unit
                                            if unit == 'KB':
                                                status.download_speed = value * 1024
                                            elif unit == 'MB':
                                                status.download_speed = value * 1024 * 1024
                                            elif unit == 'GB':
                                                status.download_speed = value * 1024 * 1024 * 1024
                                    except:
                                        pass  # If parsing fails, leave as is
                            
                            # Calculate speed only if not available from API
                            if not hasattr(status, 'download_speed') or not status.download_speed:
                                # Calculate from percentage change if possible
                                if status.id in previous_percentages:
                                    prev_pct = previous_percentages[status.id]
                                    pct_diff = status.download_percentage - prev_pct
                                    
                                    # If we have file information, estimate bytes transferred
                                    model = self.get_model_by_repo_id(repo_id)
                                    file = next((f for f in model.m_files if f.name == status.name), None)
                                    
                                    if file and file.size and pct_diff > 0:
                                        bytes_per_interval = (file.size * pct_diff) / 100
                                        bytes_per_second = bytes_per_interval / polling_interval
                                        
                                        # Update download speed estimate
                                        download_speeds[status.id] = bytes_per_second
                                        status.download_speed = bytes_per_second
                                    
                            # Store current percentage for next iteration
                            previous_percentages[status.id] = status.download_percentage
                    elif status.download_percentage == 100:
                        completed_downloads += 1
                        
                # If there are active downloads, calculate overall progress
                if active_downloads > 0:
                    overall_progress = total_percentage / active_downloads
                else:
                    overall_progress = 100 if completed_downloads > 0 else 0
                
                # Display progress if requested
                if show_progress:
                    self._display_progress(status_list, overall_progress, elapsed_seconds)
                
                # Check if all downloads are complete
                if all_completed:
                    # Wait a moment to ensure file status is updated
                    time.sleep(2)
                    
                    # Refresh the model to get updated file status
                    model = self.get_model_by_repo_id(repo_id)
                    all_files_downloaded = True
                    
                    if model and hasattr(model, 'm_files') and model.m_files:
                        for file in model.m_files:
                            # Check if this file was part of the download
                            if any(status.name == file.name for status in status_list):
                                # Verify the file is marked as downloaded
                                if not hasattr(file, 'download') or not file.download:
                                    all_files_downloaded = False
                                    if show_progress:
                                        print(f"\nWaiting for file {file.name} to be marked as downloaded...")
                    
                    # If verification passes or we don't have file info, proceed
                    if all_files_downloaded or not (model and hasattr(model, 'm_files') and model.m_files):
                        if show_progress:
                            print("\nDownload complete for:", repo_id)
                            print(f"Total download time: {self._format_elapsed_time(elapsed_seconds)}")
                            print("Files downloaded:")
                            for status in status_list:
                                # Get file size if available
                                model = self.get_model_by_repo_id(repo_id)
                                file = next((f for f in model.m_files if f.name == status.name), None)
                                size_str = f" ({self._format_size(file.size)})" if file and file.size else ""
                                print(f"- {status.name}{size_str}")
                            
                            # Show model ID
                            if status_list and status_list[0].m_id:
                                print(f"Model ID: {status_list[0].m_id}")
                        return status_list
                    else:
                        # If verification fails, wait a bit longer
                        if show_progress:
                            print("\nDownload reported complete, but files are not yet marked as downloaded. Waiting additional time...")
                        
                        # Wait longer to allow the system to update file status
                        additional_wait = 10  # seconds
                        time.sleep(additional_wait)
                        
                        # Refresh the model again
                        model = self.get_model_by_repo_id(repo_id)
                        all_files_downloaded = True
                        
                        if model and hasattr(model, 'm_files') and model.m_files:
                            for file in model.m_files:
                                # Check if this file was part of the download
                                if any(status.name == file.name for status in status_list):
                                    # Verify the file is marked as downloaded
                                    if not hasattr(file, 'download') or not file.download:
                                        all_files_downloaded = False
                        
                        # After waiting, assume download is complete even if verification fails
                        if show_progress:
                            print("\nDownload complete for:", repo_id)
                            print(f"Total download time: {self._format_elapsed_time(elapsed_seconds)}")
                            print("Files downloaded:")
                            for status in status_list:
                                # Get file size if available
                                model = self.get_model_by_repo_id(repo_id)
                                file = next((f for f in model.m_files if f.name == status.name), None)
                                size_str = f" ({self._format_size(file.size)})" if file and file.size else ""
                                print(f"- {status.name}{size_str}")
                            
                            # Show model ID
                            if status_list and status_list[0].m_id:
                                print(f"Model ID: {status_list[0].m_id}")
                        
                        return status_list
                
                # Check timeout
                elapsed_seconds = (datetime.now() - start_time).total_seconds()
                if timeout and elapsed_seconds > timeout:
                    raise TimeoutError(f"Download did not complete within {timeout} seconds")
                
                # Sleep before next check
                time.sleep(polling_interval)
                
        except KeyboardInterrupt:
            print("\nDownload monitoring interrupted")
            return self.check_download_status(repo_id)
    
    def _display_progress(self, status_list, overall_progress, elapsed_seconds):
        """Display download progress for wait_for_download method"""
        import sys
        
        # Clear previous line if not the first output
        if elapsed_seconds > 0:
            sys.stdout.write("\033[F" * (len(status_list) + 3))  # Move cursor up
        
        # Display progress bar
        bar_length = 30
        filled_length = int(overall_progress / 100 * bar_length)
        bar = '▓' * filled_length + '░' * (bar_length - filled_length)
        print(f"Download progress: [{bar}] {overall_progress:.1f}% complete")
        
        # Display individual file progress
        for status in status_list:
            if status.is_downloading and status.download_percentage is not None:
                # Use API-provided information if available
                if status.download_throughput:
                    speed_str = f" ({status.download_throughput})"
                elif hasattr(status, 'download_speed') and status.download_speed:
                    speed_str = f" ({self._format_speed(status.download_speed)})"
                else:
                    speed_str = ""
                    
                print(f"{status.name}: {status.download_percentage}%{speed_str}")
            else:
                completion = "complete" if status.download_percentage == 100 else "not started"
                print(f"{status.name}: {completion}")
        
        # Display estimated time
        if elapsed_seconds > 0:
            print(f"Elapsed time: {self._format_elapsed_time(int(elapsed_seconds))}")
            
    def _format_elapsed_time(self, seconds):
        """Format elapsed time in MM:SS format to match API output"""
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes:02d}:{secs:02d}"

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

    def download_and_deploy_model(self, repo_id: str, quantization: str = 'q6_k', wait_for_download: bool = True, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Download and deploy a model in one step.
        
        This method encapsulates the entire workflow from model search to deployment:
        1. Searches for the model
        2. Checks if the model files are already downloaded
        3. Downloads the model files if needed
        4. Deploys the model
        5. Returns information about the deployment
        
        Args:
            repo_id (str): The repo ID of the model to download and deploy.
            quantization (str, optional): The desired quantization level. Defaults to 'q6_k'.
            wait_for_download (bool, optional): Whether to wait for the download to complete. Defaults to True.
            timeout (Optional[int], optional): Timeout in seconds for the download. Defaults to None (no timeout).
            
        Returns:
            Dict[str, Any]: A dictionary containing information about the deployment.
        """
        print(f"Preparing model {repo_id} with quantization {quantization}...")
        
        try:
            # Step 1: Download the model (if needed)
            download_result = self.initiate_model_download(repo_id, quantization)
            
            # Step 2: Wait for download to complete (if needed and requested)
            if wait_for_download:
                # Check if we need to wait (if files were already downloaded, we don't need to wait)
                if download_result['result'].get('message') != "Files already downloaded":
                    print(f"Waiting for download to complete...")
                    status_list = self.wait_for_download(repo_id, timeout=timeout)
                    
                    # Add a delay after download completion to ensure files are fully processed
                    import time
                    post_download_delay = 5  # seconds
                    time.sleep(post_download_delay)
                else:
                    print("Files already downloaded, proceeding to deployment...")
            
            # Verify files are downloaded before proceeding to deployment
            model = self.get_model_by_repo_id(repo_id)
            if model and hasattr(model, 'm_files') and model.m_files:
                files_to_check = [f for f in model.m_files if hasattr(f, 'name') and f.name]
                not_downloaded = [f.name for f in files_to_check if not hasattr(f, 'download') or not f.download]
                
                if not_downloaded:
                    # Some files are not marked as downloaded - wait a bit longer
                    print(f"Waiting for file system to finalize download status...")
                    
                    # Wait additional time
                    import time
                    additional_wait = 10  # seconds
                    time.sleep(additional_wait)
                    
                    # Refresh model to get updated download status
                    model = self.get_model_by_repo_id(repo_id)
                    if model and hasattr(model, 'm_files') and model.m_files:
                        files_to_check = [f for f in model.m_files if hasattr(f, 'name') and f.name]
                        not_downloaded = [f.name for f in files_to_check if not hasattr(f, 'download') or not f.download]
                        
                        if not_downloaded:
                            print(f"Warning: Some files may not be fully downloaded, but proceeding with deployment anyway.")
            
            # Step 3: Deploy the model
            print(f"Deploying model {repo_id}...")
            
            # Add retry logic for deployment
            max_deploy_retries = 3
            deploy_retry_count = 0
            deploy_retry_delay = 5  # seconds
            
            while deploy_retry_count < max_deploy_retries:
                try:
                    # Add a small delay before deployment to ensure files are ready
                    import time
                    time.sleep(2)
                    
                    deployment_id = self.client.serving.deploy_model(repo_id=repo_id)
                    break  # Deployment successful, exit the retry loop
                except Exception as e:
                    deploy_retry_count += 1
                    if deploy_retry_count >= max_deploy_retries:
                        # All retries failed
                        raise ValueError(f"Failed to deploy model after {max_deploy_retries} attempts: {str(e)}")
                    
                    print(f"Deployment attempt {deploy_retry_count} failed: {str(e)}")
                    print(f"Retrying in {deploy_retry_delay} seconds...")
                    
                    # Wait before retrying
                    import time
                    time.sleep(deploy_retry_delay)
                    
                    # Double the delay for next retry (exponential backoff)
                    deploy_retry_delay *= 2
            
            # Step 4: Get the OpenAI client
            print(f"Creating OpenAI-compatible client...")
            openai_client = self.client.openai.get_client(repo_id=repo_id)
            
            # Step 5: Create result dictionary with custom string representation
            result = {
                "model": download_result["model"],
                "files": download_result["files"],
                "deployment_id": deployment_id,
                "openai_client": openai_client
            }
            
            # Add custom string representation
            class EnhancedDeploymentResult(dict):
                def __str__(self):
                    model_name = self["model"].name if self["model"].name else self["model"].repo_modelId
                    
                    # Format the file information
                    files_info = []
                    total_size = 0
                    for file in self["files"]:
                        size_bytes = file.size if file.size else 0
                        total_size += size_bytes
                        size_formatted = self._format_size(size_bytes)
                        files_info.append(f"- {file.name} ({size_formatted})")
                    
                    # Create the formatted output
                    output = [
                        f"Model {model_name} is ready for inference!",
                        f"Deployment ID: {self['deployment_id']}",
                        "",
                        "Files:"
                    ]
                    output.extend(files_info)
                    output.append("")
                    output.append(f"Total size: {self._format_size(total_size)}")
                    output.append("")
                    output.append("Example usage:")
                    output.append("```python")
                    output.append("response = openai_client.chat.completions.create(")
                    output.append("    messages=[")
                    output.append("        {\"role\": \"user\", \"content\": \"Hello, how are you?\"},")
                    output.append("    ],")
                    output.append("    model=\"model\",")
                    output.append(")")
                    output.append("print(response.choices[0].message.content)")
                    output.append("```")
                    
                    return "\n".join(output)
                
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
            
            return EnhancedDeploymentResult(result)
            
        except Exception as e:
            # Improved error handling
            error_msg = f"Error in download_and_deploy_model: {str(e)}"
            print(error_msg)
            
            # Return a dictionary with error information
            return {
                "error": error_msg,
                "repo_id": repo_id,
                "quantization": quantization
            }
