# Catalog Service

The Catalog Service allows you to interact with the data catalog, manage datasets and containers.

## Methods

### list_datasets()

Lists all datasets.

```python
datasets = client.catalog.list_datasets()
```

### create_dataset(dataset: Dataset)

Creates a new dataset.

```python
new_dataset = Dataset(name="my_dataset", paths=["/path/to/data"], platform="local")
created_dataset = client.catalog.create_dataset(new_dataset)
```

### list_containers()

Lists all containers.

```python
containers = client.catalog.list_containers()
```

### get_dataset(datasetname: str)

Retrieves a dataset by name.

```python
dataset = client.catalog.get_dataset("my_dataset")
```

### ingest_by_path(path: str, dataset_urn: str, platform: str, env: str, location: str, recursive: bool, secrets: Dict[str, str])

Ingests data by path.

```python
client.catalog.ingest_by_path(
    path="/path/to/data",
    dataset_urn="urn:my_dataset",
    platform="local",
    env="production",
    location="us-west",
    recursive=True,
    secrets={"api_key": "your_secret_key"}
)
```

### secret_exists(secret_name: str)

Checks if a secret exists.

```python
exists = client.catalog.secret_exists("my_secret")
```

### create_secret(secret_name: str, secret_value: str, clobber: bool)

Creates a new secret.

```python
client.catalog.create_secret("my_new_secret", "secret_value", clobber=False)
```
