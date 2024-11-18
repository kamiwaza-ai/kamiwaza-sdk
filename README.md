# Kamiwaza Python SDK

Python client library for interacting with the Kamiwaza AI Infrastructure Platform. This SDK provides a type-safe interface to all Kamiwaza API endpoints with built-in authentication, error handling, and resource management.

## Installation

```bash
pip install kamiwaza-client
```

## Quick Start

```python
from kamiwaza_client import KamiwazaClient

# Initialize with API key
client = KamiwazaClient(
    base_url="https://api.kamiwaza.ai",
    api_key="your-api-key"
)

# Or with username/password
from kamiwaza_client.authentication import UserPasswordAuthenticator

auth = UserPasswordAuthenticator("username", "password", auth_service)
client = KamiwazaClient(base_url="https://api.kamiwaza.ai", authenticator=auth)
```

## SDK Structure

The SDK is organized into service modules that map directly to API endpoints:

### Model Management
```python
# List available models
models = client.models.list_models()

# Deploy a model
deployment = client.serving.deploy_model(
    model_id="model-id",
    min_copies=1,
    max_copies=3
)
```

### Vector Operations
```python
# Store vectors
client.vectordb.insert(
    vectors=[[1.0, 2.0], [3.0, 4.0]],
    metadata=[{"source": "doc1"}, {"source": "doc2"}],
    collection_name="my_collection"
)

# Search vectors
results = client.vectordb.search(
    query_vector=[1.0, 2.0],
    collection_name="my_collection",
    limit=5
)
```

### Data Management
```python
# Create a dataset
dataset = client.catalog.create_dataset(
    dataset_name="/path/to/data",
    platform="local"
)

# Ingest data
client.ingestion.ingest(IngestionConfig(
    dataset_path="/path/to/data",
    collection_name="my_collection"
))

# Search data
chunks = client.retrieval.retrieve_relevant_chunks(
    RetrieveRelevantChunksRequest(
        collections=["my_collection"],
        query="search query"
    )
)
```

### Infrastructure Management
```python
# Create a lab environment
lab = client.lab.create_lab(
    username="user123",
    resources={"cpu": "2", "memory": "8Gi"}
)

# Manage cluster
location = client.cluster.create_location(CreateLocation(
    name="us-west",
    datacenter="dc1"
))
```

### Authentication & Users
```python
# Create user
user = client.auth.create_local_user(LocalUserCreate(
    username="newuser",
    email="user@example.com",
    password="securepass"
))

# Manage permissions
client.auth.add_user_to_group(user.id, group_id)
```

## Service Overview

| Service | Description | Key Operations |
|---------|-------------|----------------|
| `client.models` | Model management | Create, list, update, delete models and configurations |
| `client.serving` | Model deployment | Deploy models, manage instances, handle inference |
| `client.vectordb` | Vector database | Store and search vectors with metadata |
| `client.catalog` | Data management | Manage datasets and secrets |
| `client.embedding` | Text processing | Generate embeddings, chunk text |
| `client.retrieval` | Search | Retrieve relevant text chunks |
| `client.ingestion` | Data pipeline | Coordinate data ingestion workflows |
| `client.cluster` | Infrastructure | Manage locations, hardware, nodes |
| `client.lab` | Lab environments | Create and manage lab environments |
| `client.auth` | Security | Handle users, roles, permissions |
| `client.activity` | Monitoring | Track user actions and system events |

## Error Handling

The SDK provides specific error types:
```python
try:
    result = client.models.create_model(...)
except AuthenticationError:
    # Handle authentication failures
except APIError as e:
    # Handle API errors
    print(f"Operation failed: {e}")
```

## Batch Operations

Many services support batch operations for better performance:
```python
# Batch embedding
chunks = embedder.chunk_text(text, max_length=500)
embeddings = embedder.embed_chunks(chunks, batch_size=32)

# Batch vector insertion
client.vectordb.insert(vectors, metadata, batch_size=1000)
```

## Resource Management

Resources are automatically cleaned up:
```python
# Resources cleaned up when client is destroyed
client = KamiwazaClient(...)

# Explicit cleanup
client.auth.delete_user(user_id)
client.lab.delete_lab(lab_id)
```

The SDK maps closely to the Kamiwaza API while providing additional convenience methods, type safety, and resource management. Each service module corresponds to specific API endpoints and implements the full functionality available through those endpoints.