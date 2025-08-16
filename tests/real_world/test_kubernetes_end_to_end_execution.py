"""
End-to-end integration tests for Kubernetes cluster provisioning with real job execution.

These tests verify the complete workflow from cluster auto-provisioning
through job execution to cleanup using the @cluster decorator.

Requirements:
- Valid credentials for at least one cloud provider
- Network connectivity to provider APIs
- Sufficient quota for cluster/instance creation
- Real function execution capabilities

NOTE: These are expensive tests that create actual cloud infrastructure.
They should be run manually or in special CI environments only.
"""

import os
import time
import pytest
import logging
import tempfile
from typing import Dict, Any, List

from clustrix import cluster
from clustrix.config import ClusterConfig, get_config
from clustrix.credential_manager import get_credential_manager

logger = logging.getLogger(__name__)


@pytest.mark.real_world
@pytest.mark.slow
class TestKubernetesEndToEndExecution:
    """End-to-end tests for Kubernetes auto-provisioning with real job execution."""

    @pytest.fixture(scope="class")
    def available_providers(self):
        """Get list of providers with valid credentials for testing."""
        credential_manager = get_credential_manager()
        providers = ["huggingface", "lambda"]  # Start with faster providers

        available = []
        for provider in providers:
            try:
                creds = credential_manager.ensure_kubernetes_provider_credentials(
                    provider
                )
                if creds:
                    available.append(provider)
                    logger.info(f"✅ {provider} credentials available for testing")
                else:
                    logger.info(f"⏭️ {provider} credentials not available")
            except Exception as e:
                logger.info(f"⏭️ {provider} credentials error: {e}")

        if not available:
            pytest.skip(
                "No cloud provider credentials available for end-to-end testing"
            )

        return available

    @pytest.fixture(scope="function")
    def test_cluster_config(self):
        """Create test cluster configuration."""
        test_id = int(time.time())

        config = ClusterConfig()
        config.cluster_type = "kubernetes"
        config.auto_provision_k8s = True
        config.k8s_from_scratch = True
        config.k8s_node_count = 1
        config.k8s_cleanup_on_exit = True  # Always cleanup
        config.k8s_cluster_name = f"test-e2e-{test_id}"

        return config

    def test_simple_function_execution(self, available_providers, test_cluster_config):
        """Test execution of a simple Python function on auto-provisioned cluster."""
        provider = available_providers[0]  # Use first available provider
        logger.info(f"🧪 Testing simple function execution on {provider}")

        # Configure for this provider
        test_cluster_config.k8s_provider = provider
        test_cluster_config.k8s_region = (
            "us-west-2" if provider == "lambda" else "global"
        )

        # Override global config for this test
        original_config = get_config()._config
        get_config()._config = test_cluster_config

        try:
            # Define test function with @cluster decorator
            @cluster(
                platform="kubernetes",
                auto_provision=True,
                node_count=1,
                cluster_name=test_cluster_config.k8s_cluster_name,
            )
            def simple_computation(x: int, y: int) -> Dict[str, Any]:
                """Simple computation function for testing."""
                import platform
                import os

                result = x * y + 42

                return {
                    "result": result,
                    "platform": platform.platform(),
                    "python_version": platform.python_version(),
                    "environment_variables": dict(os.environ),
                    "computed_at": time.time(),
                }

            # Execute function - this should trigger cluster provisioning
            logger.info("🚀 Starting function execution (will auto-provision cluster)")
            start_time = time.time()

            result = simple_computation(5, 10)
            execution_time = time.time() - start_time

            # Verify results
            assert isinstance(result, dict), "Result should be a dictionary"
            assert result["result"] == 92, f"Expected 92, got {result['result']}"
            assert "platform" in result, "Platform info should be included"
            assert "python_version" in result, "Python version should be included"

            logger.info(f"✅ Function executed successfully in {execution_time:.1f}s")
            logger.info(f"📊 Result: {result['result']}")
            logger.info(f"🖥️ Remote platform: {result['platform']}")

        finally:
            # Restore original config
            get_config()._config = original_config

    def test_loop_parallelization_execution(
        self, available_providers, test_cluster_config
    ):
        """Test loop parallelization on auto-provisioned cluster."""
        provider = available_providers[0]
        logger.info(f"🧪 Testing loop parallelization on {provider}")

        # Configure for this provider
        test_cluster_config.k8s_provider = provider
        test_cluster_config.k8s_region = (
            "us-west-2" if provider == "lambda" else "global"
        )
        test_cluster_config.k8s_cluster_name = f"test-loop-{int(time.time())}"

        # Override global config
        original_config = get_config()._config
        get_config()._config = test_cluster_config

        try:

            @cluster(
                platform="kubernetes",
                auto_provision=True,
                parallel=True,  # Enable loop parallelization
                node_count=1,
                cluster_name=test_cluster_config.k8s_cluster_name,
            )
            def parallel_computation(numbers: List[int]) -> List[Dict[str, Any]]:
                """Function with parallelizable loop for testing."""
                results = []

                for num in numbers:  # This loop should be parallelized
                    import time
                    import os

                    # Simulate some work
                    time.sleep(0.1)
                    processed = num**2 + num

                    results.append(
                        {
                            "input": num,
                            "result": processed,
                            "worker_pid": os.getpid(),
                            "processed_at": time.time(),
                        }
                    )

                return results

            # Execute with list that should be parallelized
            test_numbers = [1, 2, 3, 4, 5]
            logger.info("🚀 Starting parallel computation")
            start_time = time.time()

            results = parallel_computation(test_numbers)
            execution_time = time.time() - start_time

            # Verify results
            assert isinstance(results, list), "Results should be a list"
            assert len(results) == len(test_numbers), "Should process all numbers"

            for i, result in enumerate(results):
                expected = test_numbers[i] ** 2 + test_numbers[i]
                assert (
                    result["result"] == expected
                ), f"Incorrect result for {test_numbers[i]}"
                assert result["input"] == test_numbers[i], "Input should be preserved"

            logger.info(f"✅ Parallel computation completed in {execution_time:.1f}s")
            logger.info(f"📊 Processed {len(results)} items")

        finally:
            # Restore original config
            get_config()._config = original_config

    def test_data_processing_workflow(self, available_providers, test_cluster_config):
        """Test realistic data processing workflow on auto-provisioned cluster."""
        provider = available_providers[0]
        logger.info(f"🧪 Testing data processing workflow on {provider}")

        # Configure for this provider
        test_cluster_config.k8s_provider = provider
        test_cluster_config.k8s_region = (
            "us-west-2" if provider == "lambda" else "global"
        )
        test_cluster_config.k8s_cluster_name = f"test-workflow-{int(time.time())}"

        # Override global config
        original_config = get_config()._config
        get_config()._config = test_cluster_config

        try:

            @cluster(
                platform="kubernetes",
                auto_provision=True,
                cores=1,
                memory="2GB",
                cluster_name=test_cluster_config.k8s_cluster_name,
            )
            def data_processing_workflow(dataset_size: int) -> Dict[str, Any]:
                """Realistic data processing function for testing."""
                import numpy as np
                import time
                import json

                start_time = time.time()

                # Generate synthetic dataset
                logger.info(f"Generating dataset of size {dataset_size}")
                data = np.random.rand(dataset_size, 10)  # Random data matrix

                # Process data (simulate real ML workflow)
                logger.info("Processing data...")

                # Step 1: Normalization
                normalized = (data - np.mean(data, axis=0)) / np.std(data, axis=0)

                # Step 2: Feature extraction
                features = {
                    "mean": np.mean(normalized, axis=0).tolist(),
                    "std": np.std(normalized, axis=0).tolist(),
                    "min": np.min(normalized, axis=0).tolist(),
                    "max": np.max(normalized, axis=0).tolist(),
                }

                # Step 3: Simple analysis
                correlation_matrix = np.corrcoef(normalized.T).tolist()

                processing_time = time.time() - start_time

                return {
                    "dataset_size": dataset_size,
                    "features": features,
                    "correlation_matrix": correlation_matrix,
                    "processing_time": processing_time,
                    "numpy_version": np.__version__,
                    "status": "completed",
                }

            # Execute workflow
            logger.info("🚀 Starting data processing workflow")
            start_time = time.time()

            result = data_processing_workflow(1000)  # Process 1000 samples
            execution_time = time.time() - start_time

            # Verify results
            assert isinstance(result, dict), "Result should be a dictionary"
            assert (
                result["status"] == "completed"
            ), "Workflow should complete successfully"
            assert result["dataset_size"] == 1000, "Dataset size should be preserved"
            assert "features" in result, "Features should be extracted"
            assert (
                "correlation_matrix" in result
            ), "Correlation matrix should be computed"
            assert len(result["features"]["mean"]) == 10, "Should have 10 feature means"

            logger.info(
                f"✅ Data processing workflow completed in {execution_time:.1f}s"
            )
            logger.info(f"⚡ Remote processing time: {result['processing_time']:.1f}s")
            logger.info(f"📊 Processed {result['dataset_size']} samples")

        finally:
            # Restore original config
            get_config()._config = original_config

    def test_error_handling_and_cleanup(self, available_providers, test_cluster_config):
        """Test error handling and proper cluster cleanup."""
        provider = available_providers[0]
        logger.info(f"🧪 Testing error handling and cleanup on {provider}")

        # Configure for this provider
        test_cluster_config.k8s_provider = provider
        test_cluster_config.k8s_region = (
            "us-west-2" if provider == "lambda" else "global"
        )
        test_cluster_config.k8s_cluster_name = f"test-error-{int(time.time())}"

        # Override global config
        original_config = get_config()._config
        get_config()._config = test_cluster_config

        try:

            @cluster(
                platform="kubernetes",
                auto_provision=True,
                cluster_name=test_cluster_config.k8s_cluster_name,
            )
            def failing_function(should_fail: bool) -> str:
                """Function that can be made to fail for testing error handling."""
                if should_fail:
                    raise ValueError("Intentional test failure")
                return "success"

            # First test successful execution
            logger.info("🚀 Testing successful execution first")
            result = failing_function(False)
            assert result == "success", "Successful execution should return 'success'"

            # Then test error handling
            logger.info("🚀 Testing error handling")
            with pytest.raises(Exception) as exc_info:
                failing_function(True)

            # Verify the error was properly propagated
            assert "Intentional test failure" in str(
                exc_info.value
            ) or "ValueError" in str(type(exc_info.value))

            logger.info("✅ Error handling working correctly")

        finally:
            # Restore original config
            get_config()._config = original_config

    @pytest.mark.slow
    def test_multi_provider_execution(self, available_providers, test_cluster_config):
        """Test execution across multiple providers if available."""
        if len(available_providers) < 2:
            pytest.skip("Need at least 2 providers for multi-provider testing")

        logger.info(f"🧪 Testing multi-provider execution: {available_providers}")

        results = {}

        for provider in available_providers[:2]:  # Test first 2 providers
            logger.info(f"🔄 Testing provider: {provider}")

            # Configure for this provider
            provider_config = ClusterConfig()
            provider_config.cluster_type = "kubernetes"
            provider_config.auto_provision_k8s = True
            provider_config.k8s_from_scratch = True
            provider_config.k8s_node_count = 1
            provider_config.k8s_cleanup_on_exit = True
            provider_config.k8s_provider = provider
            provider_config.k8s_region = (
                "us-west-2" if provider == "lambda" else "global"
            )
            provider_config.k8s_cluster_name = (
                f"test-multi-{provider}-{int(time.time())}"
            )

            # Override global config
            original_config = get_config()._config
            get_config()._config = provider_config

            try:

                @cluster(
                    platform="kubernetes",
                    auto_provision=True,
                    cluster_name=provider_config.k8s_cluster_name,
                )
                def provider_test_function(provider_name: str) -> Dict[str, Any]:
                    """Test function for multi-provider execution."""
                    import platform
                    import time

                    return {
                        "provider": provider_name,
                        "platform": platform.platform(),
                        "execution_time": time.time(),
                        "test": "multi_provider_success",
                    }

                # Execute on this provider
                start_time = time.time()
                result = provider_test_function(provider)
                execution_time = time.time() - start_time

                results[provider] = {
                    "result": result,
                    "execution_time": execution_time,
                    "success": True,
                }

                logger.info(
                    f"✅ {provider} execution completed in {execution_time:.1f}s"
                )

            except Exception as e:
                logger.error(f"❌ {provider} execution failed: {e}")
                results[provider] = {
                    "result": None,
                    "execution_time": None,
                    "success": False,
                    "error": str(e),
                }
            finally:
                # Restore original config
                get_config()._config = original_config

        # Verify results
        successful_providers = [p for p, r in results.items() if r["success"]]
        assert len(successful_providers) >= 1, "At least one provider should succeed"

        logger.info(f"✅ Multi-provider testing completed")
        logger.info(f"📊 Successful providers: {successful_providers}")
        for provider, result in results.items():
            if result["success"]:
                logger.info(f"   {provider}: {result['execution_time']:.1f}s")
            else:
                logger.info(f"   {provider}: Failed - {result.get('error', 'Unknown')}")
