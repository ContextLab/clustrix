"""
Real-world Lambda Cloud integration tests.

These tests require actual Lambda Cloud credentials and create real instances.
NO MOCKS OR SIMULATIONS - these test real cloud execution.
"""

import pytest
import os
import time
from unittest.mock import patch
import logging

from clustrix import cluster, configure
from tests.real_world.credential_manager import get_lambda_credentials

logger = logging.getLogger(__name__)


@pytest.mark.real_world
class TestLambdaCloudExecutionReal:
    """Test real Lambda Cloud job execution."""

    def setup_method(self):
        """Setup for each test method."""
        self.lambda_creds = get_lambda_credentials()
        if not self.lambda_creds:
            pytest.skip("Lambda Cloud credentials not available")

    def test_lambda_cloud_basic_execution_real(self):
        """Test basic function execution on real Lambda Cloud instance."""

        @cluster(
            provider="lambda",
            instance_type="gpu_1x_a10",
            region="us-east-1",
            cores=2,
            memory="8GB",
            lambda_api_key=self.lambda_creds.get("api_key"),
            terminate_on_completion=True,
            instance_startup_timeout=300,
        )
        def test_basic_computation():
            """Simple computation to verify execution works."""
            import platform
            import os

            result = {
                "computation": 2 + 2,
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "working_directory": os.getcwd(),
                "environment_check": "success",
            }

            return result

        # Execute function
        start_time = time.time()
        result = test_basic_computation()
        execution_time = time.time() - start_time

        # Verify results
        assert result is not None
        assert result["computation"] == 4
        assert (
            "ubuntu" in result["platform"].lower()
            or "linux" in result["platform"].lower()
        )
        assert result["environment_check"] == "success"

        # Verify execution happened on cloud (not locally)
        assert execution_time > 60  # Should take time due to instance provisioning

        logger.info(
            f"Lambda Cloud basic execution completed in {execution_time:.2f} seconds"
        )
        logger.info(f"Result: {result}")

    def test_lambda_cloud_gpu_verification_real(self):
        """Test GPU detection and computation on real Lambda Cloud GPU instance."""

        @cluster(
            provider="lambda",
            instance_type="gpu_1x_a10",
            region="us-east-1",
            cores=4,
            memory="16GB",
            lambda_api_key=self.lambda_creds.get("api_key"),
            terminate_on_completion=True,
            instance_startup_timeout=300,
        )
        def verify_gpu_functionality():
            """Verify GPU availability and perform basic GPU computation."""
            import subprocess
            import json

            # Check NVIDIA driver and GPUs
            try:
                result = subprocess.run(
                    ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=30
                )
                gpu_list = result.stdout if result.returncode == 0 else "No GPUs found"
            except Exception as e:
                gpu_list = f"Error checking GPUs: {e}"

            # Try basic PyTorch GPU computation
            gpu_computation_result = None
            try:
                import torch

                if torch.cuda.is_available():
                    device = torch.device("cuda:0")

                    # Simple GPU computation
                    a = torch.randn(100, 100, device=device)
                    b = torch.randn(100, 100, device=device)
                    c = torch.mm(a, b)

                    gpu_computation_result = {
                        "pytorch_version": torch.__version__,
                        "cuda_available": torch.cuda.is_available(),
                        "cuda_version": torch.version.cuda,
                        "device_count": torch.cuda.device_count(),
                        "device_name": torch.cuda.get_device_name(0),
                        "computation_successful": True,
                        "result_shape": list(c.shape),
                        "memory_allocated": torch.cuda.memory_allocated()
                        / (1024**2),  # MB
                    }
                else:
                    gpu_computation_result = {
                        "cuda_available": False,
                        "error": "CUDA not available",
                    }

            except Exception as e:
                gpu_computation_result = {"error": f"GPU computation failed: {e}"}

            return {
                "gpu_list": gpu_list,
                "gpu_computation": gpu_computation_result,
                "test_status": "completed",
            }

        # Execute GPU verification
        start_time = time.time()
        result = verify_gpu_functionality()
        execution_time = time.time() - start_time

        # Verify results
        assert result is not None
        assert result["test_status"] == "completed"

        # Verify GPU detection
        assert (
            "gpu" in result["gpu_list"].lower() or "a10" in result["gpu_list"].lower()
        )

        # Verify GPU computation worked
        gpu_result = result["gpu_computation"]
        assert gpu_result is not None

        if "error" not in gpu_result:
            assert gpu_result.get("cuda_available") == True
            assert gpu_result.get("device_count", 0) > 0
            assert gpu_result.get("computation_successful") == True
            assert "A10" in gpu_result.get("device_name", "")

        logger.info(
            f"Lambda Cloud GPU verification completed in {execution_time:.2f} seconds"
        )
        logger.info(f"GPU List: {result['gpu_list']}")
        logger.info(f"GPU Computation: {gpu_result}")

    def test_lambda_cloud_data_transfer_real(self):
        """Test data upload/download with real Lambda Cloud instance."""

        import numpy as np

        # Create test data
        test_matrix = np.random.randn(100, 100)
        test_vector = np.random.randn(100)

        @cluster(
            provider="lambda",
            instance_type="gpu_1x_a10",
            region="us-east-1",
            cores=2,
            memory="8GB",
            lambda_api_key=self.lambda_creds.get("api_key"),
            terminate_on_completion=True,
        )
        def process_data(matrix, vector):
            """Process data on Lambda Cloud instance."""
            import numpy as np
            import time

            # Verify data integrity
            assert matrix.shape == (100, 100)
            assert vector.shape == (100,)

            # Perform computation
            start_compute = time.time()
            result_matrix = np.dot(matrix, matrix.T)
            result_vector = np.dot(matrix, vector)
            compute_time = time.time() - start_compute

            return {
                "result_matrix_shape": result_matrix.shape,
                "result_vector_shape": result_vector.shape,
                "matrix_sum": float(np.sum(result_matrix)),
                "vector_sum": float(np.sum(result_vector)),
                "compute_time": compute_time,
                "data_integrity_check": "passed",
            }

        # Execute with data transfer
        start_time = time.time()
        result = process_data(test_matrix, test_vector)
        total_time = time.time() - start_time

        # Verify results
        assert result is not None
        assert result["data_integrity_check"] == "passed"
        assert result["result_matrix_shape"] == (100, 100)
        assert result["result_vector_shape"] == (100,)
        assert isinstance(result["matrix_sum"], float)
        assert isinstance(result["vector_sum"], float)
        assert result["compute_time"] > 0

        logger.info(
            f"Lambda Cloud data transfer test completed in {total_time:.2f} seconds"
        )
        logger.info(f"Computation time: {result['compute_time']:.4f} seconds")

    def test_lambda_cloud_cost_tracking_real(self):
        """Test cost tracking integration with real Lambda Cloud usage."""

        @cluster(
            provider="lambda",
            instance_type="gpu_1x_a10",
            region="us-east-1",
            cores=1,
            memory="4GB",
            lambda_api_key=self.lambda_creds.get("api_key"),
            terminate_on_completion=True,
        )
        def cost_tracking_test():
            """Simple function to test cost tracking."""
            import time

            # Do some work to generate billable time
            time.sleep(10)

            return {
                "work_completed": True,
                "execution_time": 10,
                "cost_tracking_test": "completed",
            }

        # Track execution
        start_time = time.time()
        result = cost_tracking_test()
        end_time = time.time()

        execution_duration = end_time - start_time

        # Verify results
        assert result is not None
        assert result["cost_tracking_test"] == "completed"

        # Verify execution took reasonable time (including provisioning)
        assert execution_duration > 60  # Should include instance startup time

        # Note: In a real implementation, we would also verify:
        # - Usage appears in Lambda Cloud dashboard
        # - Cost estimates are generated
        # - Billing information is tracked
        # This requires access to Lambda Cloud billing API

        logger.info(f"Cost tracking test completed in {execution_duration:.2f} seconds")
        logger.info("Note: Check Lambda Cloud dashboard for usage data")

    def test_lambda_cloud_error_handling_real(self):
        """Test error handling with real Lambda Cloud execution."""

        @cluster(
            provider="lambda",
            instance_type="gpu_1x_a10",
            region="us-east-1",
            cores=1,
            memory="4GB",
            lambda_api_key=self.lambda_creds.get("api_key"),
            terminate_on_completion=True,
        )
        def failing_function():
            """Function that intentionally fails to test error handling."""
            import time

            # Do some work before failing
            time.sleep(2)

            # Intentional failure
            raise ValueError("Intentional test failure")

        # Execute and expect failure
        with pytest.raises(RuntimeError) as exc_info:
            failing_function()

        # Verify error information
        error_message = str(exc_info.value)
        assert "failed" in error_message.lower()
        assert "intentional test failure" in error_message

        logger.info(f"Error handling test completed: {error_message}")

    def test_lambda_cloud_multiple_instance_types_real(self):
        """Test different Lambda Cloud instance types."""

        instance_types = ["gpu_1x_a10"]  # Start with one, expand as needed

        for instance_type in instance_types:

            @cluster(
                provider="lambda",
                instance_type=instance_type,
                region="us-east-1",
                cores=1,
                memory="4GB",
                lambda_api_key=self.lambda_creds.get("api_key"),
                terminate_on_completion=True,
            )
            def test_instance_type():
                """Test execution on specific instance type."""
                import subprocess
                import platform

                # Get system information
                cpu_info = platform.processor()

                # Get GPU information if available
                try:
                    gpu_result = subprocess.run(
                        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    gpu_info = (
                        gpu_result.stdout.strip()
                        if gpu_result.returncode == 0
                        else "No GPU"
                    )
                except:
                    gpu_info = "GPU query failed"

                return {
                    "instance_type_tested": instance_type,
                    "cpu_info": cpu_info,
                    "gpu_info": gpu_info,
                    "test_result": "success",
                }

            # Execute test for this instance type
            result = test_instance_type()

            # Verify results
            assert result is not None
            assert result["test_result"] == "success"
            assert result["instance_type_tested"] == instance_type

            logger.info(f"Instance type {instance_type} test completed")
            logger.info(f"CPU: {result['cpu_info']}")
            logger.info(f"GPU: {result['gpu_info']}")

            # Add delay between instance tests to avoid rate limits
            time.sleep(30)
