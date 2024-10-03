# kamiwaza_client/services/cluster.py

from typing import Dict, List, Optional
from uuid import UUID
from .base_service import BaseService

class ClusterService(BaseService):
    def create_location(self, location_data: Dict) -> Dict:
        """Create a new location."""
        return self.client.post("/cluster/location", json=location_data)

    def update_location(self, location_id: UUID, location_data: Dict) -> Dict:
        """Update an existing location by its ID."""
        return self.client.put(f"/cluster/location/{location_id}", json=location_data)

    def get_location(self, location_id: UUID) -> Dict:
        """Retrieve a specific location."""
        return self.client.get(f"/cluster/location/{location_id}")

    def list_locations(self, skip: Optional[int] = None, limit: Optional[int] = None) -> List[Dict]:
        """List all locations."""
        params = {"skip": skip, "limit": limit}
        return self.client.get("/cluster/locations", params=params)

    def create_cluster(self, cluster_data: Dict) -> Dict:
        """Create a new cluster."""
        return self.client.post("/cluster/cluster", json=cluster_data)

    def get_cluster(self, cluster_id: UUID) -> Dict:
        """Retrieve a specific cluster."""
        return self.client.get(f"/cluster/cluster/{cluster_id}")

    def list_clusters(self, skip: Optional[int] = None, limit: Optional[int] = None) -> List[Dict]:
        """List all clusters."""
        params = {"skip": skip, "limit": limit}
        return self.client.get("/cluster/clusters", params=params)

    def get_node_by_id(self, node_id: UUID) -> Dict:
        """Get details of a specific node."""
        return self.client.get(f"/cluster/node/{node_id}")

    def get_running_nodes(self) -> List[Dict]:
        """Get a list of currently running nodes."""
        return self.client.get("/cluster/get_running_nodes")

    def list_nodes(self, skip: Optional[int] = None, limit: Optional[int] = None, active: Optional[bool] = None) -> List[Dict]:
        """List all nodes."""
        params = {"skip": skip, "limit": limit, "active": active}
        return self.client.get("/cluster/nodes", params=params)

    def create_hardware(self, hardware_data: Dict) -> Dict:
        """Create a new hardware entry."""
        return self.client.post("/cluster/hardware", json=hardware_data)

    def get_hardware(self, hardware_id: UUID) -> Dict:
        """Retrieve a specific hardware entry."""
        return self.client.get(f"/cluster/hardware/{hardware_id}")

    def list_hardware(self, skip: Optional[int] = None, limit: Optional[int] = None) -> List[Dict]:
        """List all hardware entries."""
        params = {"skip": skip, "limit": limit}
        return self.client.get("/cluster/hardware", params=params)

    def get_runtime_config(self) -> Dict:
        """Retrieve the runtime configuration of the cluster."""
        return self.client.get("/cluster/runtime_config")

    def get_hostname(self) -> Dict:
        """Get the hostname for the cluster."""
        return self.client.get("/cluster/get_hostname")