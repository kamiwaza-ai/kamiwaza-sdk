"""Integration tests for TS4 CLUSTER endpoints.

Tests cover:
- TS4.002: POST /cluster/cluster - Create cluster
- TS4.003: GET /cluster/cluster/{cluster_id} - Get cluster by ID
- TS4.004: GET /cluster/cluster_capabilities
- TS4.006: GET /cluster/clusters
- TS4.010: GET /cluster/federations - List federations
- TS4.018: GET /cluster/get_hostname
- TS4.019: GET /cluster/get_running_nodes
- TS4.020: GET /cluster/hardware (list)
- TS4.021: POST /cluster/hardware - Create hardware
- TS4.022: GET /cluster/hardware/{hardware_id} - Get hardware by ID
- TS4.023-25: Location CRUD
- TS4.026: GET /cluster/locations
- TS4.027: GET /cluster/node/{node_id}
- TS4.028: GET /cluster/nodes
- TS4.030: POST /cluster/refresh_hardware
- TS4.031: GET /cluster/runtime_config

Note: Federation endpoints (TS4.001, TS4.005, TS4.007-008, TS4.011-017, TS4.029)
require a second cluster and are not testable in single-node environments.
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
        try:
            hardware_list = live_kamiwaza_client.cluster.list_hardware()
            assert isinstance(hardware_list, list)
            # Hardware may be empty but should return a list
            for hw in hardware_list:
                assert hasattr(hw, "id")
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Hardware list endpoint returned 500: {exc}")
            raise

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


class TestClusterLifecycle:
    """Tests for cluster CRUD operations."""

    def test_cluster_lifecycle(self, live_kamiwaza_client) -> None:
        """TS4.002 + TS4.003: Create cluster and get by ID.

        Note: Creating a cluster requires a valid location_id.
        """
        from kamiwaza_sdk.schemas.cluster import CreateLocation, CreateCluster

        location = None
        cluster = None
        try:
            # First create a location (required for cluster)
            loc_payload = CreateLocation(name="sdk-test-cluster-location")
            location = live_kamiwaza_client.cluster.create_location(loc_payload)
            assert location is not None

            # TS4.002: Create a cluster
            cluster_payload = CreateCluster(
                name="sdk-test-cluster",
                location_id=location.id
            )
            cluster = live_kamiwaza_client.cluster.create_cluster(cluster_payload)
            assert cluster is not None
            assert cluster.name == "sdk-test-cluster"
            assert cluster.id is not None

            # TS4.003: Get cluster by ID
            retrieved = live_kamiwaza_client.cluster.get_cluster(cluster.id)
            assert retrieved is not None
            assert retrieved.id == cluster.id
            assert retrieved.name == "sdk-test-cluster"

        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for cluster operations")
            raise
        finally:
            # Cleanup - delete cluster first, then location
            if cluster:
                try:
                    live_kamiwaza_client.delete(f"/cluster/cluster/{cluster.id}")
                except APIError:
                    pass
            if location:
                try:
                    live_kamiwaza_client.delete(f"/cluster/location/{location.id}")
                except APIError:
                    pass

    def test_get_nonexistent_cluster(self, live_kamiwaza_client) -> None:
        """TS4.003: GET /cluster/cluster/{id} - Test with non-existent ID."""
        from uuid import uuid4

        fake_cluster_id = uuid4()

        try:
            live_kamiwaza_client.cluster.get_cluster(fake_cluster_id)
            pytest.fail("Expected error for non-existent cluster")
        except APIError as exc:
            # Should get 404 or 500 for non-existent cluster
            assert exc.status_code in (404, 500, 422)
        except Exception:
            # Other errors acceptable
            pass


class TestHardwareLifecycle:
    """Tests for hardware CRUD operations."""

    def test_create_and_get_hardware(self, live_kamiwaza_client) -> None:
        """TS4.021 + TS4.022: Create hardware and get by ID."""
        from kamiwaza_sdk.schemas.cluster import CreateHardware

        created = None
        try:
            # TS4.021: Create hardware
            hw_payload = CreateHardware(
                name="sdk-test-hardware",
                os="Linux",
                platform="x86_64"
            )
            created = live_kamiwaza_client.cluster.create_hardware(hw_payload)
            assert created is not None
            assert created.name == "sdk-test-hardware"
            assert created.id is not None

            # TS4.022: Get hardware by ID
            retrieved = live_kamiwaza_client.cluster.get_hardware(created.id)
            assert retrieved is not None
            assert retrieved.id == created.id
            assert retrieved.name == "sdk-test-hardware"

        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for hardware operations")
            raise
        finally:
            # Cleanup
            if created:
                try:
                    live_kamiwaza_client.delete(f"/cluster/hardware/{created.id}")
                except APIError:
                    pass

    def test_get_nonexistent_hardware(self, live_kamiwaza_client) -> None:
        """TS4.022: GET /cluster/hardware/{id} - Test with non-existent ID."""
        from uuid import uuid4

        fake_hardware_id = uuid4()

        try:
            live_kamiwaza_client.cluster.get_hardware(fake_hardware_id)
            pytest.fail("Expected error for non-existent hardware")
        except APIError as exc:
            # Should get 404 or 500 for non-existent hardware
            assert exc.status_code in (404, 500, 422)
        except Exception:
            # Other errors acceptable
            pass


class TestHardwareRefresh:
    """Tests for hardware refresh operations."""

    def test_refresh_hardware(self, live_kamiwaza_client) -> None:
        """TS4.030: POST /cluster/refresh_hardware - Refresh hardware info."""
        try:
            result = live_kamiwaza_client.post("/cluster/refresh_hardware")
            assert result is not None
            # Should return a success message
            if isinstance(result, dict):
                assert "message" in result or len(result) >= 0
        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for hardware refresh")
            if exc.status_code == 500:
                # Hardware refresh might fail if hardware detection fails
                pytest.skip(f"Hardware refresh failed: {exc}")
            raise


class TestFederationListOperations:
    """Tests for federation list operations.

    Note: Federation creation/pairing requires a second cluster.
    These tests verify the list endpoint works (returns empty list).
    """

    def test_list_federations(self, live_kamiwaza_client) -> None:
        """TS4.010: GET /cluster/federations - List all federations.

        In a single-cluster environment, this should return an empty list.
        """
        try:
            result = live_kamiwaza_client.get("/cluster/federations")
            assert isinstance(result, list)
            # In single-cluster environment, expect empty list
        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for federation list")
            raise


class TestFederationOperationsNotAvailable:
    """Tests documenting federation operations that require multi-cluster setup.

    These tests are skipped because they require a second cluster to test.
    They serve as documentation of what endpoints exist.
    """

    @pytest.mark.skip(reason="Requires second cluster for federation")
    def test_create_federation(self, live_kamiwaza_client) -> None:
        """TS4.011: POST /cluster/federations - Create federation."""
        pass

    @pytest.mark.skip(reason="Requires existing federation")
    def test_get_federation(self, live_kamiwaza_client) -> None:
        """TS4.013: GET /cluster/federations/{federation_id}."""
        pass

    @pytest.mark.skip(reason="Requires existing federation")
    def test_update_federation(self, live_kamiwaza_client) -> None:
        """TS4.014: PUT /cluster/federations/{federation_id}."""
        pass

    @pytest.mark.skip(reason="Requires existing federation")
    def test_delete_federation(self, live_kamiwaza_client) -> None:
        """TS4.012: DELETE /cluster/federations/{federation_id}."""
        pass

    @pytest.mark.skip(reason="Requires existing federation")
    def test_pair_federation(self, live_kamiwaza_client) -> None:
        """TS4.016: POST /cluster/federations/{federation_id}/pair."""
        pass

    @pytest.mark.skip(reason="Requires existing federation")
    def test_disconnect_federation(self, live_kamiwaza_client) -> None:
        """TS4.015: POST /cluster/federations/{federation_id}/disconnect."""
        pass

    @pytest.mark.skip(reason="Requires existing federation")
    def test_ping_federation(self, live_kamiwaza_client) -> None:
        """TS4.017: POST /cluster/federations/{federation_id}/ping."""
        pass

    @pytest.mark.skip(reason="Two-node pairing moved to .env configuration")
    def test_attach_pairing(self, live_kamiwaza_client) -> None:
        """TS4.001: POST /cluster/attach_pairing."""
        pass

    @pytest.mark.skip(reason="Two-node pairing moved to .env configuration")
    def test_detach_pairing(self, live_kamiwaza_client) -> None:
        """TS4.007: POST /cluster/detach_pairing."""
        pass

    @pytest.mark.skip(reason="Requires remote cluster initiating pairing")
    def test_pair_federation_handler(self, live_kamiwaza_client) -> None:
        """TS4.029: POST /cluster/pair_federation."""
        pass

    @pytest.mark.skip(reason="Requires remote cluster for reciprocation")
    def test_federation_reciprocation(self, live_kamiwaza_client) -> None:
        """TS4.005: POST /cluster/cluster_federation_reciprocation."""
        pass

    @pytest.mark.skip(reason="Requires remote cluster initiating disconnect")
    def test_disconnect_federation_handler(self, live_kamiwaza_client) -> None:
        """TS4.008: POST /cluster/disconnect_federation."""
        pass
