# Cluster Service

## Overview
The Cluster Service (`ClusterService`) provides comprehensive cluster and infrastructure management for the Kamiwaza AI Platform. Located in `kamiwaza_client/services/cluster.py`, this service handles location management, cluster operations, node management, and hardware configuration.

## Key Features
- Location Management
- Cluster Operations
- Node Management
- Hardware Configuration
- Runtime Configuration
- Hostname Management

## Location Management

### Available Methods
- `create_location(location: CreateLocation) -> Location`: Create new location
- `update_location(location_id: UUID, location: UpdateLocation) -> Location`: Update location
- `get_location(location_id: UUID) -> Location`: Get location info
- `list_locations() -> List[Location]`: List all locations

```python
# Create new location
location = client.cluster.create_location(CreateLocation(
    name="us-west",
    provider="aws",
    region="us-west-2"
))

# Update location
updated = client.cluster.update_location(
    location_id=location.id,
    location=UpdateLocation(name="us-west-prod")
)

# Get location details
location = client.cluster.get_location(location_id)

# List all locations
locations = client.cluster.list_locations()
```

## Cluster Management

### Available Methods
- `create_cluster(cluster: CreateCluster) -> Cluster`: Create new cluster
- `get_cluster(cluster_id: UUID) -> Cluster`: Get cluster info
- `list_clusters() -> List[Cluster]`: List all clusters
- `get_hostname() -> str`: Get cluster hostname

```python
# Create new cluster
cluster = client.cluster.create_cluster(CreateCluster(
    name="training-cluster",
    location_id=location_id,
    node_count=3
))

# Get cluster info
cluster = client.cluster.get_cluster(cluster_id)

# List clusters
clusters = client.cluster.list_clusters()

# Get hostname
hostname = client.cluster.get_hostname()
```

## Node Management

### Available Methods
- `get_node_by_id(node_id: UUID) -> Node`: Get node info
- `get_running_nodes() -> List[Node]`: List running nodes
- `list_nodes() -> List[Node]`: List all nodes

```python
# Get node details
node = client.cluster.get_node_by_id(node_id)

# List running nodes
running_nodes = client.cluster.get_running_nodes()

# List all nodes
all_nodes = client.cluster.list_nodes()
```

## Hardware Management

### Available Methods
- `create_hardware(hardware: CreateHardware) -> Hardware`: Create hardware entry
- `get_hardware(hardware_id: UUID) -> Hardware`: Get hardware info
- `list_hardware() -> List[Hardware]`: List hardware entries
- `get_runtime_config() -> RuntimeConfig`: Get runtime configuration

```python
# Create hardware entry
hardware = client.cluster.create_hardware(CreateHardware(
    name="gpu-node",
    gpu_count=4,
    gpu_type="nvidia-a100"
))

# Get hardware info
hardware = client.cluster.get_hardware(hardware_id)

# List hardware
hardware_list = client.cluster.list_hardware()

# Get runtime config
config = client.cluster.get_runtime_config()
```

## Error Handling
The service includes built-in error handling for common scenarios:
```python
try:
    cluster = client.cluster.create_cluster(cluster_config)
except LocationNotFoundError:
    print("Location not found")
except ResourceError as e:
    print(f"Resource allocation failed: {e}")
except APIError as e:
    print(f"Operation failed: {e}")
```

## Best Practices
1. Validate location existence before cluster creation
2. Monitor node health regularly
3. Use appropriate hardware configurations
4. Implement proper error handling
5. Clean up unused resources
6. Consider resource limits
7. Monitor cluster performance
8. Use meaningful naming conventions

## Performance Considerations
- Node count affects cluster performance
- Hardware configuration impacts resource availability
- Location selection influences latency
- Runtime configuration affects resource utilization
