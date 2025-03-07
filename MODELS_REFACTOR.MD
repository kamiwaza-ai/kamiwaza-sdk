# ModelService Refactoring Plan

## Overview

The current `models.py` file (1123 lines) is too large and contains multiple distinct functionalities. This refactoring plan aims to split it into smaller, more manageable components to improve maintainability and readability.

## New Directory Structure

```
kamiwaza_client/
  services/
    models/
      __init__.py             # Exports the ModelService class
      base.py                 # Core ModelService class with basic operations
      search.py               # Model search functionality
      downloads.py            # Download initiation and status checking
      files.py                # Model file operations
      configs.py              # Model configuration operations
      compatibility.py        # OS compatibility checks
  utils/
    quant_manager.py          # Already exists
    download_tracker.py       # New: Download progress tracking
    progress_formatter.py     # New: Formatting utilities for file sizes, times, etc.
```

## Implementation Strategy

1. Create the new directory structure
2. Implement each module with appropriate mixins
3. Test thoroughly to ensure functionality is maintained

## Module Details

### services/models/__init__.py
```python
from .base import ModelService

__all__ = ['ModelService']
```

### services/models/base.py
Core ModelService class that inherits from all mixins:

```python
from typing import List, Optional, Union, Dict, Any
from uuid import UUID
from ...exceptions import APIError
from ...schemas.models.model import Model, CreateModel
from ...utils.quant_manager import QuantizationManager
from ..base_service import BaseService
from .search import ModelSearchMixin
from .downloads import ModelDownloadMixin
from .files import ModelFileMixin
from .configs import ModelConfigMixin
from .compatibility import CompatibilityMixin

class ModelService(BaseService, 
                  ModelSearchMixin,
                  ModelDownloadMixin, 
                  ModelFileMixin, 
                  ModelConfigMixin,
                  CompatibilityMixin):
    """Service for managing models in the Kamiwaza platform."""
    
    def __init__(self, client):
        super().__init__(client)
        self._server_info = None  # Cache server info
        self.quant_manager = QuantizationManager()
```

## New Utility Classes

### utils/download_tracker.py
Utilities for tracking download progress.

### utils/progress_formatter.py
Utilities for formatting progress information (file sizes, times, etc.).

## Function Mapping

Below is a complete mapping of every function in the current `models.py` file to its new location:

| Current Function | New Location |
|------------------|--------------|
| `__init__` | `models/base.py` |
| `get_model` | `models/base.py` |
| `create_model` | `models/base.py` |
| `delete_model` | `models/base.py` |
| `list_models` | `models/base.py` |
| `search_models` | `models/search.py` |
| `_get_exact_quant_match` | `models/search.py` |
| `initiate_model_download` | `models/downloads.py` |
| `check_download_status` | `models/downloads.py` |
| `get_model_files_download_status` | `models/downloads.py` |
| `get_model_memory_usage` | `models/files.py` |
| `delete_model_file` | `models/files.py` |
| `get_model_file` | `models/files.py` |
| `get_model_files_by_model_id` | `models/files.py` |
| `list_model_files` | `models/files.py` |
| `get_model_by_repo_id` | `models/base.py` |
| `create_model_file` | `models/files.py` |
| `search_hub_model_files` | `models/files.py` |
| `get_model_file_memory_usage` | `models/files.py` |
| `create_model_config` | `models/configs.py` |
| `get_model_configs` | `models/configs.py` |
| `get_model_configs_for_model` | `models/configs.py` |
| `get_model_config` | `models/configs.py` |
| `delete_model_config` | `models/configs.py` |
| `update_model_config` | `models/configs.py` |
| `_get_server_os` | `models/compatibility.py` |
| `filter_compatible_models` | `models/compatibility.py` |
| `_filter_files_for_os` | `models/compatibility.py` |
| `wait_for_download` | `models/downloads.py` |
| `_calculate_overall_progress` | `utils/download_tracker.py` |
| `_display_progress` | `models/downloads.py` |
| `_format_elapsed_time` | `utils/progress_formatter.py` |
| `_format_size` | `utils/progress_formatter.py` |
| `_format_speed` | `utils/progress_formatter.py` |
| `_format_time` | `utils/progress_formatter.py` |
| `download_and_deploy_model` | `models/downloads.py` |
| `get_model_download_status` | `models/downloads.py` |

## Class Definitions

### ModelSearchMixin
```python
class ModelSearchMixin:
    """Mixin for model search functionality."""
    
    def search_models(self, query: str, exact: bool = False, limit: int = 100, 
                     hubs_to_search: Optional[List[str]] = None, 
                     load_files: bool = True) -> List[Model]:
        """Search for models based on a query string."""
        
    def _get_exact_quant_match(self, filename: str, quantization: str) -> bool:
        """Check if a filename matches a specific quantization."""
```

### ModelDownloadMixin
```python
class ModelDownloadMixin:
    """Mixin for model download functionality."""
    
    def initiate_model_download(self, repo_id: str, quantization: str = 'q6_k') -> Dict[str, Any]:
        """Initiate a model download."""
        
    def check_download_status(self, repo_id: str) -> List[ModelDownloadStatus]:
        """Check the status of a model download."""
        
    def get_model_files_download_status(self, repo_model_id: str) -> List[ModelDownloadStatus]:
        """Get the download status of model files.
        
        Note: Need to investigate the difference between this method and check_download_status
        to potentially consolidate functionality.
        """
        
    def wait_for_download(self, repo_id: str, polling_interval: int = 5, 
                         timeout: Optional[int] = None, 
                         show_progress: bool = True) -> List[ModelDownloadStatus]:
        """Wait for a model download to complete."""
        
    def _display_progress(self, status_list, overall_progress, elapsed_seconds):
        """Display download progress."""
        
    def download_and_deploy_model(self, repo_id: str, quantization: str = 'q6_k', 
                                 wait_for_download: bool = True, 
                                 timeout: Optional[int] = None) -> Dict[str, Any]:
        """Download and deploy a model."""
        
    def get_model_download_status(self, repo_id: str, 
                                 quantization: Optional[str] = None) -> Dict[str, Any]:
        """Get the download status of a model."""
```

### ModelFileMixin
```python
class ModelFileMixin:
    """Mixin for model file operations."""
    
    def get_model_memory_usage(self, model_id: Union[str, UUID]) -> int:
        """Get the memory usage of a model."""
        
    def delete_model_file(self, model_file_id: Union[str, UUID]) -> dict:
        """Delete a model file."""
        
    def get_model_file(self, model_file_id: Union[str, UUID]) -> ModelFile:
        """Get a model file by ID."""
        
    def get_model_files_by_model_id(self, model_id: Union[str, UUID]) -> List[ModelFile]:
        """Get model files by model ID."""
        
    def list_model_files(self) -> List[ModelFile]:
        """List all model files."""
        
    def create_model_file(self, model_file: CreateModelFile) -> ModelFile:
        """Create a model file."""
        
    def search_hub_model_files(self, search_request: Union[dict, HubModelFileSearch]) -> List[ModelFile]:
        """Search for model files in the hub."""
        
    def get_model_file_memory_usage(self, model_file_id: Union[str, UUID]) -> int:
        """Get the memory usage of a model file."""
```

### ModelConfigMixin
```python
class ModelConfigMixin:
    """Mixin for model configuration operations."""
    
    def create_model_config(self, config: CreateModelConfig) -> ModelConfig:
        """Create a model configuration."""
        
    def get_model_configs(self, model_id: Union[str, UUID]) -> List[ModelConfig]:
        """Get model configurations by model ID."""
        
    def get_model_configs_for_model(self, model_id: Union[str, UUID], default: bool = False) -> List[ModelConfig]:
        """Get model configurations for a model."""
        
    def get_model_config(self, model_config_id: Union[str, UUID]) -> ModelConfig:
        """Get a model configuration by ID."""
        
    def delete_model_config(self, model_config_id: Union[str, UUID]) -> None:
        """Delete a model configuration."""
        
    def update_model_config(self, model_config_id: Union[str, UUID], config: CreateModelConfig) -> ModelConfig:
        """Update a model configuration."""
```

### CompatibilityMixin
```python
class CompatibilityMixin:
    """Mixin for OS compatibility checks."""
    
    def _get_server_os(self) -> str:
        """Get the server OS."""
        
    def filter_compatible_models(self, model_name: str) -> List[Dict[str, Any]]:
        """Filter models compatible with the server OS."""
        
    def _filter_files_for_os(self, files: List[ModelFile]) -> List[ModelFile]:
        """Filter files compatible with the server OS."""
```

## Implementation Notes

1. All schemas will remain in their current location
2. Each mixin will be responsible for a specific aspect of model management
3. Utility functions will be moved to appropriate utility classes
4. Since this is a new project, we don't need to maintain backward compatibility
5. We should investigate the difference between `check_download_status` and `get_model_files_download_status` to potentially consolidate functionality
