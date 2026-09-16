# kamiwaza_sdk/services/cluster.py

from typing import List, Optional, Dict, Any
from uuid import UUID
from ..schemas.cluster import (
    CreateLocation, Location, CreateHardware, Hardware,
    CreateCluster, Cluster, Node, NodeDetails, NodeListNode
)
from .base_service import BaseService

class ClusterService(BaseService):
    def create_location(self, location: CreateLocation) -> Location:
        """Register a physical or logical location for cluster nodes."""
        response = self.client.post("/cluster/location", json=location.model_dump())
        return Location.model_validate(response)

    def update_location(self, location_id: UUID, location: CreateLocation) -> Location:
        """Update an existing location by its ID."""
        response = self.client.put(f"/cluster/location/{location_id}", json=location.model_dump())
        return Location.model_validate(response)

    def get_location(self, location_id: UUID) -> Location:
        """Fetch one registered location by its identifier."""
        response = self.client.get(f"/cluster/location/{location_id}")
        return Location.model_validate(response)

    def list_locations(self, skip: Optional[int] = None, limit: Optional[int] = None) -> List[Location]:
        """List every location registered for cluster nodes."""
        params = {"skip": skip, "limit": limit}
        response = self.client.get("/cluster/locations", params=params)
        return [Location.model_validate(item) for item in response]

    def create_cluster(self, cluster: CreateCluster) -> Cluster:
        """Register a new cluster in the platform's inventory."""
        response = self.client.post("/cluster/cluster", json=cluster.model_dump(mode='json'))
        return Cluster.model_validate(response)

    def get_cluster(self, cluster_id: UUID) -> Cluster:
        """Fetch one registered cluster by its identifier."""
        response = self.client.get(f"/cluster/cluster/{cluster_id}")
        return Cluster.model_validate(response)

    def list_clusters(self, skip: Optional[int] = None, limit: Optional[int] = None) -> List[Cluster]:
        """List every cluster registered in the platform's inventory."""
        params = {"skip": skip, "limit": limit}
        response = self.client.get("/cluster/clusters", params=params)
        return [Cluster.model_validate(item) for item in response]

    def get_node_by_id(self, node_id: UUID) -> NodeDetails:
        """Get details of a specific node."""
        response = self.client.get(f"/cluster/node/{node_id}")
        return NodeDetails.model_validate(response)

    def get_running_nodes(self) -> List[NodeListNode]:
        """Get a list of currently running nodes."""
        response = self.client.get("/cluster/get_running_nodes")
        return [NodeListNode.model_validate(item) for item in response]

    def list_nodes(self, skip: Optional[int] = None, limit: Optional[int] = None, active: Optional[bool] = None) -> List[Node]:
        """List every node known to the cluster, running or not."""
        params = {"skip": skip, "limit": limit, "active": active}
        response = self.client.get("/cluster/nodes", params=params)
        return [Node.model_validate(item) for item in response]

    def create_hardware(self, hardware: CreateHardware) -> Hardware:
        """Register a hardware profile that nodes can be matched against."""
        response = self.client.post("/cluster/hardware", json=hardware.model_dump())
        return Hardware.model_validate(response)

    def get_hardware(self, hardware_id: UUID) -> Hardware:
        """Fetch one registered hardware profile by its identifier."""
        response = self.client.get(f"/cluster/hardware/{hardware_id}")
        return Hardware.model_validate(response)

    def list_hardware(self, skip: Optional[int] = None, limit: Optional[int] = None) -> List[Hardware]:
        """List every registered hardware profile in the inventory."""
        params = {"skip": skip, "limit": limit}
        response = self.client.get("/cluster/hardware", params=params)
        return [Hardware.model_validate(item) for item in response]

    def get_runtime_config(self) -> Dict[str, Any]:
        """Retrieve the runtime configuration of the cluster."""
        return self.client.get("/cluster/runtime_config")

    def get_hostname(self) -> Dict[str, str]:
        """Get the hostname for the cluster."""
        return self.client.get("/cluster/get_hostname")