# Model Service

The Model Service handles operations related to machine learning models.

## Methods

### get_model(model_id: UUID)

Retrieves a model by ID.

```python
model = client.models.get_model("model_id_here")
```

### create_model(model: CreateModel)

Creates a new model.

```python
new_model = CreateModel(name="My Model", version="1.0")
created_model = client.models.create_model(new_model)
```

### delete_model(model_id: UUID)

Deletes a model.

```python
client.models.delete_model("model_id_here")
```

### list_models(load_files: bool = False)

Lists all models.

```python
models = client.models.list_models(load_files=True)
```

### search_models(search_request: ModelSearchRequest)

Searches for models.

```python
search_request = ModelSearchRequest(query="language model", limit=10)
search_results = client.models.search_models(search_request)
```

### download_model(download_request: ModelDownloadRequest)

Downloads model files.

```python
download_request = ModelDownloadRequest(model="my_model", version="1.0")
client.models.download_model(download_request)
```

### get_model_memory_usage(model_id: UUID)

Gets a model's memory usage.

```python
memory_usage = client.models.get_model_memory_usage("model_id_here")
```

## Model File Operations

### delete_model_file(model_file_id: UUID)

Deletes a model file.

```python
client.models.delete_model_file("model_file_id_here")
```

### get_model_file(model_file_id: UUID)

Retrieves a model file.

```python
model_file = client.models.get_model_file("model_file_id_here")
```

### list_model_files()

Lists all model files.

```python
model_files = client.models.list_model_files()
```

### create_model_file(model_file: CreateModelFile)

Creates a new model file.

```python
new_model_file = CreateModelFile(name="weights.bin", size=1000000)
created_model_file = client.models.create_model_file(new_model_file)
```

### search_hub_model_files(search_request: HubModelFileSearch)

Searches model files in a hub.

```python
search_request = HubModelFileSearch(hub="my_hub", model="my_model")
search_results = client.models.search_hub_model_files(search_request)
```

### get_model_file_memory_usage(model_file_id: UUID)

Gets memory usage of a model file.

```python
memory_usage = client.models.get_model_file_memory_usage("model_file_id_here")
```

### get_model_files_download_status(model_ids: List[UUID])

Gets download status of model files.

```python
download_status = client.models.get_model_files_download_status(["model_id_1", "model_id_2"])
```

## Model Configuration Operations

### create_model_config(config: CreateModelConfig)

Creates a new model configuration.

```python
new_config = CreateModelConfig(m_id="model_id_here", name="Default Config", default=True)
created_config = client.models.create_model_config(new_config)
```

### get_model_configs(model_id: UUID)

Gets configurations for a model.

```python
configs = client.models.get_model_configs("model_id_here")
```

### get_model_configs_for_model(model_id: UUID, default: bool = None)

Gets configurations for a model, optionally filtering for default configurations.

```python
configs = client.models.get_model_configs_for_model("model_id_here", default=True)
```

### get_model_config(model_config_id: UUID)

Gets a model configuration.

```python
config = client.models.get_model_config("config_id_here")
```

### delete_model_config(model_config_id: UUID)

Deletes a model configuration.

```python
client.models.delete_model_config("config_id_here")
```

### update_model_config(model_config_id: UUID, config: CreateModelConfig)

Updates a model configuration.

```python
updated_config = CreateModelConfig(name="Updated Config", default=False)
client.models.update_model_config("config_id_here", updated_config)
```
