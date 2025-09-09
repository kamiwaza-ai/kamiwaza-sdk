# Kamiwaza Python SDK

Python client library for interacting with the Kamiwaza AI Platform. This SDK provides a type-safe interface to all Kamiwaza API endpoints with built-in authentication, error handling, and resource management.

## Installation

```bash
pip install kamiwaza-sdk
```

> **Note:** This SDK (version 0.5.1+) is incompatible with Kamiwaza versions before 0.5.1. Please ensure you're using the latest version of Kamiwaza.

## Python SDK Usage

```python
from kamiwaza_client import KamiwazaClient

# Initialize the client for local development
client = KamiwazaClient("http://localhost:7777/api/")
```

## Examples

The `/examples` directory contains Jupyter notebooks demonstrating various use cases:

1. [Model Download and Deployment](examples/01_download_and_deploy.ipynb) - A comprehensive guide to searching, downloading, deploying, and using models with the Kamiwaza SDK
2. [Quick Model Deployment](examples/02_download_and_deploy_quick.ipynb) - Streamlined approach to download and deploy models using a single function
3. [Model Evaluation](examples/03_eval_multiple_models.ipynb) - How to evaluate and benchmark multiple language models for performance comparison using the streamlined `download_and_deploy_model` function
4. [Structured Output](examples/04_structured_output.ipynb) - Using Kamiwaza's OpenAI-compatible interface to generate structured outputs with specific JSON schemas
5. [Function Calling](examples/05_tools.ipynb) - Demonstrates how to use function calling (tools) with Kamiwaza's OpenAI-compatible API
6. [Web Agent](examples/06_web-agent.ipynb) - Build an AI agent that can browse and interact with web pages
7. [RAG Demo](examples/07_kamiwaza_rag_demo.ipynb) - Retrieval Augmented Generation using Kamiwaza's vector database and embedding services
8. [App Garden and Tool Shed](examples/08_app_garden_and_tools.ipynb) - Deploy containerized applications and MCP Tool servers

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
| `client.cluster` | Infrastructure | [Cluster Service](docs/services/cluster/README.md) |
| `client.lab` | Lab environments | [Lab Service](docs/services/lab/README.md) |
| `client.auth` | Security | [Auth Service](docs/services/auth/README.md) |
| `client.activity` | Monitoring | [Activity Service](docs/services/activity/README.md) |
| `client.openai` | OpenAI API compatible| [OpenAI Service](docs/services/openai/README.md) |
| `client.apps` | App deployment | [App Service](docs/services/apps/README.md) |
| `client.tools` | Tool servers (MCP) | [Tool Service](docs/services/tools/README.md) |


The Kamiwaza SDK is actively being developed with new features, examples, and documentation being added regularly. Stay tuned for updates including additional example notebooks, enhanced documentation, and expanded functionality across all services.
