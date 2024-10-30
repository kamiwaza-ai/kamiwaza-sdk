# Cluster Service

The Cluster Service allows you to manage clusters, nodes, hardware, and runtime configurations.

## Methods

### create_location(location: CreateLocation)

Creates a new location.

```python
new_location = CreateLocation(name="US West", datacenter="DC1")
created_location = client.cluster.create_location(new_location)
```

### update_location(location_id: UUID, location: CreateLocation)

Updates an existing location.

```python
updated_location = client.cluster.update_location("location_id_here", CreateLocation(name="US West Updated"))
```

### get_location(location_id: UUID)

Retrieves a specific location.

```python
location = client.cluster.get_location("location_id_here")
```

### list_locations(skip: int = 0, limit: int = 100)

Lists all locations.

```python
locations = client.cluster.list_locations(skip=0, limit=50)
```

### create_cluster(cluster: CreateCluster)

Creates a new cluster.

```python
new_cluster = CreateCluster(name="Production Cluster", location_id="location_id_here")
created_cluster = client.cluster.create_cluster(new_cluster)
```

### get_cluster(cluster_id: UUID)

Retrieves a specific cluster.

```python
cluster = client.cluster.get_cluster("cluster_id_here")
```

### list_clusters(skip: int = 0, limit: int = 100)

Lists all clusters.

```python
clusters = client.cluster.list_clusters(skip=0, limit=50)
```

### get_node_by_id(node_id: UUID)

Gets details of a specific node.

```python
node = client.cluster.get_node_by_id("node_id_here")
```

### get_running_nodes()

Gets a list of currently running nodes.

```python
running_nodes = client.cluster.get_running_nodes()
```

### list_nodes(skip: int = 0, limit: int = 100, active: bool = None)

Lists all nodes.

```python
nodes = client.cluster.list_nodes(skip=0, limit=50, active=True)
```

### create_hardware(hardware: CreateHardware)

Creates a new hardware entry.

```python
new_hardware = CreateHardware(name="GPU Server", gpus=[{"name": "NVIDIA A100", "count": 4}])
created_hardware = client.cluster.create_hardware(new_hardware)
```

### get_hardware(hardware_id: UUID)

Retrieves a specific hardware entry.

```python
hardware = client.cluster.get_hardware("hardware_id_here")
```

### list_hardware(skip: int = 0, limit: int = 100)

Lists all hardware entries.

```python
hardware_list = client.cluster.list_hardware(skip=0, limit=50)
```

### get_runtime_config()

Retrieves the runtime configuration of the cluster.

```python
runtime_config = client.cluster.get_runtime_config()
```

### get_hostname()

Gets the cluster's hostname.

```python
hostname = client.cluster.get_hostname()
```
