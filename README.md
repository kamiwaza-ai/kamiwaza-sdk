# Kamiwaza Client Python SDK

The Kamiwaza Client Python SDK provides a convenient and intuitive interface for interacting with the Kamiwaza platform's APIs. This SDK simplifies the process of integrating Kamiwaza's powerful features into your Python applications, enabling you to focus on building innovative solutions without worrying about the underlying API details.

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Services](#services)
  - [Activity Service](#activity-service)
  - [Authentication Service](#authentication-service)
  - [Catalog Service](#catalog-service)
  - [Cluster Service](#cluster-service)
  - [Embedding Service](#embedding-service)
  - [Lab Service](#lab-service)
  - [Model Service](#model-service)
  - [Prompts Service](#prompts-service)
  - [Serving Service](#serving-service)
  - [VectorDB Service](#vectordb-service)
- [Examples](#examples)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Comprehensive API Coverage**: Access all Kamiwaza platform features through an easy-to-use Python interface.
- **Modular Design**: Each service corresponds to a specific domain, making it easy to navigate and utilize.
- **Error Handling**: Built-in exceptions for API errors and authentication issues.
- **Session Management**: Efficiently manage API sessions with persistent connections.
- **Data Models**: Utilize Pydantic models for data validation and serialization.

## Installation

Install the SDK using `pip`:

```bash
pip install kamiwaza-client
```

> **Note**: Replace `kamiwaza-client` with the actual package name once it's published to PyPI.

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

## Services

The Kamiwaza Client SDK provides the following services:

### Activity Service

Manage and retrieve activity logs.

**Methods:**

- `get_recent_activity()`: Get recent activities.

### Authentication Service

Handle user authentication, user management, and permissions.

**Methods:**

- `login_for_access_token(username, password)`: Obtain an access token.
- `verify_token(authorization)`: Verify the validity of a token.
- `create_local_user(user)`: Create a new local user.
- `list_users()`: Retrieve a list of all users.
- `read_users_me(authorization)`: Get information about the current user.
- `login_local(username, password)`: Log in with local credentials.
- `read_user(user_id)`: Retrieve a specific user's details.
- `update_user(user_id, user)`: Update user information.
- `delete_user(user_id)`: Delete a user.
- `read_own_permissions(token)`: Get the permissions of the current user.

### Catalog Service

Interact with the data catalog, manage datasets and containers.

**Methods:**

- `list_datasets()`: List all datasets.
- `create_dataset(dataset)`: Create a new dataset.
- `list_containers()`: List all containers.
- `get_dataset(datasetname)`: Retrieve a dataset by name.
- `ingest_by_path(path, dataset_urn, platform, env, location, recursive, secrets)`: Ingest data by path.
- `secret_exists(secret_name)`: Check if a secret exists.
- `create_secret(secret_name, secret_value, clobber)`: Create a new secret.

### Cluster Service

Manage clusters, nodes, hardware, and runtime configurations.

**Methods:**

- `create_location(location)`: Create a new location.
- `update_location(location_id, location)`: Update an existing location.
- `get_location(location_id)`: Retrieve a specific location.
- `list_locations(skip, limit)`: List all locations.
- `create_cluster(cluster)`: Create a new cluster.
- `get_cluster(cluster_id)`: Retrieve a specific cluster.
- `list_clusters(skip, limit)`: List all clusters.
- `get_node_by_id(node_id)`: Get details of a specific node.
- `get_running_nodes()`: Get a list of currently running nodes.
- `list_nodes(skip, limit, active)`: List all nodes.
- `create_hardware(hardware)`: Create a new hardware entry.
- `get_hardware(hardware_id)`: Retrieve a specific hardware entry.
- `list_hardware(skip, limit)`: List all hardware entries.
- `get_runtime_config()`: Retrieve the runtime configuration of the cluster.
- `get_hostname()`: Get the cluster's hostname.

### Embedding Service

Create and manage text embeddings.

**Methods:**

- `create_embedding(text, model, max_length, overlap, preamble_text)`: Create an embedding for text.
- `get_embedding(text)`: Get an embedding for text.
- `chunk_text(text, max_length, overlap, preamble_text)`: Chunk text into smaller pieces.
- `embed_chunks(text_chunks, batch_size)`: Generate embeddings for text chunks.
- `reset_model()`: Reset the embedding model.
- `call(batch, model_name)`: Generate embeddings for a batch of inputs.

### Lab Service

Manage lab environments.

**Methods:**

- `list_labs()`: List all labs.
- `create_lab(username, resources)`: Create a new lab environment.
- `get_lab(lab_id)`: Get details of a specific lab.
- `delete_lab(lab_id)`: Delete a lab.

### Model Service

Handle operations related to machine learning models.

**Methods:**

- `get_model(model_id)`: Retrieve a model by ID.
- `create_model(model)`: Create a new model.
- `delete_model(model_id)`: Delete a model.
- `list_models(load_files)`: List all models.
- `search_models(search_request)`: Search for models.
- `download_model(download_request)`: Download model files.
- `get_model_memory_usage(model_id)`: Get a model's memory usage.

**Model File Operations:**

- `delete_model_file(model_file_id)`: Delete a model file.
- `get_model_file(model_file_id)`: Retrieve a model file.
- `list_model_files()`: List all model files.
- `create_model_file(model_file)`: Create a new model file.
- `search_hub_model_files(search_request)`: Search model files in a hub.
- `get_model_file_memory_usage(model_file_id)`: Get memory usage of a model file.
- `get_model_files_download_status(model_ids)`: Get download status of model files.

**Model Configuration Operations:**

- `create_model_config(config)`: Create a new model configuration.
- `get_model_configs(model_id)`: Get configurations for a model.
- `get_model_configs_for_model(model_id, default)`: Get configurations for a model.
- `get_model_config(model_config_id)`: Get a model configuration.
- `delete_model_config(model_config_id)`: Delete a model configuration.
- `update_model_config(model_config_id, config)`: Update a model configuration.

### Prompts Service

Manage prompts, roles, systems, elements, and templates.

**Methods:**

- `create_role(role)`: Create a new role.
- `list_roles(skip, limit)`: List all roles.
- `get_role(role_id)`: Get a role by ID.
- `create_system(system)`: Create a new system.
- `list_systems(skip, limit)`: List all systems.
- `get_system(system_id)`: Get a system by ID.
- `create_element(element)`: Create a new element.
- `list_elements(skip, limit)`: List all elements.
- `get_element(element_id)`: Get an element by ID.
- `create_template(template)`: Create a new template.
- `list_templates(skip, limit)`: List all templates.
- `get_template(template_id)`: Get a template by ID.

### Serving Service

Deploy and manage machine learning models for serving.

**Methods:**

- `start_ray(address, runtime_env, options)`: Start the Ray server.
- `get_status()`: Get the status of the Ray server.
- `estimate_model_vram(deployment_request)`: Estimate VRAM required for deployment.
- `deploy_model(deployment_request)`: Deploy a model.
- `list_deployments(model_id)`: List all deployments.
- `get_deployment(deployment_id)`: Get deployment details.
- `stop_deployment(deployment_id, force)`: Stop a deployment.
- `get_deployment_status(deployment_id)`: Get the status of a deployment.
- `list_model_instances(deployment_id)`: List all model instances.
- `get_model_instance(instance_id)`: Get a model instance.
- `get_health()`: Get health status of all deployments.
- `unload_model(request)`: Unload a model.
- `load_model(request)`: Load a model.
- `simple_generate(request)`: Generate a simple response.
- `generate(request)`: Generate a response based on conversation history.

### VectorDB Service

Interact with vector databases for similarity search and embeddings.

**Methods:**

- `create_vectordb(vectordb_data)`: Create a new vector database instance.
- `get_vectordbs(engine)`: List all vector databases.
- `get_vectordb(vectordb_id)`: Get a vector database by ID.
- `remove_vectordb(vectordb)`: Remove a vector database.

## Examples

### Authenticating and Listing Models

```python
from kamiwaza_client import KamiwazaClient
from kamiwaza_client.schemas.auth import LocalUserCreate

# Initialize the client with base URL and API key
client = KamiwazaClient(base_url="https://api.kamiwaza.io", api_key="your_api_key")

# Authenticate and get a token
token = client.auth.login_for_access_token(username="user", password="pass")

# Update the client's session with the new token
client.session.headers.update({'Authorization': f'Bearer {token.access_token}'})

# List all models
models = client.models.list_models()
for model in models:
    print(f"Model ID: {model.id}, Model Name: {model.name}")
```

### Deploying a Model

```python
from kamiwaza_client import KamiwazaClient
from kamiwaza_client.schemas.serving.serving import CreateModelDeployment

client = KamiwazaClient(base_url="https://api.kamiwaza.io", api_key="your_api_key")

# Define the deployment request
deployment_request = CreateModelDeployment(
    model_id="model-uuid",
    deployment_name="my_model_deployment",
    config={
        # Configuration details
    }
)

# Deploy the model
deployment_id = client.serving.deploy_model(deployment_request)
print(f"Deployment ID: {deployment_id}")
```

### Creating and Searching in VectorDB

```python
from kamiwaza_client import KamiwazaClient
from kamiwaza_client.schemas.vectordb import CreateVectorDB

client = KamiwazaClient(base_url="https://api.kamiwaza.io", api_key="your_api_key")

# Create a new VectorDB instance
vectordb_data = CreateVectorDB(
    name="my_vectordb",
    engine="faiss",
    dimension=512
)
vectordb = client.vectordb.create_vectordb(vectordb_data)
print(f"VectorDB ID: {vectordb.id}")

# List all VectorDB instances
vectordbs = client.vectordb.get_vectordbs()
for db in vectordbs:
    print(f"VectorDB Name: {db.name}, Engine: {db.engine}")
```

## Contributing

We welcome contributions to improve the Kamiwaza Client Python SDK. Please follow these steps:

1. Fork the repository.
2. Create a new branch for your feature or bug fix.
3. Write tests for your changes.
4. Ensure all tests pass.
5. Submit a pull request with a detailed description of your changes.

## License

The Kamiwaza Client Python SDK is released under the [MIT License](LICENSE).

---

*Disclaimer: This SDK and the examples provided are for illustrative purposes. Replace placeholders like `your_api_key`, `https://api.kamiwaza.io`, and model or deployment IDs with actual values from your Kamiwaza environment.*