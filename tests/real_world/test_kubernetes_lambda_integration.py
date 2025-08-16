"""
Real-world integration tests for Lambda Cloud Kubernetes provisioning.

These tests use actual Lambda Cloud API calls and credentials to verify that:
1. Lambda Cloud instances can be created and configured for Kubernetes-style jobs
2. SSH connectivity and job execution works correctly
3. GPU instances are properly provisioned when available
4. Instances can be properly cleaned up after use
5. Error handling works correctly with real API responses

Requirements:
- Valid Lambda Cloud API key
- Network connectivity to Lambda Cloud API
- Sufficient Lambda Cloud quota for instance creation
- SSH key generation capabilities
"""

import os
import time
import pytest
import logging
import socket
from typing import Dict, Any

from clustrix.kubernetes.lambda_provisioner import LambdaCloudKubernetesProvisioner
from clustrix.kubernetes.cluster_provisioner import ClusterSpec

logger = logging.getLogger(__name__)


@pytest.mark.real_world
class TestLambdaCloudKubernetesIntegration:
    """Real-world integration tests for Lambda Cloud Kubernetes provisioning."""

    @pytest.fixture(scope="class")
    def lambda_credentials(self):
        """Get Lambda Cloud credentials from environment or 1Password."""
        # Try environment variables first
        api_key = os.getenv("LAMBDA_API_KEY") or os.getenv("LAMBDA_CLOUD_API_KEY")

        if not api_key:
            # Try 1Password CLI
            try:
                import subprocess

                # Get API key from 1Password
                result = subprocess.run(
                    ["op", "item", "get", "Lambda-Cloud", "--field", "api_key"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    api_key = result.stdout.strip()

            except (
                subprocess.TimeoutExpired,
                subprocess.SubprocessError,
                FileNotFoundError,
            ):
                pass

        if not api_key:
            pytest.skip("Lambda Cloud API key not available")

        return {"api_key": api_key}

    @pytest.fixture(scope="class")
    def provisioner(self, lambda_credentials):
        """Create Lambda Cloud Kubernetes provisioner."""
        return LambdaCloudKubernetesProvisioner(
            credentials=lambda_credentials,
            region="us-west-2",  # Common Lambda Cloud region
        )

    @pytest.fixture(scope="class")
    def cluster_spec(self):
        """Create test cluster specification."""
        test_id = int(time.time())
        return ClusterSpec(
            cluster_name=f"test-lambda-k8s-{test_id}",
            provider="lambda",
            node_count=1,  # Single instance for testing
            kubernetes_version="1.28",
            region="us-west-2",
        )

    def test_credential_validation(self, provisioner):
        """Test that Lambda Cloud credentials can be validated."""
        logger.info("🧪 Testing Lambda Cloud credential validation")

        result = provisioner.validate_credentials()
        assert result is True, "Lambda Cloud credentials should be valid"

        logger.info("✅ Lambda Cloud credentials validated successfully")

    def test_instance_provisioning_lifecycle(self, provisioner, cluster_spec):
        """Test complete instance provisioning lifecycle."""
        logger.info(
            f"🧪 Testing Lambda Cloud instance provisioning lifecycle for {cluster_spec.cluster_name}"
        )

        cluster_info = None
        try:
            # Provision the instances
            start_time = time.time()
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)
            provision_time = time.time() - start_time

            # Verify cluster info structure
            assert cluster_info is not None, "Cluster info should not be None"
            assert cluster_info["cluster_id"] == cluster_spec.cluster_name
            assert cluster_info["provider"] == "lambda"
            assert cluster_info["ready_for_jobs"] is True
            assert "instances" in cluster_info
            assert len(cluster_info["instances"]) == cluster_spec.node_count
            assert "kubectl_config" in cluster_info

            # Verify instance details
            instance = cluster_info["instances"][0]
            assert "id" in instance
            assert "ip" in instance
            assert "status" in instance
            assert instance["status"] == "active"

            logger.info(
                f"✅ Instances provisioned in {provision_time:.1f}s: {len(cluster_info['instances'])} instances"
            )

            # Test instance connectivity
            instance_ip = instance["ip"]
            self._test_instance_connectivity(instance_ip)

            # Test cluster status
            status = provisioner.get_cluster_status(cluster_spec.cluster_name)
            assert status["ready_for_jobs"] is True
            assert status["status"] == "ACTIVE"

            logger.info(
                "✅ Lambda Cloud instance provisioning lifecycle completed successfully"
            )

        finally:
            # Clean up
            if cluster_info:
                logger.info("🧹 Cleaning up Lambda Cloud instances")
                success = provisioner.destroy_cluster_infrastructure(
                    cluster_spec.cluster_name
                )
                if success:
                    logger.info("✅ Instance cleanup completed successfully")
                else:
                    logger.warning("⚠️ Instance cleanup may not have completed fully")

    def _test_instance_connectivity(self, instance_ip: str):
        """Test basic connectivity to instance."""
        logger.info(f"🧪 Testing connectivity to instance: {instance_ip}")

        # Test if port 22 (SSH) is open
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)

        try:
            result = sock.connect_ex((instance_ip, 22))
            if result == 0:
                logger.info("✅ SSH port is accessible")
            else:
                logger.warning(
                    "⚠️ SSH port is not immediately accessible (may still be starting)"
                )
        finally:
            sock.close()

    def test_ssh_job_execution(self, provisioner, cluster_spec):
        """Test SSH-based job execution on Lambda Cloud instances."""
        logger.info(
            f"🧪 Testing SSH job execution on Lambda Cloud instances: {cluster_spec.cluster_name}"
        )

        cluster_info = None
        try:
            # Provision the instances
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)

            # Wait for instances to be fully ready
            max_wait = 300  # 5 minutes
            wait_start = time.time()

            while time.time() - wait_start < max_wait:
                status = provisioner.get_cluster_status(cluster_spec.cluster_name)
                if status["ready_for_jobs"]:
                    break
                time.sleep(10)
            else:
                pytest.fail("Instances did not become ready for job execution")

            logger.info("✅ Instances are ready, testing job execution setup")

            # Verify kubectl config is properly formatted
            kubectl_config = cluster_info["kubectl_config"]
            assert "clusters" in kubectl_config
            assert "contexts" in kubectl_config
            assert "users" in kubectl_config
            assert kubectl_config["kind"] == "Config"

            # Verify instance has job server running (would be started in setup)
            instance = cluster_info["instances"][0]
            instance_ip = instance["ip"]

            # Test if the job server port is accessible
            self._test_job_server_connectivity(instance_ip)

            logger.info("✅ SSH job execution setup verified")

        finally:
            # Clean up
            if cluster_info:
                logger.info("🧹 Cleaning up Lambda Cloud instances after job test")
                provisioner.destroy_cluster_infrastructure(cluster_spec.cluster_name)

    def _test_job_server_connectivity(self, instance_ip: str):
        """Test connectivity to job server on instance."""
        logger.info(f"🧪 Testing job server connectivity on {instance_ip}:8080")

        # Test if port 8080 (job server) is open
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)

        try:
            result = sock.connect_ex((instance_ip, 8080))
            if result == 0:
                logger.info("✅ Job server port is accessible")
            else:
                logger.warning(
                    "⚠️ Job server port is not immediately accessible (may still be starting)"
                )
        finally:
            sock.close()

    def test_instance_type_selection(self, provisioner):
        """Test that appropriate instance types are selected."""
        logger.info("🧪 Testing Lambda Cloud instance type selection")

        # Test the instance type mapping logic
        spec = ClusterSpec(
            cluster_name="test-instance-type",
            provider="lambda",
            node_count=1,
            kubernetes_version="1.28",
            region="us-west-2",
        )

        # This should select a GPU instance type (preferred for Lambda Cloud)
        instance_type = provisioner._map_node_requirements_to_instance_type(spec)

        # Verify it's a valid instance type (will be from available types)
        assert instance_type is not None, "Should select a valid instance type"
        assert isinstance(instance_type, str), "Instance type should be a string"

        logger.info(f"✅ Selected instance type: {instance_type}")

    def test_ssh_key_management(self, provisioner, cluster_spec):
        """Test SSH key creation and management."""
        logger.info(f"🧪 Testing SSH key management: {cluster_spec.cluster_name}")

        cluster_info = None
        try:
            # Provision instances (which creates SSH keys)
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)

            # Verify SSH key was created and tracked
            created_resources = cluster_info["created_resources"]
            assert "ssh_keys" in created_resources
            assert len(created_resources["ssh_keys"]) > 0

            ssh_key_name = created_resources["ssh_keys"][0]
            assert ssh_key_name.startswith(
                "clustrix-"
            ), "SSH key should have clustrix prefix"

            logger.info(f"✅ SSH key created and tracked: {ssh_key_name}")

        finally:
            # Clean up
            if cluster_info:
                logger.info("🧹 Cleaning up SSH keys")
                provisioner.destroy_cluster_infrastructure(cluster_spec.cluster_name)

    def test_error_handling(self, lambda_credentials):
        """Test error handling with invalid configurations."""
        logger.info("🧪 Testing Lambda Cloud error handling")

        # Test with invalid credentials
        invalid_provisioner = LambdaCloudKubernetesProvisioner(
            credentials={"api_key": "invalid-key"}, region="us-west-2"
        )

        result = invalid_provisioner.validate_credentials()
        assert result is False, "Invalid credentials should fail validation"

        # Test with missing credentials
        with pytest.raises(ValueError, match="Lambda Cloud API key required"):
            LambdaCloudKubernetesProvisioner(credentials={}, region="us-west-2")

        logger.info("✅ Lambda Cloud error handling working correctly")

    def test_instance_status_monitoring(self, provisioner, cluster_spec):
        """Test instance status monitoring capabilities."""
        logger.info(
            f"🧪 Testing Lambda Cloud instance status monitoring: {cluster_spec.cluster_name}"
        )

        # Test status of non-existent cluster
        status = provisioner.get_cluster_status("non-existent-cluster")
        assert status["status"] == "NOT_FOUND"
        assert status["ready_for_jobs"] is False

        cluster_info = None
        try:
            # Create instances and monitor their status
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)

            # Monitor status during startup
            startup_statuses = []
            max_wait = 180  # 3 minutes for startup monitoring
            wait_start = time.time()

            while time.time() - wait_start < max_wait:
                status = provisioner.get_cluster_status(cluster_spec.cluster_name)
                startup_statuses.append(status["status"])

                if status["ready_for_jobs"]:
                    break
                time.sleep(10)

            # Verify we captured the status progression
            assert len(startup_statuses) > 0, "Should have captured startup statuses"
            assert "ACTIVE" in startup_statuses, "Should eventually reach ACTIVE status"

            logger.info(
                f"✅ Status monitoring captured progression: {set(startup_statuses)}"
            )

        finally:
            # Clean up
            if cluster_info:
                provisioner.destroy_cluster_infrastructure(cluster_spec.cluster_name)

    def test_concurrent_instance_operations(self, provisioner, lambda_credentials):
        """Test handling of concurrent instance operations."""
        logger.info("🧪 Testing concurrent Lambda Cloud instance operations")

        import concurrent.futures

        test_id = int(time.time())
        cluster_specs = [
            ClusterSpec(
                cluster_name=f"test-concurrent-{test_id}-{i}",
                provider="lambda",
                node_count=1,
                kubernetes_version="1.28",
                region="us-west-2",
            )
            for i in range(2)  # Test with 2 concurrent clusters
        ]

        created_clusters = []

        def create_cluster(spec):
            """Helper function to create a cluster."""
            try:
                provisioner_instance = LambdaCloudKubernetesProvisioner(
                    credentials=lambda_credentials, region="us-west-2"
                )
                cluster_info = provisioner_instance.provision_complete_infrastructure(
                    spec
                )
                return cluster_info
            except Exception as e:
                logger.error(f"Failed to create cluster {spec.cluster_name}: {e}")
                return None

        try:
            # Create clusters concurrently
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(create_cluster, spec) for spec in cluster_specs
                ]
                results = [
                    future.result(timeout=600) for future in futures
                ]  # 10 min timeout

            # Verify results
            successful_clusters = [r for r in results if r is not None]
            created_clusters = successful_clusters

            # We expect at least one to succeed (Lambda Cloud might have capacity limits)
            assert (
                len(successful_clusters) >= 1
            ), "At least one concurrent cluster creation should succeed"

            logger.info(
                f"✅ Concurrent operations completed: {len(successful_clusters)}/{len(cluster_specs)} succeeded"
            )

        finally:
            # Clean up all created clusters
            for cluster_info in created_clusters:
                if cluster_info:
                    try:
                        provisioner.destroy_cluster_infrastructure(
                            cluster_info["cluster_name"]
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to cleanup cluster {cluster_info['cluster_name']}: {e}"
                        )

    def test_instance_resource_cleanup(self, provisioner, cluster_spec):
        """Test thorough resource cleanup after instance operations."""
        logger.info(
            f"🧪 Testing Lambda Cloud instance resource cleanup: {cluster_spec.cluster_name}"
        )

        # Create instances
        cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)

        # Verify instances exist
        status = provisioner.get_cluster_status(cluster_spec.cluster_name)
        assert status["status"] != "NOT_FOUND", "Cluster should exist after creation"

        # Get resource info for verification
        created_resources = cluster_info["created_resources"]
        instance_ids = created_resources.get("instances", [])
        ssh_keys = created_resources.get("ssh_keys", [])

        assert len(instance_ids) > 0, "Should have created instances"
        assert len(ssh_keys) > 0, "Should have created SSH keys"

        # Clean up
        success = provisioner.destroy_cluster_infrastructure(cluster_spec.cluster_name)
        assert success is True, "Cleanup should succeed"

        # Verify instances are gone (with some delay for API consistency)
        time.sleep(15)  # Wait for cleanup to propagate
        final_status = provisioner.get_cluster_status(cluster_spec.cluster_name)
        assert (
            final_status["status"] == "NOT_FOUND"
        ), "Cluster should be gone after cleanup"

        logger.info("✅ Lambda Cloud instance resource cleanup verified")

    @pytest.mark.performance
    def test_instance_provisioning_performance(self, provisioner, cluster_spec):
        """Test and benchmark instance provisioning performance."""
        logger.info(
            f"🧪 Testing Lambda Cloud instance provisioning performance: {cluster_spec.cluster_name}"
        )

        cluster_info = None
        try:
            # Measure provisioning time
            start_time = time.time()
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)
            provision_time = time.time() - start_time

            # Measure time to ready state
            ready_start = time.time()
            max_wait = 300  # 5 minutes

            while time.time() - ready_start < max_wait:
                status = provisioner.get_cluster_status(cluster_spec.cluster_name)
                if status["ready_for_jobs"]:
                    break
                time.sleep(10)
            else:
                pytest.fail("Instances did not become ready within performance timeout")

            ready_time = time.time() - ready_start
            total_time = provision_time + ready_time

            # Log performance metrics
            logger.info(f"📊 Lambda Cloud Instance Performance Metrics:")
            logger.info(f"   Provisioning time: {provision_time:.1f}s")
            logger.info(f"   Ready time: {ready_time:.1f}s")
            logger.info(f"   Total time: {total_time:.1f}s")

            # Performance assertions (reasonable expectations for Lambda Cloud)
            assert (
                provision_time < 180
            ), f"Provisioning should complete within 3min (took {provision_time:.1f}s)"
            assert (
                total_time < 420
            ), f"Total setup should complete within 7min (took {total_time:.1f}s)"

            # Test cleanup performance
            cleanup_start = time.time()
            success = provisioner.destroy_cluster_infrastructure(
                cluster_spec.cluster_name
            )
            cleanup_time = time.time() - cleanup_start

            assert success is True, "Cleanup should succeed"
            assert (
                cleanup_time < 60
            ), f"Cleanup should complete within 60s (took {cleanup_time:.1f}s)"

            logger.info(f"   Cleanup time: {cleanup_time:.1f}s")
            logger.info("✅ Lambda Cloud instance performance benchmarking completed")

            cluster_info = None  # Prevent duplicate cleanup

        finally:
            # Ensure cleanup
            if cluster_info:
                provisioner.destroy_cluster_infrastructure(cluster_spec.cluster_name)

    def test_gpu_instance_availability(self, provisioner):
        """Test GPU instance availability and selection."""
        logger.info("🧪 Testing Lambda Cloud GPU instance availability")

        # This test checks if GPU instances are available and properly selected
        spec = ClusterSpec(
            cluster_name="test-gpu-availability",
            provider="lambda",
            node_count=1,
            kubernetes_version="1.28",
            region="us-west-2",
        )

        try:
            # Get available instance types
            import requests

            response = requests.get(
                f"{provisioner.base_url}/instance-types",
                headers=provisioner.headers,
                timeout=30,
            )
            response.raise_for_status()
            instance_types = response.json().get("data", {})

            # Check if GPU types are available
            gpu_types = [t for t in instance_types.keys() if "gpu" in t.lower()]

            if gpu_types:
                logger.info(f"✅ GPU instance types available: {gpu_types}")

                # Test instance type selection prefers GPU
                selected_type = provisioner._map_node_requirements_to_instance_type(
                    spec
                )
                if "gpu" in selected_type.lower():
                    logger.info(f"✅ GPU instance type selected: {selected_type}")
                else:
                    logger.info(f"ℹ️ Non-GPU instance type selected: {selected_type}")
            else:
                logger.info("ℹ️ No GPU instance types currently available")

        except Exception as e:
            logger.warning(f"⚠️ Could not check GPU availability: {e}")

    def test_network_security_setup(self, provisioner, cluster_spec):
        """Test network security and SSH setup."""
        logger.info(f"🧪 Testing network security setup: {cluster_spec.cluster_name}")

        cluster_info = None
        try:
            # Provision instances
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)

            # Verify SSH connectivity setup
            instances = cluster_info["instances"]
            ssh_key_info = cluster_info.get("created_resources", {}).get("ssh_keys", [])

            assert len(ssh_key_info) > 0, "Should have SSH keys for secure access"

            # Verify instances have public IPs for connectivity
            for instance in instances:
                assert "ip" in instance, "Instance should have IP address"
                assert instance["ip"] is not None, "Instance IP should not be None"

                # Basic IP format validation
                ip_parts = instance["ip"].split(".")
                assert len(ip_parts) == 4, "Should be valid IPv4 address format"

            logger.info("✅ Network security setup verified")

        finally:
            if cluster_info:
                provisioner.destroy_cluster_infrastructure(cluster_spec.cluster_name)
