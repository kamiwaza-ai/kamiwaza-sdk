# kamiwaza_sdk/schemas/cluster.py

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

class CreateLocation(BaseModel):
    name: str = Field(description="Name of the location")
    datacenter: Optional[str] = Field(default=None, description="Datacenter of the location")
    region: Optional[str] = Field(default=None, description="Region of the location")
    zone: Optional[str] = Field(default=None, description="Zone of the location")
    building: Optional[str] = Field(default=None, description="Building of the location")
    address: Optional[str] = Field(default=None, description="Address of the location")
    contact_phone: Optional[str] = Field(default=None, description="Contact phone for the location")
    contact_name: Optional[str] = Field(default=None, description="Contact name for the location")
    contact_email: Optional[EmailStr] = Field(default=None, description="Contact email for the location")
    placement_group_id: Optional[str] = Field(default=None, description="Ray placement group ID")

class Location(CreateLocation):
    id: UUID = Field(description="Unique identifier for the location")
    created_at: datetime = Field(description="Time the location was created")

class CreateHardware(BaseModel):
    name: Optional[str] = Field(default=None, description="Name of the hardware")
    gpus: Optional[List[Dict]] = Field(default=None, description="GPU configuration of the hardware")
    cluster_ip: Optional[str] = Field(default=None, description="Cluster IP of the hardware")
    processors: Optional[List[str]] = Field(default=None, description="Processor configuration of the hardware")
    processor_vendor: Optional[str] = Field(default=None, description="Processor vendor of the hardware")
    os: Optional[str] = Field(default=None, description="Operating system of the hardware")
    platform: Optional[str] = Field(default=None, description="Platform of the hardware")
    local_node_id: Optional[str] = Field(default=None, description="Local node ID of the hardware")
    ray_node_id: Optional[str] = Field(default=None, description="Ray node ID of the hardware")
    configuration: Optional[Dict] = Field(default=None, description="Configuration of the hardware")

class Hardware(CreateHardware):
    id: UUID = Field(description="Unique identifier for the hardware")
    created_at: datetime = Field(description="Time the hardware was created")
    node_id: Optional[UUID] = Field(default=None, description="ID of the associated Node")
    active: Optional[bool] = Field(default=None, description="Active status of the hardware")

class CreateCluster(BaseModel):
    location_id: UUID = Field(description="Unique identifier for the location of the cluster")
    name: str = Field(description="Name of the cluster")

class Cluster(CreateCluster):
    id: UUID = Field(description="Unique identifier for the cluster")
    created_at: datetime = Field(description="Time the cluster was created")

class Node(BaseModel):
    id: UUID = Field(description="Unique identifier for the node")
    hardware_id: Optional[UUID] = Field(default=None, description="Hardware configuration of the node")
    ray_id: Optional[str] = Field(default=None, description="Ray identifier for the node")
    last_seen: Optional[datetime] = Field(default=None, description="Last time the node was seen via Ray")
    last_config: Dict = Field(description="Last Ray configuration of the node")
    created_at: datetime = Field(description="Time the node was created")
    hardware: Optional[Hardware] = None
    location_id: Optional[UUID] = Field(default=None, description="Location ID of the node")
    active: bool = Field(default=True, description="Active status of the node")

class NodeListNode(BaseModel):
    node_id: Optional[str] = Field(default=None, description="Node ID")
    alive: Optional[bool] = Field(default=None, description="Node alive status")
    node_manager_address: Optional[str] = Field(default=None, description="Node Manager Address")
    node_manager_hostname: Optional[str] = Field(default=None, description="Node Manager Hostname")
    node_manager_port: Optional[int] = Field(default=None, description="Node Manager Port")
    object_manager_port: Optional[int] = Field(default=None, description="Object Manager Port")
    object_store_socket_name: Optional[str] = Field(default=None, description="Object Store Socket Name")
    raylet_socket_name: Optional[str] = Field(default=None, description="Raylet Socket Name")
    metrics_export_port: Optional[int] = Field(default=None, description="Metrics Export Port")
    resources: Optional[Dict] = Field(default=None, description="Resources")
    node_ip: Optional[str] = Field(default=None, description="Node IP")

class NodeDetails(BaseModel):
    node: Node
    location: Optional[Location] = None
    hardware: Optional[Hardware] = None
    node_list_node: Optional[NodeListNode] = None

model_config = {
    "from_attributes": True
}