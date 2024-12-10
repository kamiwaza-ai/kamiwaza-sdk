# Model Service

## Overview
The Model Service (`ModelService`) provides comprehensive model management functionality for the Kamiwaza AI Platform. Located in `kamiwaza_client/services/models.py`, this service handles model lifecycle management, including creation, deployment, file management, and configuration.

## Key Features
- Model Management (CRUD operations)
- Model File Management
- Model Search and Discovery
- Model Download Management
- Model Configuration Management
- Memory Usage Tracking

## Model Management

### Basic Operations
```python
# Get a specific model
model = client.models.get_model(model_id)

# Create a new model
model = client.models.create_model(CreateModel(
    name="my-model",
    description="My custom model"
))

# List all models
models = client.models.list_models(load_files=True)

# Delete a model
client.models.delete_model(model_id)
```

### Model Search
```python
# Search for models
models = client.models.search_models(
    query="bert",
    exact=False,
    limit=100,
    hubs_to_search=["huggingface"]
)

# Get model by repo ID
model = client.models.get_model_by_repo_id("bert-base-uncased")
```

## Model Download Management

### Available Methods
- `initiate_model_download(repo_id: str, quantization: str = 'q6_k') -> Dict[str, Any]`: Start model download
- `check_download_status(repo_id: str) -> List[ModelDownloadStatus]`: Check download progress
- `get_model_files_download_status(repo_model_id: str) -> List[ModelDownloadStatus]`: Get detailed file status

```python
# Download a model
download_info = client.models.initiate_model_download(
    repo_id="llama2-7b",
    quantization="q6_k"
)

# Check download status
status = client.models.check_download_status("llama2-7b")
```

## Model File Management

### Available Methods
- `delete_model_file(model_file_id: UUID) -> dict`: Delete a model file
- `get_model_file(model_file_id: UUID) -> ModelFile`: Get file details
- `get_model_files_by_model_id(model_id: UUID) -> List[ModelFile]`: Get all files for a model
- `list_model_files() -> List[ModelFile]`: List all model files
- `create_model_file(model_file: CreateModelFile) -> ModelFile`: Create new model file
- `search_hub_model_files(search_request: HubModelFileSearch) -> List[ModelFile]`: Search hub files
- `get_model_file_memory_usage(model_file_id: UUID) -> int`: Get file memory usage

```python
# List model files
files = client.models.list_model_files()

# Get files for specific model
model_files = client.models.get_model_files_by_model_id(model_id)

# Search hub files
files = client.models.search_hub_model_files(HubModelFileSearch(
    hub="huggingface",
    model="bert-base-uncased"
))
```

## Model Configuration Management

### Available Methods
- `create_model_config(config: CreateModelConfig) -> ModelConfig`: Create new config
- `get_model_configs(model_id: UUID) -> List[ModelConfig]`: Get all configs
- `get_model_configs_for_model(model_id: UUID, default: bool = False) -> List[ModelConfig]`: Get model configs

```python
# Create model configuration
config = client.models.create_model_config(CreateModelConfig(
    model_id=model_id,
    parameters={"temperature": 0.7}
))

# Get configurations for model
configs = client.models.get_model_configs(model_id)
```

## Memory Usage Tracking

### Available Methods
- `get_model_memory_usage(model_id: UUID) -> int`: Get model memory usage
- `get_model_file_memory_usage(model_file_id: UUID) -> int`: Get file memory usage

```python
# Check model memory usage
memory_usage = client.models.get_model_memory_usage(model_id)
```

## Error Handling
The service includes built-in error handling for common scenarios:
```python
try:
    model = client.models.get_model(model_id)
except APIError as e:
    print(f"Operation failed: {e}")
```

## Best Practices
1. Always check model compatibility before downloading
2. Monitor download status for large models
3. Use appropriate quantization for your use case
4. Clean up unused model files to manage storage
5. Keep track of model configurations
6. Monitor memory usage for large models
