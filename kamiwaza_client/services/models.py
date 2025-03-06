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
        
        # Check if files are already downloaded using multiple indicators
        files_to_download = []
        already_downloaded_files = []
        
        # Get model files directly from the model object
        for file in compatible_files:
            # A file is considered downloaded if:
            # 1. It has download=True attribute, OR
            # 2. It appears in the directory where it should be downloaded
            
            is_downloaded = False
            
            # Check method 1: file.download attribute
            if hasattr(file, 'download') and file.download:
                is_downloaded = True
            
            # If the file is downloaded, add it to already_downloaded_files
            if is_downloaded:
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
        from datetime import datetime
        
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
                    self._display_progress(status_list, self._calculate_overall_progress(status_list), elapsed_seconds)
                
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
                            print(f"Total download time: {self._format_elapsed_time(elapsed_seconds)}")
                            print("Files downloaded:")
                            for file in model.m_files:
                                if hasattr(file, 'download') and file.download:
                                    size_str = f" ({self._format_size(file.size)})" if hasattr(file, 'size') and file.size else ""
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
            
    def _calculate_overall_progress(self, status_list):
        """Calculate the overall progress percentage from a list of download statuses"""
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
        # Convert seconds to integer to avoid formatting issues
        seconds = int(seconds)
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
        import time
        print(f"Preparing model {repo_id} with quantization {quantization}...")
        
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
                            from kamiwaza_client.utils.quant_manager import QuantizationManager
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
                raise ValueError(f"Model not found after download: {repo_id}")
            
            # Step 4: Deploy the model
            print(f"Deploying model {repo_id}...")
            
            # Add better retry logic for deployment
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
            
            # Step 5: Get the OpenAI client
            print(f"Creating OpenAI-compatible client...")
            openai_client = self.client.openai.get_client(repo_id=repo_id)
            
            # Step 6: Create result dictionary with custom string representation
            result = {
                "model": model,
                "files": model.m_files if hasattr(model, 'm_files') else [],
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
            # Handle any errors and provide error information
            error_msg = f"Error in download_and_deploy_model: {str(e)}"
            print(error_msg)
            return {
                "error": error_msg,
                "repo_id": repo_id,
                "quantization": quantization
            }

    def get_model_download_status(self, repo_id: str, quantization: Optional[str] = None) -> Dict[str, Any]:
        """
        Get detailed download status for a model.
        
        Args:
            repo_id (str): The repository ID of the model
            quantization (Optional[str]): Filter files by quantization
            
        Returns:
            Dict with download status information including:
            - model: The model object
            - target_files: Files matching the quantization filter
            - downloading_files: Files currently downloading
            - downloaded_files: Files already downloaded
            - pending_files: Files not yet downloaded
            - total_progress: Overall download progress percentage
            - all_downloaded: Whether all target files are downloaded
            - any_downloading: Whether any target files are downloading
        """
        # Get the model
        model = self.get_model_by_repo_id(repo_id)
        if not model:
            return {"error": f"Model not found: {repo_id}"}
        
        # Get all files for the model
        files = self.get_model_files_by_model_id(model.id)
        
        # Filter files by quantization if specified
        if quantization:
            # Use quant_manager to filter files by quantization
            from kamiwaza_client.utils.quant_manager import QuantizationManager
            quant_manager = QuantizationManager()
            
            # Only consider GGUF files
            gguf_files = [f for f in files if f.name and f.name.lower().endswith('.gguf')]
            
            # Filter by quantization
            target_files = quant_manager.filter_files_by_quantization(gguf_files, quantization)
        else:
            # If no quantization specified, include all GGUF files
            target_files = [f for f in files if f.name and f.name.lower().endswith('.gguf')]
        
        # Analyze download status
        downloading_files = [f for f in target_files if f.is_downloading]
        downloaded_files = [f for f in target_files if hasattr(f, 'download') and f.download]
        pending_files = [f for f in target_files if f not in downloading_files and f not in downloaded_files]
        
        # Calculate overall progress
        total_progress = 0
        if downloading_files:
            for file in downloading_files:
                total_progress += file.download_percentage or 0
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
            "any_downloading": len(downloading_files) > 0
        }
