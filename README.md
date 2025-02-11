# Kamiwaza Python SDK

Python client library for interacting with the Kamiwaza AI Platform. This SDK provides a type-safe interface to all Kamiwaza API endpoints with built-in authentication, error handling, and resource management.

## Installation

```bash
pip install kamiwaza-client
```

## Quick Start

```python
from kamiwaza_client import KamiwazaClient

# Initialize the client for local development
client = KamiwazaClient("http://localhost:7777/api/")
```

## Examples

The `/examples` directory contains Jupyter notebooks demonstrating various use cases:

1. [Model Download and Deployment](examples/quickstart.ipynb) - Shows how to download and deploy a model using the SDK
2. [Structured Data Extraction](examples/structured_data_extraction.ipynb) - Demonstrates how to use deployed models for data processing and standardization

More examples coming soon!

## Service Overview

| Service | Description | Documentation |
|---------|-------------|---------------|
| `client.models` | Model management | [Models Service](docs/services/models/README.md) |
| `client.serving` | Model deployment | [Serving Service](docs/services/serving/README.md) |
| `client.vectordb` | Vector database | [VectorDB Service](docs/services/vectordb/README.md) |
| `client.catalog` | Data management | [Catalog Service](docs/services/catalog/README.md) |
| `client.embedding` | Text processing | [Embedding Service](docs/services/embedding/README.md) |
| `client.retrieval` | Search | [Retrieval Service](docs/services/retrieval/README.md) |
| `client.ingestion` | Data pipeline | [Ingestion Service](docs/services/ingestion/README.md) |
| `client.cluster` | Infrastructure | [Cluster Service](docs/services/cluster/README.md) |
| `client.lab` | Lab environments | [Lab Service](docs/services/lab/README.md) |
| `client.auth` | Security | [Auth Service](docs/services/auth/README.md) |
| `client.activity` | Monitoring | [Activity Service](docs/services/activity/README.md) |
| `client.openai` | OpenAI API compatible| [OpenAI Service](docs/services/openai/README.md) |

## Batch Operations

Many services support batch operations for better performance:
```python
# Batch embedding
chunks = embedder.chunk_text(text, max_length=500)
embeddings = embedder.embed_chunks(chunks, batch_size=32)

# Batch vector insertion
client.vectordb.insert(vectors, metadata, batch_size=1000)
```

---

The Kamiwaza SDK is actively being developed with new features, examples, and documentation being added regularly. Stay tuned for updates including additional example notebooks, enhanced documentation, and expanded functionality across all services. For the latest updates and feature releases, keep an eye on this repository.