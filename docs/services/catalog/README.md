# Catalog Service

## Overview
The Catalog service exposes three focused clients that mirror the server-side routers:

| Attribute | Description |
|-----------|-------------|
| `client.catalog.datasets` | Dataset CRUD, schema management, and URN helpers |
| `client.catalog.containers` | Container lifecycle and dataset membership |
| `client.catalog.secrets` | Secret registration/listing tied to corpuser owners |

Each client accepts/returns the Pydantic models defined in `kamiwaza_sdk/schemas/catalog.py`, so responses are thread-safe and easy to validate.

## Datasets

```python
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.catalog import DatasetCreate, DatasetUpdate

client = KamiwazaClient("https://localhost/api", api_key="...shh...")

# Create and immediately fetch the dataset metadata
payload = DatasetCreate(
    name="/var/data/visitors.parquet",
    platform="s3",
    environment="PROD",
    description="Daily visitor export",
    properties={"region": "us-east-1", "endpoint": "http://localhost:9100"},
    tags=["pii", "bronze"],
)
dataset_urn = client.catalog.datasets.create(payload)
dataset = client.catalog.datasets.get(dataset_urn)

# Update tags/properties via URN
client.catalog.datasets.update(
    dataset_urn,
    DatasetUpdate(tags=["pii", "silver"], properties={"region": "us-east-1"}),
)

# Fetch or update the logical schema
schema = client.catalog.datasets.get_schema(dataset_urn)
client.catalog.datasets.update_schema(dataset_urn, schema)

# Convenience wrappers for legacy code that previously called
# `client.catalog.create_dataset(...)` still work and delegate to the dataset client.
```

## Containers

```python
from kamiwaza_sdk.schemas.catalog import ContainerCreate, ContainerUpdate

containers = client.catalog.containers
container_urn = containers.create(ContainerCreate(name="rag-demo", platform="kamiwaza"))
containers.add_dataset(container_urn, dataset_urn)

# Update metadata or re-parent the container
containers.update(container_urn, ContainerUpdate(description="Demo assets"))

# Remove membership or delete the container
containers.remove_dataset(container_urn, dataset_urn)
containers.delete(container_urn)
```

All URNs can be percent-encoded for the `/v2/{urn}` endpoints with `client.catalog.encode_urn(...)` (or `DatasetClient.encode_path_urn` / `ContainerClient.encode_path_urn`).

## Secrets

```python
from kamiwaza_sdk.schemas.catalog import SecretCreate

secrets = client.catalog.secrets
secret_urn = secrets.create(
    SecretCreate(
        name="minio/rag-demo",
        value="{'aws_access_key_id':'minioadmin','aws_secret_access_key':'minioadmin'}",
        owner="urn:li:corpuser:demo",
        description="Demo MinIO credentials",
    ),
    clobber=True,  # overwrite existing entry if present
)

secret = secrets.get(secret_urn)
for visible in secrets.list():
    print(visible.urn, visible.owner)

secrets.delete(secret_urn)
```

## Diagnostics

Both the catalog and auth services now expose lightweight health endpoints to simplify readiness checks:

```python
client.auth.health()        # -> {"status": "ok", ...}
client.auth.metadata()      # -> {"version": "...", ...}
client.catalog.health()     # -> {"status": "ok"}
client.catalog.metadata()   # -> {... service banner ...}
```

These helpers pair with the improved `VectorDBUnavailableError` so SDK consumers can reason about platform readiness before kicking off ingestion or retrieval jobs.
