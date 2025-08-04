# App Garden Service

The App Garden service allows you to deploy and manage containerized applications within the Kamiwaza platform. It provides a simple interface for deploying web applications, databases, and other services using Docker containers.

## Overview

The App Garden is designed to make it easy to deploy applications alongside your AI workloads. It supports:
- Docker-based container deployment
- Pre-built application templates
- Environment variable configuration
- Automatic port allocation and routing
- Instance scaling and management

## Authentication

**Note**: App Garden endpoints do not require authentication, making them accessible for quick deployments and testing.

## Quick Start

```python
from kamiwaza_client import KamiwazaClient

# Initialize client
client = KamiwazaClient("http://localhost:7777/api/")

# List available templates
templates = client.apps.list_templates()
for template in templates:
    print(f"{template.name} - {template.description}")

# Deploy an application from a template
deployment = client.apps.deploy(
    template_id=template.id,
    name="my-postgres-db",
    env_vars={"POSTGRES_PASSWORD": "mysecretpassword"}
)

print(f"Deployed: {deployment.name}")
print(f"Status: {deployment.status}")
```

## Available Methods

### Deployment Management

#### `deploy(template_id, name, **kwargs)`
Deploy a new application from a template.

**Parameters:**
- `template_id` (UUID): The ID of the template to deploy
- `name` (str): Name for your deployment
- `min_copies` (int, optional): Minimum instances to maintain (default: 1)
- `starting_copies` (int, optional): Initial number of instances (default: 1)
- `max_copies` (int, optional): Maximum instances allowed
- `env_vars` (dict, optional): Environment variables for the container

**Returns:** `AppDeployment` object with deployment details

#### `list_deployments()`
List all active application deployments.

**Returns:** List of `AppDeployment` objects

#### `get_deployment(deployment_id)`
Get details of a specific deployment.

**Parameters:**
- `deployment_id` (UUID): The deployment ID

**Returns:** `AppDeployment` object

#### `stop_deployment(deployment_id)`
Stop and remove a deployment.

**Parameters:**
- `deployment_id` (UUID): The deployment ID

**Returns:** Success message

### Template Management

#### `list_templates()`
List all available application templates.

**Returns:** List of `AppTemplate` objects

#### `search_templates(query)`
Search for templates by name or description.

**Parameters:**
- `query` (str): Search term

**Returns:** List of matching `AppTemplate` objects

### Instance Management

#### `list_instances(deployment_id)`
List all running instances of a deployment.

**Parameters:**
- `deployment_id` (UUID): The deployment ID

**Returns:** List of `AppInstance` objects with host and port information

### Image Management

#### `pull_images(deployment_id)`
Pull Docker images for a deployment (useful for pre-loading).

**Parameters:**
- `deployment_id` (UUID): The deployment ID

**Returns:** `ImagePullResult` with status information

## Common Use Cases

### Deploy a PostgreSQL Database

```python
# Find PostgreSQL template
templates = client.apps.search_templates("postgres")
postgres_template = templates[0]

# Deploy with custom settings
deployment = client.apps.deploy(
    template_id=postgres_template.id,
    name="my-database",
    env_vars={
        "POSTGRES_PASSWORD": "secure_password",
        "POSTGRES_DB": "myapp"
    }
)

# Get connection details
instances = client.apps.list_instances(deployment.id)
for instance in instances:
    print(f"Connect to: {instance.host_name}:{instance.listen_port}")
```

### Deploy a Web Application

```python
# Deploy a web app with scaling
deployment = client.apps.deploy(
    template_id=web_template.id,
    name="my-web-app",
    min_copies=2,
    max_copies=5,
    env_vars={
        "API_KEY": "your_api_key",
        "DEBUG": "false"
    }
)
```

### Monitor Deployments

```python
# List all deployments
deployments = client.apps.list_deployments()

for deployment in deployments:
    print(f"\n{deployment.name}:")
    print(f"  Status: {deployment.status}")
    print(f"  Created: {deployment.created_at}")
    
    # Check instances
    instances = client.apps.list_instances(deployment.id)
    print(f"  Running instances: {len(instances)}")
```

## Templates

Templates define the container configuration and requirements for applications. Each template includes:
- Docker image reference
- Default environment variables
- Port configurations
- Resource requirements
- Risk tier classification

Templates are verified by the Kamiwaza team and categorized by risk level for security.

## Error Handling

```python
try:
    deployment = client.apps.deploy(
        template_id=template_id,
        name="my-app"
    )
except Exception as e:
    print(f"Deployment failed: {e}")
```

## Best Practices

1. **Use Templates**: Prefer templates over custom images for better security and reliability
2. **Environment Variables**: Store sensitive data like passwords in environment variables
3. **Scaling**: Set appropriate min/max copies based on your load requirements
4. **Monitoring**: Regularly check deployment status and instance health
5. **Cleanup**: Stop unused deployments to free resources

## See Also

- [Example Notebook](../../../examples/08_app_garden_and_tools.ipynb) - Complete examples of App Garden usage
- [Tool Service](../tools/README.md) - Deploy MCP Tool servers