"""
Real-world tests for GCP provider functionality.

These tests use actual GCP APIs when credentials are available,
demonstrating real user workflows without mocks.
"""

import pytest
import os
import json
import time
import tempfile
from pathlib import Path
from clustrix.cloud_providers.gcp import GCPProvider
from clustrix.config import ClusterConfig


class TestGCPProviderReal:
    """Test GCP provider with real infrastructure."""

    @pytest.fixture
    def gcp_credentials(self):
        """Get real GCP credentials if available."""
        # Check multiple sources for GCP credentials
        sources = [
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
            os.getenv("GCP_SERVICE_ACCOUNT_JSON"),
            os.path.expanduser("~/.gcp/credentials.json"),
            os.path.expanduser("~/.config/gcloud/application_default_credentials.json"),
        ]

        for source in sources:
            if source and os.path.exists(source):
                with open(source, "r") as f:
                    return json.load(f)

        # Check if service account JSON is in environment variable
        service_account_json = os.getenv("GCP_SERVICE_ACCOUNT_JSON")
        if service_account_json:
            try:
                return json.loads(service_account_json)
            except json.JSONDecodeError:
                pass

        pytest.skip("GCP credentials not available")

    @pytest.fixture
    def test_config(self):
        """Create test configuration."""
        return {
            "project_id": os.getenv("GCP_PROJECT_ID", "clustrix-test"),
            "region": os.getenv("GCP_REGION", "us-central1"),
            "zone": os.getenv("GCP_ZONE", "us-central1-a"),
            "cluster_prefix": f"test-{int(time.time())}",
            "cleanup_on_failure": True,
        }

    def test_provider_initialization(self):
        """
        Test provider initialization without credentials.

        This demonstrates:
        - Default configuration values
        - Uninitialized state
        - No mock dependencies
        """
        provider = GCPProvider()

        # Verify default state
        assert provider.project_id is None
        assert provider.region == "us-central1"
        assert provider.zone == "us-central1-a"
        assert provider.compute_client is None
        assert provider.container_client is None
        assert provider.service_account_info is None
        assert not provider.authenticated

    @pytest.mark.real_world
    def test_authentication_with_service_account(self, gcp_credentials, test_config):
        """
        Test authentication with real GCP service account.

        This demonstrates:
        - Real service account authentication
        - Client initialization
        - Project validation
        """
        provider = GCPProvider()

        # Authenticate with real credentials
        success = provider.authenticate(
            credentials={
                "project_id": test_config["project_id"],
                "service_account_key": json.dumps(gcp_credentials),
            }
        )

        # Verify authentication
        assert success is True
        assert provider.authenticated is True
        assert provider.project_id == test_config["project_id"]
        assert provider.compute_client is not None
        assert provider.container_client is not None
        assert provider.service_account_info is not None

    @pytest.mark.real_world
    def test_list_regions_and_zones(self, gcp_credentials, test_config):
        """
        Test listing available regions and zones.

        This demonstrates:
        - Real API calls to GCP
        - Region/zone enumeration
        - Resource availability checking
        """
        provider = GCPProvider()
        provider.authenticate(
            credentials={
                "project_id": test_config["project_id"],
                "service_account_key": json.dumps(gcp_credentials),
            }
        )

        # List regions
        regions = provider.list_regions()
        assert isinstance(regions, list)
        assert len(regions) > 0
        assert any("us-central1" in r for r in regions)

        # List zones in a region
        zones = provider.list_zones(region="us-central1")
        assert isinstance(zones, list)
        assert len(zones) > 0
        assert "us-central1-a" in zones

    @pytest.mark.real_world
    def test_check_quota_and_limits(self, gcp_credentials, test_config):
        """
        Test checking project quotas and limits.

        This demonstrates:
        - Real quota API calls
        - Resource limit validation
        - Capacity planning
        """
        provider = GCPProvider()
        provider.authenticate(
            credentials={
                "project_id": test_config["project_id"],
                "service_account_key": json.dumps(gcp_credentials),
            }
        )

        # Check compute quotas
        quotas = provider.check_quotas()

        assert isinstance(quotas, dict)
        # Common quotas that should exist
        expected_quotas = ["CPUS", "DISKS_TOTAL_GB", "INSTANCES"]
        for quota_name in expected_quotas:
            assert quota_name in quotas or any(
                quota_name in key for key in quotas.keys()
            )

    @pytest.mark.real_world
    def test_create_and_delete_vm_instance(self, gcp_credentials, test_config):
        """
        Test creating and deleting a VM instance.

        This demonstrates:
        - Real VM provisioning
        - Instance configuration
        - Resource cleanup
        """
        provider = GCPProvider()
        provider.authenticate(
            credentials={
                "project_id": test_config["project_id"],
                "service_account_key": json.dumps(gcp_credentials),
            }
        )

        instance_name = f"clustrix-test-vm-{int(time.time())}"

        try:
            # Create VM instance
            instance_config = {
                "name": instance_name,
                "machine_type": "e2-micro",  # Smallest instance type
                "disk_size_gb": 10,
                "image_family": "debian-11",
                "image_project": "debian-cloud",
                "network_tags": ["clustrix-test"],
                "metadata": {"clustrix-test": "true", "created-by": "test-suite"},
            }

            operation = provider.create_instance(
                zone=test_config["zone"], instance_config=instance_config
            )

            # Wait for instance creation
            instance = provider.wait_for_operation(
                operation=operation, zone=test_config["zone"], timeout=300
            )

            assert instance is not None
            assert instance["name"] == instance_name
            assert instance["status"] == "RUNNING"

            # Verify instance exists
            instances = provider.list_instances(zone=test_config["zone"])
            assert any(i["name"] == instance_name for i in instances)

        finally:
            # Clean up - delete the instance
            try:
                provider.delete_instance(
                    zone=test_config["zone"], instance_name=instance_name
                )

                # Wait for deletion
                time.sleep(10)

                # Verify deletion
                instances = provider.list_instances(zone=test_config["zone"])
                assert not any(i["name"] == instance_name for i in instances)

            except Exception as e:
                print(f"Warning: Failed to clean up instance {instance_name}: {e}")

    @pytest.mark.real_world
    def test_create_and_delete_gke_cluster(self, gcp_credentials, test_config):
        """
        Test creating and deleting a GKE cluster.

        This demonstrates:
        - Real GKE cluster provisioning
        - Kubernetes configuration
        - Cluster lifecycle management
        """
        provider = GCPProvider()
        provider.authenticate(
            credentials={
                "project_id": test_config["project_id"],
                "service_account_key": json.dumps(gcp_credentials),
            }
        )

        cluster_name = f"clustrix-test-gke-{int(time.time())}"

        try:
            # Create minimal GKE cluster
            cluster_config = {
                "name": cluster_name,
                "initial_node_count": 1,
                "node_config": {
                    "machine_type": "e2-micro",
                    "disk_size_gb": 10,
                    "preemptible": True,  # Use preemptible for cost savings
                    "oauth_scopes": ["https://www.googleapis.com/auth/cloud-platform"],
                },
                "master_auth": {
                    "client_certificate_config": {"issue_client_certificate": False}
                },
                "labels": {"clustrix-test": "true", "created-by": "test-suite"},
            }

            # Create cluster
            operation = provider.create_gke_cluster(
                zone=test_config["zone"], cluster_config=cluster_config
            )

            # Wait for cluster creation (this can take several minutes)
            cluster = provider.wait_for_gke_operation(
                operation=operation, zone=test_config["zone"], timeout=600  # 10 minutes
            )

            assert cluster is not None
            assert cluster["name"] == cluster_name
            assert cluster["status"] == "RUNNING"

            # Get cluster credentials
            credentials = provider.get_gke_credentials(
                zone=test_config["zone"], cluster_name=cluster_name
            )

            assert credentials is not None
            assert "kubeconfig" in credentials or "endpoint" in credentials

        finally:
            # Clean up - delete the cluster
            try:
                provider.delete_gke_cluster(
                    zone=test_config["zone"], cluster_name=cluster_name
                )

                # Wait for deletion
                time.sleep(30)

                # Verify deletion
                clusters = provider.list_gke_clusters(zone=test_config["zone"])
                assert not any(c["name"] == cluster_name for c in clusters)

            except Exception as e:
                print(f"Warning: Failed to clean up cluster {cluster_name}: {e}")

    @pytest.mark.real_world
    def test_storage_operations(self, gcp_credentials, test_config):
        """
        Test Google Cloud Storage operations.

        This demonstrates:
        - Bucket creation and deletion
        - Object upload and download
        - Storage lifecycle management
        """
        provider = GCPProvider()
        provider.authenticate(
            credentials={
                "project_id": test_config["project_id"],
                "service_account_key": json.dumps(gcp_credentials),
            }
        )

        bucket_name = f"clustrix-test-{int(time.time())}"

        try:
            # Create storage bucket
            bucket = provider.create_storage_bucket(
                bucket_name=bucket_name,
                location=test_config["region"],
                storage_class="STANDARD",
            )

            assert bucket is not None
            assert bucket.name == bucket_name

            # Upload test data
            test_data = b"Test data for clustrix storage operations"
            blob_name = "test-file.txt"

            blob = provider.upload_to_bucket(
                bucket_name=bucket_name, blob_name=blob_name, data=test_data
            )

            assert blob is not None
            assert blob.name == blob_name

            # Download and verify
            downloaded_data = provider.download_from_bucket(
                bucket_name=bucket_name, blob_name=blob_name
            )

            assert downloaded_data == test_data

            # List bucket contents
            blobs = provider.list_bucket_contents(bucket_name=bucket_name)
            assert len(blobs) == 1
            assert blobs[0].name == blob_name

        finally:
            # Clean up
            try:
                provider.delete_storage_bucket(
                    bucket_name=bucket_name, force=True  # Delete even if not empty
                )
            except Exception as e:
                print(f"Warning: Failed to clean up bucket {bucket_name}: {e}")

    @pytest.mark.real_world
    def test_network_operations(self, gcp_credentials, test_config):
        """
        Test VPC network operations.

        This demonstrates:
        - VPC creation and configuration
        - Firewall rule management
        - Network security setup
        """
        provider = GCPProvider()
        provider.authenticate(
            credentials={
                "project_id": test_config["project_id"],
                "service_account_key": json.dumps(gcp_credentials),
            }
        )

        network_name = f"clustrix-test-vpc-{int(time.time())}"

        try:
            # Create VPC network
            network = provider.create_vpc_network(
                network_name=network_name,
                auto_create_subnetworks=True,
                description="Test VPC for clustrix",
            )

            assert network is not None
            assert network["name"] == network_name

            # Create firewall rule
            firewall_rule_name = f"{network_name}-allow-ssh"
            firewall_rule = provider.create_firewall_rule(
                rule_name=firewall_rule_name,
                network_name=network_name,
                source_ranges=["0.0.0.0/0"],
                allowed_protocols=["tcp"],
                allowed_ports=["22"],
                target_tags=["clustrix-ssh"],
            )

            assert firewall_rule is not None
            assert firewall_rule["name"] == firewall_rule_name

            # List networks
            networks = provider.list_networks()
            assert any(n["name"] == network_name for n in networks)

        finally:
            # Clean up
            try:
                # Delete firewall rule first
                provider.delete_firewall_rule(firewall_rule_name)
                time.sleep(5)

                # Delete network
                provider.delete_vpc_network(network_name)

            except Exception as e:
                print(f"Warning: Failed to clean up network {network_name}: {e}")

    def test_error_handling_without_credentials(self):
        """
        Test error handling when credentials are missing.

        This demonstrates:
        - Graceful failure without credentials
        - Appropriate error messages
        - No mock dependencies
        """
        provider = GCPProvider()

        # Attempt authentication without credentials
        success = provider.authenticate(credentials={})
        assert success is False
        assert not provider.authenticated

        # Attempt operations without authentication
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.list_instances()

        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.create_instance(zone="us-central1-a", instance_config={})

    def test_region_zone_validation(self):
        """
        Test region and zone validation logic.

        This demonstrates:
        - Input validation
        - Configuration constraints
        - No external dependencies
        """
        provider = GCPProvider()

        # Test valid regions/zones
        assert provider.is_valid_region("us-central1")
        assert provider.is_valid_zone("us-central1-a")

        # Test invalid formats
        assert not provider.is_valid_region("invalid_region")
        assert not provider.is_valid_zone("us-central1")  # Missing zone letter
        assert not provider.is_valid_zone("invalid-zone-format")


class TestGCPProviderIntegrationWorkflows:
    """Integration tests showing complete GCP workflows."""

    @pytest.mark.real_world
    def test_complete_cluster_lifecycle(self, gcp_credentials, test_config):
        """
        Test complete cluster lifecycle as users would use it.

        This demonstrates the full user experience from setup
        through execution to cleanup.
        """
        from clustrix import cluster, configure
        from clustrix.config import ClusterConfig

        # User configures GCP provider
        config = ClusterConfig()
        config.cluster_type = "kubernetes"
        config.auto_provision_k8s = True
        config.k8s_provider = "gcp"
        config.gcp_project_id = test_config["project_id"]
        config.gcp_region = test_config["region"]
        config.gcp_zone = test_config["zone"]
        config.gcp_credentials = json.dumps(gcp_credentials)

        # Apply configuration
        configure(config)

        try:
            # User defines computation function
            @cluster(cores=2, memory="2Gi", auto_provision=True)
            def analyze_data_on_gcp(data_size):
                """Run analysis on GCP infrastructure."""
                import platform
                import socket
                import numpy as np

                # Generate and analyze data
                data = np.random.randn(data_size, data_size)

                # Compute statistics
                results = {
                    "mean": float(np.mean(data)),
                    "std": float(np.std(data)),
                    "min": float(np.min(data)),
                    "max": float(np.max(data)),
                    "shape": data.shape,
                    "platform": platform.platform(),
                    "hostname": socket.gethostname(),
                    "provider": "gcp",
                }

                # Compute eigenvalues for small subset
                if data_size <= 100:
                    eigenvalues = np.linalg.eigvals(data[:10, :10])
                    results["max_eigenvalue"] = float(np.max(np.abs(eigenvalues)))

                return results

            # Execute on GCP
            result = analyze_data_on_gcp(50)

            # Validate execution
            assert isinstance(result, dict)
            assert "mean" in result
            assert "std" in result
            assert result["shape"] == (50, 50)
            assert result["provider"] == "gcp"
            assert (
                "gke" in result["hostname"].lower()
                or "clustrix" in result["hostname"].lower()
            )

        finally:
            # Cleanup would happen automatically with k8s_cleanup_on_exit
            pass
