# Extensions Service

The Extensions Service (`client.extensions`) manages K8s-native extensions deployed as `KamiwazaExtension` Custom Resources. It replaces the legacy App Garden (`client.apps`) and Tool Shed (`client.tools`) services for Kubernetes deployments.

## Quick Start

```python
from kamiwaza_sdk import KamiwazaClient

client = KamiwazaClient(base_url="https://kamiwaza.test/api")
# ... authenticate ...

# List all extensions
extensions = client.extensions.list_extensions()
for ext in extensions:
    print(f"{ext.name}: {ext.phase} ({ext.type})")

# Get a specific extension
detail = client.extensions.get_extension("my-extension")
print(detail.endpoints.external)

# Delete an extension
client.extensions.delete_extension("my-extension")
```

## Methods

### `list_extensions() -> List[Extension]`

List all extensions visible to the current user.

```python
extensions = client.extensions.list_extensions()
for ext in extensions:
    print(f"{ext.name} ({ext.type}): {ext.phase}")
```

### `get_extension(name: str) -> Extension`

Get a single extension by CR name.

```python
ext = client.extensions.get_extension("kaizen-abc12345")
print(ext.version)        # "2.0.0"
print(ext.phase)          # "Running"
print(ext.endpoints)      # ExtensionEndpoints(external="https://...", internal="http://...")
```

**Raises:** `NotFoundError` if the extension does not exist.

### `create_extension(request: CreateExtension) -> Extension`

Create a new extension from a specification.

```python
from kamiwaza_sdk.schemas.extensions import CreateExtension, ExtensionServiceSpec

request = CreateExtension(
    name="my-tool",
    type="tool",
    version="1.0.0",
    services=[
        ExtensionServiceSpec(
            name="backend",
            image="myregistry/backend:1.0.0",
            primary=True,
            ports=[{"container_port": 8000}],
        ),
    ],
)
ext = client.extensions.create_extension(request)
print(ext.name)   # "my-tool"
print(ext.phase)  # "Pending"
```

### `delete_extension(name: str) -> bool`

Delete an extension by CR name. Returns `True` on success.

```python
client.extensions.delete_extension("my-tool")
```

**Raises:** `NotFoundError` if the extension does not exist.

**Note:** K8s CR deletion is asynchronous. The CR may still appear briefly in `list_extensions()` after deletion.

## Schemas

### `Extension`

Response model for an extension.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Extension CR name |
| `type` | `str` | `"app"` or `"tool"` |
| `version` | `str` | Extension version |
| `phase` | `str \| None` | CR phase: `Pending`, `Deploying`, `Running`, `Failed` |
| `services` | `List[ExtensionServiceStatus]` | Per-service status |
| `endpoints` | `ExtensionEndpoints \| None` | Resolved external/internal URLs |
| `owner_user_id` | `str \| None` | User who created the extension |
| `created_at` | `datetime \| None` | Creation timestamp |

### `CreateExtension`

Request model for creating an extension.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `str` | Yes | Extension name (K8s DNS label format) |
| `type` | `"app" \| "tool"` | Yes | Extension type |
| `version` | `str` | Yes | Semver version string |
| `services` | `List[ExtensionServiceSpec]` | Yes | One or more service specs |
| `kamiwaza` | `KamiwazaIntegrationSpec` | No | Platform integration settings |
| `networking` | `NetworkingSpec` | No | Ingress configuration |
| `security` | `SecuritySpec` | No | Security classification |

### `ExtensionServiceSpec`

Specification for a single service within an extension.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | — | Service name |
| `image` | `str` | — | Container image |
| `primary` | `bool` | `False` | Whether this is the primary ingress service |
| `ports` | `List[ExtensionPort]` | `[]` | Exposed ports (empty = init Job) |
| `env` | `List[Dict]` | `None` | Environment variables |
| `replicas` | `int` | `1` | Number of replicas |
| `resources` | `ResourceSpec` | `None` | CPU/memory requests and limits |
| `command` | `List[str]` | `None` | Container command override |

## Migration from App Garden / Tool Shed

| Legacy (App Garden / Tool Shed) | Extensions API |
|---------------------------------|----------------|
| `client.apps.list_deployments()` | `client.extensions.list_extensions()` |
| `client.apps.deploy(template_id=..., name=...)` | `client.extensions.create_extension(request)` |
| `client.apps.get_deployment(deployment_id)` | `client.extensions.get_extension(name)` |
| `client.apps.stop_deployment(deployment_id)` | `client.extensions.delete_extension(name)` |
| `client.tools.list_deployments()` | `client.extensions.list_extensions()` |
| `client.tools.deploy(template_id=..., name=...)` | `client.extensions.create_extension(request)` |

**Key differences:**
- Extensions are identified by **name** (not UUID)
- Extensions use a **declarative spec** (`CreateExtension`) instead of template IDs
- The extensions API talks to K8s CRDs, not Docker Compose

## Related

- [Apps Service (deprecated)](../apps/README.md)
- [Tools Service (deprecated)](../tools/README.md)
