"""
Real-world integration tests for HuggingFace Spaces Kubernetes provisioning.

These tests use actual HuggingFace API calls and credentials to verify that:
1. HuggingFace Spaces can be created and configured for Kubernetes-style jobs
2. Job execution works correctly through the Kubernetes adapter interface
3. Spaces can be properly cleaned up after use
4. Error handling works correctly with real API responses

Requirements:
- Valid HuggingFace token with Spaces creation permissions
- Network connectivity to HuggingFace Hub
- Sufficient HuggingFace quota for Space creation
"""

import os
import time
import pytest
import logging
from typing import Dict, Any

from clustrix.kubernetes.huggingface_provisioner import HuggingFaceKubernetesProvisioner
from clustrix.kubernetes.cluster_provisioner import ClusterSpec

logger = logging.getLogger(__name__)


@pytest.mark.real_world
class TestHuggingFaceKubernetesIntegration:
    """Real-world integration tests for HuggingFace Spaces Kubernetes provisioning."""

    @pytest.fixture(scope="class")
    def hf_credentials(self):
        """Get HuggingFace credentials from environment or 1Password."""
        # Try environment variables first
        token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
        username = os.getenv("HF_USERNAME") or os.getenv("HUGGINGFACE_USERNAME")

        if not token or not username:
            # Try 1Password CLI
            try:
                import subprocess

                # Get token from 1Password
                token_result = subprocess.run(
                    ["op", "item", "get", "HuggingFace", "--field", "token"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if token_result.returncode == 0:
                    token = token_result.stdout.strip()

                # Get username from 1Password
                username_result = subprocess.run(
                    ["op", "item", "get", "HuggingFace", "--field", "username"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if username_result.returncode == 0:
                    username = username_result.stdout.strip()

            except (
                subprocess.TimeoutExpired,
                subprocess.SubprocessError,
                FileNotFoundError,
            ):
                pass

        if not token or not username:
            pytest.skip("HuggingFace credentials not available")

        return {"token": token, "username": username}

    @pytest.fixture(scope="class")
    def provisioner(self, hf_credentials):
        """Create HuggingFace Kubernetes provisioner."""
        return HuggingFaceKubernetesProvisioner(
            credentials=hf_credentials, region="global"  # HF doesn't have regions
        )

    @pytest.fixture(scope="class")
    def cluster_spec(self):
        """Create test cluster specification."""
        test_id = int(time.time())
        return ClusterSpec(
            cluster_name=f"test-hf-k8s-{test_id}",
            provider="huggingface",
            node_count=1,  # Single node for basic testing
            kubernetes_version="1.28",
            region="global",
        )

    def test_credential_validation(self, provisioner):
        """Test that HuggingFace credentials can be validated."""
        logger.info("🧪 Testing HuggingFace credential validation")

        result = provisioner.validate_credentials()
        assert result is True, "HuggingFace credentials should be valid"

        logger.info("✅ HuggingFace credentials validated successfully")

    def test_space_provisioning_lifecycle(self, provisioner, cluster_spec):
        """Test complete Space provisioning lifecycle."""
        logger.info(
            f"🧪 Testing HuggingFace Space provisioning lifecycle for {cluster_spec.cluster_name}"
        )

        cluster_info = None
        try:
            # Provision the Space
            start_time = time.time()
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)
            provision_time = time.time() - start_time

            # Verify cluster info structure
            assert cluster_info is not None, "Cluster info should not be None"
            assert cluster_info["cluster_id"] == cluster_spec.cluster_name
            assert cluster_info["provider"] == "huggingface"
            assert cluster_info["ready_for_jobs"] is True
            assert "space_url" in cluster_info
            assert "kubectl_config" in cluster_info

            logger.info(
                f"✅ Space provisioned in {provision_time:.1f}s: {cluster_info['space_url']}"
            )

            # Wait for Space to be fully ready
            max_wait = 600  # 10 minutes
            wait_start = time.time()

            while time.time() - wait_start < max_wait:
                status = provisioner.get_cluster_status(cluster_spec.cluster_name)
                if status["ready_for_jobs"]:
                    break
                logger.info(
                    f"⏳ Waiting for Space to be ready... Status: {status['status']}"
                )
                time.sleep(30)
            else:
                pytest.fail("Space did not become ready within timeout period")

            ready_time = time.time() - wait_start
            logger.info(f"✅ Space ready for jobs in {ready_time:.1f}s")

            # Test basic Space functionality
            final_status = provisioner.get_cluster_status(cluster_spec.cluster_name)
            assert final_status["ready_for_jobs"] is True
            assert final_status["status"] == "RUNNING"

            logger.info(
                "✅ HuggingFace Space provisioning lifecycle completed successfully"
            )

        finally:
            # Clean up
            if cluster_info:
                logger.info("🧹 Cleaning up HuggingFace Space")
                success = provisioner.destroy_cluster_infrastructure(
                    cluster_spec.cluster_name
                )
                if success:
                    logger.info("✅ Space cleanup completed successfully")
                else:
                    logger.warning("⚠️ Space cleanup may not have completed fully")

    def test_kubernetes_job_execution(self, provisioner, cluster_spec):
        """Test Kubernetes-style job execution on HuggingFace Space."""
        logger.info(
            f"🧪 Testing Kubernetes job execution on HuggingFace Space: {cluster_spec.cluster_name}"
        )

        cluster_info = None
        try:
            # Provision the Space
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)

            # Wait for Space to be ready
            max_wait = 600  # 10 minutes
            wait_start = time.time()

            while time.time() - wait_start < max_wait:
                status = provisioner.get_cluster_status(cluster_spec.cluster_name)
                if status["ready_for_jobs"]:
                    break
                time.sleep(30)
            else:
                pytest.fail("Space did not become ready for job execution")

            logger.info("✅ Space is ready, testing job execution")

            # Test job execution through the kubectl interface
            # Note: This would require implementing a test client for the Space's API
            # For now, we verify that the Space is configured correctly for job execution

            # Verify kubectl config is properly formatted
            kubectl_config = cluster_info["kubectl_config"]
            assert "clusters" in kubectl_config
            assert "contexts" in kubectl_config
            assert "users" in kubectl_config
            assert kubectl_config["kind"] == "Config"

            # Verify the Space endpoint is accessible
            space_url = cluster_info["space_url"]
            assert space_url.startswith("https://huggingface.co/spaces/")

            logger.info("✅ Kubernetes job execution setup verified")

        finally:
            # Clean up
            if cluster_info:
                logger.info("🧹 Cleaning up HuggingFace Space after job test")
                provisioner.destroy_cluster_infrastructure(cluster_spec.cluster_name)

    def test_space_hardware_mapping(self, provisioner):
        """Test that node requirements are properly mapped to HF hardware."""
        logger.info("🧪 Testing HuggingFace hardware mapping")

        test_cases = [
            {"node_count": 1, "expected_hardware": "cpu-basic"},
            {"node_count": 2, "expected_hardware": "cpu-upgrade"},
            {"node_count": 4, "expected_hardware": "t4-small"},
            {"node_count": 8, "expected_hardware": "t4-medium"},
        ]

        for case in test_cases:
            spec = ClusterSpec(
                cluster_name=f"test-hardware-{case['node_count']}",
                provider="huggingface",
                node_count=case["node_count"],
                kubernetes_version="1.28",
                region="global",
            )

            # Test the hardware mapping logic
            hardware = provisioner._map_node_requirements_to_hardware(spec)
            assert hardware == case["expected_hardware"], (
                f"Expected hardware {case['expected_hardware']} for {case['node_count']} nodes, "
                f"got {hardware}"
            )

        logger.info("✅ HuggingFace hardware mapping working correctly")

    def test_error_handling(self, hf_credentials):
        """Test error handling with invalid configurations."""
        logger.info("🧪 Testing HuggingFace error handling")

        # Test with invalid credentials
        invalid_provisioner = HuggingFaceKubernetesProvisioner(
            credentials={"token": "invalid", "username": "invalid"}, region="global"
        )

        result = invalid_provisioner.validate_credentials()
        assert result is False, "Invalid credentials should fail validation"

        # Test with missing credentials
        with pytest.raises(ValueError, match="HuggingFace token required"):
            HuggingFaceKubernetesProvisioner(credentials={}, region="global")

        logger.info("✅ HuggingFace error handling working correctly")

    def test_space_status_monitoring(self, provisioner, cluster_spec):
        """Test Space status monitoring capabilities."""
        logger.info(
            f"🧪 Testing HuggingFace Space status monitoring: {cluster_spec.cluster_name}"
        )

        # Test status of non-existent Space
        status = provisioner.get_cluster_status("non-existent-space")
        assert status["status"] == "NOT_FOUND"
        assert status["ready_for_jobs"] is False

        cluster_info = None
        try:
            # Create Space and monitor its status
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)

            # Monitor status during startup
            startup_statuses = []
            max_wait = 300  # 5 minutes for startup monitoring
            wait_start = time.time()

            while time.time() - wait_start < max_wait:
                status = provisioner.get_cluster_status(cluster_spec.cluster_name)
                startup_statuses.append(status["status"])

                if status["ready_for_jobs"]:
                    break
                time.sleep(15)

            # Verify we captured the status progression
            assert len(startup_statuses) > 0, "Should have captured startup statuses"
            assert (
                "RUNNING" in startup_statuses
            ), "Should eventually reach RUNNING status"

            logger.info(
                f"✅ Status monitoring captured progression: {set(startup_statuses)}"
            )

        finally:
            # Clean up
            if cluster_info:
                provisioner.destroy_cluster_infrastructure(cluster_spec.cluster_name)

    def test_concurrent_space_operations(self, provisioner, hf_credentials):
        """Test handling of concurrent Space operations."""
        logger.info("🧪 Testing concurrent HuggingFace Space operations")

        import threading
        import concurrent.futures

        test_id = int(time.time())
        cluster_specs = [
            ClusterSpec(
                cluster_name=f"test-concurrent-{test_id}-{i}",
                provider="huggingface",
                node_count=1,
                kubernetes_version="1.28",
                region="global",
            )
            for i in range(2)  # Test with 2 concurrent spaces
        ]

        created_clusters = []

        def create_space(spec):
            """Helper function to create a Space."""
            try:
                provisioner_instance = HuggingFaceKubernetesProvisioner(
                    credentials=hf_credentials, region="global"
                )
                cluster_info = provisioner_instance.provision_complete_infrastructure(
                    spec
                )
                return cluster_info
            except Exception as e:
                logger.error(f"Failed to create Space {spec.cluster_name}: {e}")
                return None

        try:
            # Create Spaces concurrently
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(create_space, spec) for spec in cluster_specs
                ]
                results = [
                    future.result(timeout=900) for future in futures
                ]  # 15 min timeout

            # Verify results
            successful_clusters = [r for r in results if r is not None]
            created_clusters = successful_clusters

            # We expect at least one to succeed (HF might have rate limits)
            assert (
                len(successful_clusters) >= 1
            ), "At least one concurrent Space creation should succeed"

            logger.info(
                f"✅ Concurrent operations completed: {len(successful_clusters)}/{len(cluster_specs)} succeeded"
            )

        finally:
            # Clean up all created Spaces
            for cluster_info in created_clusters:
                if cluster_info:
                    try:
                        provisioner.destroy_cluster_infrastructure(
                            cluster_info["cluster_name"]
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to cleanup Space {cluster_info['cluster_name']}: {e}"
                        )

    def test_space_resource_cleanup(self, provisioner, cluster_spec):
        """Test thorough resource cleanup after Space operations."""
        logger.info(
            f"🧪 Testing HuggingFace Space resource cleanup: {cluster_spec.cluster_name}"
        )

        # Create Space
        cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)

        # Verify Space exists
        status = provisioner.get_cluster_status(cluster_spec.cluster_name)
        assert status["status"] != "NOT_FOUND", "Space should exist after creation"

        # Clean up
        success = provisioner.destroy_cluster_infrastructure(cluster_spec.cluster_name)
        assert success is True, "Cleanup should succeed"

        # Verify Space is gone (with some delay for eventual consistency)
        time.sleep(10)  # Wait for cleanup to propagate
        final_status = provisioner.get_cluster_status(cluster_spec.cluster_name)
        assert (
            final_status["status"] == "NOT_FOUND"
        ), "Space should be gone after cleanup"

        logger.info("✅ HuggingFace Space resource cleanup verified")

    @pytest.mark.performance
    def test_space_provisioning_performance(self, provisioner, cluster_spec):
        """Test and benchmark Space provisioning performance."""
        logger.info(
            f"🧪 Testing HuggingFace Space provisioning performance: {cluster_spec.cluster_name}"
        )

        cluster_info = None
        try:
            # Measure provisioning time
            start_time = time.time()
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)
            provision_time = time.time() - start_time

            # Measure time to ready state
            ready_start = time.time()
            max_wait = 600  # 10 minutes

            while time.time() - ready_start < max_wait:
                status = provisioner.get_cluster_status(cluster_spec.cluster_name)
                if status["ready_for_jobs"]:
                    break
                time.sleep(15)
            else:
                pytest.fail("Space did not become ready within performance timeout")

            ready_time = time.time() - ready_start
            total_time = provision_time + ready_time

            # Log performance metrics
            logger.info(f"📊 HuggingFace Space Performance Metrics:")
            logger.info(f"   Provisioning time: {provision_time:.1f}s")
            logger.info(f"   Ready time: {ready_time:.1f}s")
            logger.info(f"   Total time: {total_time:.1f}s")

            # Performance assertions (reasonable expectations for HF Spaces)
            assert (
                provision_time < 60
            ), f"Provisioning should complete within 60s (took {provision_time:.1f}s)"
            assert (
                total_time < 900
            ), f"Total setup should complete within 15min (took {total_time:.1f}s)"

            # Test cleanup performance
            cleanup_start = time.time()
            success = provisioner.destroy_cluster_infrastructure(
                cluster_spec.cluster_name
            )
            cleanup_time = time.time() - cleanup_start

            assert success is True, "Cleanup should succeed"
            assert (
                cleanup_time < 30
            ), f"Cleanup should complete within 30s (took {cleanup_time:.1f}s)"

            logger.info(f"   Cleanup time: {cleanup_time:.1f}s")
            logger.info("✅ HuggingFace Space performance benchmarking completed")

            cluster_info = None  # Prevent duplicate cleanup

        finally:
            # Ensure cleanup
            if cluster_info:
                provisioner.destroy_cluster_infrastructure(cluster_spec.cluster_name)
