# Tool Shed Service

The Tool Shed service enables deployment and management of MCP (Model Context Protocol) servers that provide tools and capabilities to AI assistants. These Tool servers can integrate with external services, APIs, and systems to extend AI functionality.

## Overview

The Tool Shed allows you to:
- Deploy MCP-compatible Tool servers
- Manage Tool server lifecycle
- Discover available tools and their capabilities
- Monitor Tool server health
- Use pre-built Tool templates for common integrations

## Authentication

**Important**: Tool Shed endpoints require authentication. You must provide valid credentials to access these services.

```python
from kamiwaza_sdk import kamiwaza_sdk as kz
from kamiwaza_sdk.authentication import UserPasswordAuthenticator

# Create authenticated client
client = kz("http://localhost:7777/api/")
authenticator = UserPasswordAuthenticator(
    username="your_username",
    password="your_password",
    auth_service=client.auth
)
client = kz(
    "http://localhost:7777/api/",
    authenticator=authenticator
)
```

## Quick Start

```python
# List available Tool templates
templates = client.tools.list_available_templates()
for template in templates:
    print(f"{template['name']} - {template['description']}")

# Deploy a Tool server from template
tool = client.tools.deploy_from_template(
    template_name="tool-websearch",
    name="my-search-tool",
    env_vars={"TAVILY_API_KEY": "your_api_key"}
)

print(f"Tool deployed at: {tool.url}")
print(f"Status: {tool.status}")
```

## Available Methods

### Deployment Management

#### `deploy(image, name, port, env_vars=None)`
Deploy a custom Tool server from a Docker image.

**Parameters:**
- `image` (str): Docker image for the Tool server
- `name` (str): Name for your deployment
- `port` (int): Port the Tool server listens on
- `env_vars` (dict, optional): Environment variables

**Returns:** `ToolDeployment` object

#### `deploy_from_template(template_name, name, env_vars=None)`
Deploy a Tool server from a pre-built template.

**Parameters:**
- `template_name` (str): Name of the template (e.g., "tool-websearch")
- `name` (str): Name for your deployment instance
- `env_vars` (dict, optional): Environment variables (e.g., API keys)

**Returns:** `ToolDeployment` object

#### `list_deployments()`
List all active Tool server deployments.

**Returns:** List of `ToolDeployment` objects

#### `get_deployment(deployment_id)`
Get details of a specific Tool deployment.

**Parameters:**
- `deployment_id` (UUID): The deployment ID

**Returns:** `ToolDeployment` object

#### `stop_deployment(deployment_id)`
Stop and remove a Tool server deployment.

**Parameters:**
- `deployment_id` (UUID): The deployment ID

**Returns:** Success message

### Discovery and Health

#### `discover_servers()`
Discover all Tool servers and their capabilities.

**Returns:** `ToolDiscovery` object containing:
- `total`: Total number of servers
- `servers`: List of servers with their capabilities

#### `check_health(deployment_id)`
Check the health status of a Tool server.

**Parameters:**
- `deployment_id` (UUID): The deployment ID

**Returns:** `ToolHealthCheck` object with status and protocol information

### Template Management

#### `list_available_templates()`
List all available Tool server templates.

**Returns:** List of template dictionaries with:
- `name`: Template identifier
- `description`: What the Tool does
- `category`: Tool category
- `capabilities`: List of capabilities
- `required_env_vars`: Required environment variables

## Common Use Cases

### Deploy a Web Search Tool

```python
# Deploy Tavily search Tool
tool = client.tools.deploy_from_template(
    template_name="tool-websearch",
    name="search-assistant",
    env_vars={
        "TAVILY_API_KEY": "your_tavily_api_key"
    }
)

print(f"Search Tool available at: {tool.url}")
```

### Deploy a Database Query Tool

```python
# Deploy PostgreSQL Tool
tool = client.tools.deploy_from_template(
    template_name="tool-postgres",
    name="db-assistant",
    env_vars={
        "DATABASE_URL": "postgresql://user:pass@host:5432/db"
    }
)
```

### Discover Tool Capabilities

```python
# Discover all available tools
discovery = client.tools.discover_servers()

print(f"Found {discovery.total} Tool servers:")
for server in discovery.servers:
    print(f"\n{server.name} ({server.status})")
    if server.capabilities:
        print("Capabilities:")
        for cap in server.capabilities:
            print(f"  - {cap.name}: {cap.description}")
```

### Monitor Tool Health

```python
# Check health of all deployments
deployments = client.tools.list_deployments()

for deployment in deployments:
    try:
        health = client.tools.check_health(deployment.id)
        print(f"{deployment.name}: {health.status}")
    except Exception as e:
        print(f"{deployment.name}: Error - {e}")
```

## Tool Templates

Available Tool templates include:

- **tool-websearch**: Web search using Tavily API
- **tool-postgres**: PostgreSQL database queries
- **tool-filesystem**: File system operations
- **tool-github**: GitHub repository interaction
- **tool-slack**: Slack messaging integration

Each template requires specific environment variables (like API keys) which are documented in the template details.

## Using Tool URLs

Once deployed, Tool servers provide MCP-compatible endpoints that can be used with:
- AI assistants that support MCP
- Custom integrations
- Direct API calls

The `tool.url` returned after deployment is the public HTTPS endpoint for your Tool server.

## Error Handling

```python
from kamiwaza_sdk.exceptions import AuthenticationError, NotFoundError

try:
    tool = client.tools.deploy_from_template(
        template_name="tool-websearch",
        name="my-tool"
    )
except AuthenticationError:
    print("Authentication failed. Check your credentials.")
except NotFoundError:
    print("Template not found.")
except Exception as e:
    print(f"Deployment failed: {e}")
```

## Best Practices

1. **Secure Credentials**: Store API keys and secrets in environment variables
2. **Health Monitoring**: Regularly check Tool server health
3. **Capability Discovery**: Use discovery to understand what tools can do
4. **Resource Cleanup**: Stop unused Tool servers to free resources
5. **Template Usage**: Use verified templates for better security

## MCP Protocol

Tool servers implement the Model Context Protocol (MCP), which standardizes how AI assistants interact with external tools. The protocol defines:
- Tool discovery and capability reporting
- Input/output schemas
- Authentication methods
- Error handling

## See Also

- [Example Notebook](../../../examples/08_app_garden_and_tools.ipynb) - Complete examples of Tool Shed usage
- [App Service](../apps/README.md) - Deploy containerized applications
- [Authentication Guide](../auth/README.md) - Setting up authentication