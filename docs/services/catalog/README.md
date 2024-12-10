# Catalog Service

## Overview
The Catalog Service (`CatalogService`) provides comprehensive dataset and container management for the Kamiwaza AI Platform. Located in `kamiwaza_client/services/catalog.py`, this service handles dataset operations, container management, and secret handling for secure data access.

## Key Features
- Dataset Management
- Container Organization
- Secret Management
- Data Ingestion
- Catalog Maintenance

## Dataset Management

### Available Methods
- `list_datasets() -> List[Dataset]`: List all datasets
- `create_dataset(dataset: CreateDataset) -> Dataset`: Create new dataset
- `get_dataset(dataset_id: UUID) -> Dataset`: Get dataset info
- `ingest_by_path(path: str, **kwargs) -> IngestionResponse`: Ingest dataset by path

```python
# List all datasets
datasets = client.catalog.list_datasets()
for dataset in datasets:
    print(f"Dataset: {dataset.name}")
    print(f"Description: {dataset.description}")

# Create new dataset
dataset = client.catalog.create_dataset(CreateDataset(
    name="training-data",
    description="Training dataset for model XYZ",
    metadata={
        "source": "internal",
        "version": "1.0"
    }
))

# Get dataset details
dataset = client.catalog.get_dataset(dataset_id)
print(f"Status: {dataset.status}")
print(f"Size: {dataset.size}")

# Ingest dataset from path
response = client.catalog.ingest_by_path(
    path="/data/training",
    recursive=True,
    file_pattern="*.csv"
)
```

## Container Management

### Available Methods
- `list_containers() -> List[Container]`: List all containers

```python
# List containers
containers = client.catalog.list_containers()
for container in containers:
    print(f"Container: {container.name}")
    print(f"Type: {container.type}")
```

## Secret Management

### Available Methods
- `secret_exists(name: str) -> bool`: Check secret existence
- `create_secret(secret: CreateSecret) -> Secret`: Create new secret
- `flush_catalog() -> None`: Clear catalog data

```python
# Check if secret exists
if client.catalog.secret_exists("api-key"):
    print("Secret exists")

# Create new secret
secret = client.catalog.create_secret(CreateSecret(
    name="database-credentials",
    value="secret-value",
    metadata={
        "type": "database",
        "environment": "production"
    }
))

# Clear catalog data
client.catalog.flush_catalog()
```

## Integration with Other Services
The Catalog Service works in conjunction with:
1. Ingestion Service
   - For dataset processing
2. Authentication Service
   - For access control
3. VectorDB Service
   - For vector storage
4. Retrieval Service
   - For data access

## Error Handling
The service includes built-in error handling for common scenarios:
```python
try:
    dataset = client.catalog.create_dataset(dataset_config)
except DatasetExistsError:
    print("Dataset already exists")
except StorageError:
    print("Storage operation failed")
except APIError as e:
    print(f"Operation failed: {e}")
```

## Best Practices
1. Use meaningful dataset names
2. Include comprehensive metadata
3. Implement proper error handling
4. Regular catalog maintenance
5. Secure secret management
6. Monitor storage usage
7. Document dataset lineage
8. Validate data before ingestion

## Performance Considerations
- Dataset size impacts ingestion time
- Container organization affects retrieval speed
- Secret management overhead
- Storage capacity requirements
- Catalog operation latency
