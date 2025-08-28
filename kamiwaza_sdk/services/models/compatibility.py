from typing import List, Optional, Union, Dict, Any
import platform
from uuid import UUID
from ...exceptions import APIError
from ...schemas.models.model_file import ModelFile
from ...schemas.models.model_search import HubModelFileSearch


class CompatibilityMixin:
    """Mixin for OS compatibility checks."""
    
    def _get_server_os(self) -> str:
        """
        Get and cache server OS info from cluster hardware.
        
        Returns:
            str: The server operating system.
        """
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
        """
        Filter models based on server compatibility.
        
        Args:
            model_name (str): The name of the model to filter for compatibility.
            
        Returns:
            List[Dict[str, Any]]: A list of compatible models with their files.
        """
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
