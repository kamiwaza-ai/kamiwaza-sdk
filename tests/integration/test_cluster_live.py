"""Integration tests for TS4 CLUSTER endpoints.

Tests cover:
- TS4.006: GET /cluster/clusters
- TS4.018: GET /cluster/get_hostname
- TS4.019: GET /cluster/get_running_nodes
- TS4.020: GET /cluster/hardware (list)
- TS4.026: GET /cluster/locations
- TS4.028: GET /cluster/nodes
- TS4.031: GET /cluster/runtime_config
- TS4.004: GET /cluster/cluster_capabilities
"""
from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


class TestClusterReadOperations:
    """Tests for read-only cluster operations."""

    def test_list_clusters(self, live_kamiwaza_client) -> None:
        """TS4.006: GET /cluster/clusters - List all clusters."""
        clusters = live_kamiwaza_client.cluster.list_clusters()
        assert isinstance(clusters, list)
        # Clusters may be empty but should return a list
        for cluster in clusters:
            assert hasattr(cluster, "id")
            assert hasattr(cluster, "name")

    def test_get_hostname(self, live_kamiwaza_client) -> None:
        """TS4.018: GET /cluster/get_hostname - Get cluster hostname."""
        result = live_kamiwaza_client.cluster.get_hostname()
        assert isinstance(result, dict)
        # Should contain hostname information
        assert "hostname" in result or len(result) > 0

    def test_get_running_nodes(self, live_kamiwaza_client) -> None:
        """TS4.019: GET /cluster/get_running_nodes - Get running nodes list."""
        nodes = live_kamiwaza_client.cluster.get_running_nodes()
        assert isinstance(nodes, list)
        # At least one node should be running in a functional cluster
        if nodes:
            node = nodes[0]
            assert hasattr(node, "node_id") or hasattr(node, "id")

    def test_list_hardware(self, live_kamiwaza_client) -> None:
        """TS4.020: GET /cluster/hardware - List hardware entries."""
        hardware_list = live_kamiwaza_client.cluster.list_hardware()
        assert isinstance(hardware_list, list)
        # Hardware may be empty but should return a list
        for hw in hardware_list:
            assert hasattr(hw, "id")

    def test_list_locations(self, live_kamiwaza_client) -> None:
        """TS4.026: GET /cluster/locations - List all locations."""
        locations = live_kamiwaza_client.cluster.list_locations()
        assert isinstance(locations, list)
        # Locations may be empty but should return a list
        for loc in locations:
            assert hasattr(loc, "id")
            assert hasattr(loc, "name")

    def test_list_nodes(self, live_kamiwaza_client) -> None:
        """TS4.028: GET /cluster/nodes - List all nodes."""
        nodes = live_kamiwaza_client.cluster.list_nodes()
        assert isinstance(nodes, list)
        # At least one node should exist in a functional cluster
        if nodes:
            node = nodes[0]
            assert hasattr(node, "id")

    def test_get_runtime_config(self, live_kamiwaza_client) -> None:
        """TS4.031: GET /cluster/runtime_config - Get runtime configuration."""
        config = live_kamiwaza_client.cluster.get_runtime_config()
        assert isinstance(config, dict)
        # Should return some configuration data


class TestClusterCapabilities:
    """Tests for cluster capability operations."""

    def test_get_cluster_capabilities(self, live_kamiwaza_client) -> None:
        """TS4.004: GET /cluster/cluster_capabilities - Get cluster capabilities.

        Note: This endpoint may not be exposed via SDK, testing via direct API call.
        """
        try:
            result = live_kamiwaza_client.get("/cluster/cluster_capabilities")
            assert isinstance(result, (dict, list))
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("cluster_capabilities endpoint not available")
            raise


class TestClusterNodeDetails:
    """Tests for node detail operations that depend on existing nodes."""

    def test_get_node_by_id(self, live_kamiwaza_client) -> None:
        """TS4.027: GET /cluster/node/{node_id} - Get node details by ID."""
        # First list nodes to get an ID
        nodes = live_kamiwaza_client.cluster.list_nodes()
        if not nodes:
            pytest.skip("No nodes available to test node details")

        node_id = nodes[0].id
        details = live_kamiwaza_client.cluster.get_node_by_id(node_id)
        assert details is not None
        # NodeDetails wraps a node object
        assert hasattr(details, "node")
        assert details.node.id == node_id


class TestClusterLocationLifecycle:
    """Tests for location CRUD operations - requires cleanup."""

    def test_location_lifecycle(self, live_kamiwaza_client) -> None:
        """TS4.023/24/25: Location create, get, update operations."""
        from kamiwaza_sdk.schemas.cluster import CreateLocation

        # Create a location
        create_payload = CreateLocation(
            name="sdk-test-location",
            region="test-region"
        )

        created = None
        try:
            created = live_kamiwaza_client.cluster.create_location(create_payload)
            assert created is not None
            assert created.name == "sdk-test-location"

            # Get the location
            retrieved = live_kamiwaza_client.cluster.get_location(created.id)
            assert retrieved.id == created.id
            assert retrieved.name == "sdk-test-location"

            # Update the location
            update_payload = CreateLocation(
                name="sdk-test-location-updated",
                region="test-region-updated"
            )
            updated = live_kamiwaza_client.cluster.update_location(created.id, update_payload)
            assert updated.name == "sdk-test-location-updated"

        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for location operations")
            raise
        finally:
            # Cleanup: Delete if created (no SDK delete method, use direct API)
            if created:
                try:
                    live_kamiwaza_client.delete(f"/cluster/location/{created.id}")
                except APIError:
                    pass  # Best effort cleanup
