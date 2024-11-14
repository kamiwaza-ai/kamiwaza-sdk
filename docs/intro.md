# Kamiwaza Client Python SDK

The Kamiwaza Client Python SDK provides a convenient and intuitive interface for interacting with the Kamiwaza platform's APIs. This SDK simplifies the process of integrating Kamiwaza's powerful features into your Python applications, enabling you to focus on building innovative solutions without worrying about the underlying API details.

## Installation

Install the SDK using `pip`:

```bash
pip install kamiwaza-client
```

## Quick Start

Here's a quick example to get you started:

```python
from kamiwaza_client import KamiwazaClient

# Initialize the client
client = KamiwazaClient(base_url="https://api.kamiwaza.io", api_key="your_api_key")

# List all models
models = client.models.list_models()
for model in models:
    print(model.name)
```

## Available Services

The Kamiwaza Client SDK provides the following services:

1. [Activity Service](activity_service.md)
2. [Authentication Service](authentication_service.md)
3. [Catalog Service](catalog_service.md)
4. [Cluster Service](cluster_service.md)
5. [Embedding Service](embedding_service.md)
6. [Lab Service](lab_service.md)
7. [Model Service](model_service.md)
8. [Prompts Service](prompts_service.md)
9. [Serving Service](serving_service.md)
10. [VectorDB Service](vectordb_service.md)

Each service corresponds to a specific domain of the Kamiwaza platform, providing methods to interact with various features and functionalities.

For detailed information on each service, please refer to their respective documentation pages.
