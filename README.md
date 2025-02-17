# Kamiwaza Python SDK

Python client library and CLI for interacting with the Kamiwaza AI Platform. This SDK provides a type-safe interface to all Kamiwaza API endpoints with built-in authentication, error handling, and resource management.

## Installation

```bash
pip install kamiwaza-client
```

## Python SDK Usage

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

---

## Quick Start - CLI

The easiest way to get started is using the CLI:

```bash
# First time setup
kamiwaza config set-url http://localhost:7777/api

# Download, deploy, and chat with a model in one command
$ kamiwaza run qwen2.5-7b-instruct
🚀 Deploying Qwen2.5-7B-Instruct-GGUF...
✨ Deployment ready!

🤖 Chat session started (Ctrl+C to exit)

User: What is a funny way to explain GenAI?

Assistant: GenAI is like a magic wand that can create amazing things. It can write code, draw pictures, and even figure out your taxes.
```

For more CLI commands and examples, see the [CLI Documentation](docs/cli.md).

The Kamiwaza SDK is actively being developed with new features, examples, and documentation being added regularly. Stay tuned for updates including additional example notebooks, enhanced documentation, and expanded functionality across all services.