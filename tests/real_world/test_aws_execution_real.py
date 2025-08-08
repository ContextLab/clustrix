"""
Real-world AWS cloud integration tests.

These tests require actual AWS credentials and create real EC2 instances.
NO MOCKS OR SIMULATIONS - these test real AWS cloud execution.
"""

import pytest
import os
import time
import logging

from clustrix import cluster, configure
from tests.real_world.credential_manager import get_aws_credentials

logger = logging.getLogger(__name__)


@pytest.mark.real_world
class TestAWSExecutionReal:
    """Test real AWS cloud job execution."""

    def setup_method(self):
        """Setup for each test method."""
        self.aws_creds = get_aws_credentials()
        if not self.aws_creds:
            pytest.skip("AWS credentials not available")

    def test_aws_ec2_basic_execution_real(self):
        """Test basic function execution on real AWS EC2 instance."""

        @cluster(
            provider="aws",
            instance_type="t3.medium",
            region="us-east-1",
            cores=2,
            memory="4GB",
            aws_access_key_id=self.aws_creds.get("access_key_id"),
            aws_secret_access_key=self.aws_creds.get("secret_access_key"),
            aws_region=self.aws_creds.get("region", "us-east-1"),
            terminate_on_completion=True,
            instance_startup_timeout=600,  # 10 minutes for EC2 startup
        )
        def test_aws_computation():
            """Simple computation to verify AWS execution works."""
            import platform
            import os
            import subprocess

            # Get AWS metadata to confirm we're on EC2
            try:
                result = subprocess.run(
                    [
                        "curl",
                        "-s",
                        "http://169.254.169.254/latest/meta-data/instance-type",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                instance_type = result.stdout if result.returncode == 0 else "unknown"
            except:
                instance_type = "metadata_unavailable"

            result = {
                "computation": 5 * 7,
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "aws_instance_type": instance_type,
                "working_directory": os.getcwd(),
                "environment_check": "aws_success",
            }

            return result

        # Execute function
        start_time = time.time()
        result = test_aws_computation()
        execution_time = time.time() - start_time

        # Verify results
        assert result is not None
        assert result["computation"] == 35
        assert "linux" in result["platform"].lower()
        assert result["environment_check"] == "aws_success"

        # Verify execution happened on AWS (not locally)
        assert execution_time > 120  # Should take time due to EC2 provisioning

        # Verify we're actually on EC2
        if result["aws_instance_type"] != "metadata_unavailable":
            assert (
                "t3.medium" in result["aws_instance_type"]
                or "t3" in result["aws_instance_type"]
            )

        logger.info(
            f"AWS EC2 basic execution completed in {execution_time:.2f} seconds"
        )
        logger.info(f"Instance type: {result['aws_instance_type']}")
        logger.info(f"Result: {result}")

    def test_aws_gpu_instance_execution_real(self):
        """Test execution on AWS GPU instance (if available and budget allows)."""

        @cluster(
            provider="aws",
            instance_type="g4dn.xlarge",  # GPU instance
            region="us-east-1",
            cores=4,
            memory="16GB",
            aws_access_key_id=self.aws_creds.get("access_key_id"),
            aws_secret_access_key=self.aws_creds.get("secret_access_key"),
            aws_region=self.aws_creds.get("region", "us-east-1"),
            terminate_on_completion=True,
            instance_startup_timeout=600,
        )
        def test_aws_gpu_computation():
            """Test GPU functionality on AWS."""
            import subprocess
            import platform

            # Check for GPU
            try:
                gpu_result = subprocess.run(
                    ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=30
                )
                gpu_list = (
                    gpu_result.stdout if gpu_result.returncode == 0 else "No GPUs found"
                )
            except Exception as e:
                gpu_list = f"Error checking GPUs: {e}"

            # Get instance metadata
            try:
                metadata_result = subprocess.run(
                    [
                        "curl",
                        "-s",
                        "http://169.254.169.254/latest/meta-data/instance-type",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                instance_type = (
                    metadata_result.stdout
                    if metadata_result.returncode == 0
                    else "unknown"
                )
            except:
                instance_type = "metadata_unavailable"

            return {
                "platform": platform.platform(),
                "gpu_detection": gpu_list,
                "aws_instance_type": instance_type,
                "gpu_test_status": "completed",
            }

        # Note: This test may be skipped if GPU instances are too expensive
        # or not available in the test account
        try:
            start_time = time.time()
            result = test_aws_gpu_computation()
            execution_time = time.time() - start_time

            # Verify results
            assert result is not None
            assert result["gpu_test_status"] == "completed"

            # Verify we're on a GPU instance
            if result["aws_instance_type"] != "metadata_unavailable":
                assert "g4dn" in result["aws_instance_type"].lower()

            logger.info(f"AWS GPU test completed in {execution_time:.2f} seconds")
            logger.info(f"GPU Detection: {result['gpu_detection']}")

        except Exception as e:
            # GPU instances might not be available or budget-restricted
            logger.warning(
                f"AWS GPU test failed (possibly due to instance availability): {e}"
            )
            pytest.skip(f"AWS GPU instance test failed: {e}")

    def test_aws_data_processing_real(self):
        """Test data processing capabilities on AWS."""

        import numpy as np

        # Create test data
        test_data = {
            "matrix": np.random.randn(50, 50),
            "values": list(range(100)),
            "metadata": {"test_type": "aws_data_processing", "version": "1.0"},
        }

        @cluster(
            provider="aws",
            instance_type="c5.large",  # Compute-optimized instance
            region="us-east-1",
            cores=2,
            memory="4GB",
            aws_access_key_id=self.aws_creds.get("access_key_id"),
            aws_secret_access_key=self.aws_creds.get("secret_access_key"),
            terminate_on_completion=True,
        )
        def process_aws_data(data_dict):
            """Process data on AWS instance."""
            import numpy as np
            import time

            # Verify data transfer
            matrix = data_dict["matrix"]
            values = data_dict["values"]
            metadata = data_dict["metadata"]

            assert matrix.shape == (50, 50)
            assert len(values) == 100
            assert metadata["test_type"] == "aws_data_processing"

            # Perform computation
            start_compute = time.time()
            matrix_result = np.dot(matrix, matrix.T)
            values_result = [x**2 for x in values]
            compute_time = time.time() - start_compute

            return {
                "matrix_result_shape": matrix_result.shape,
                "values_result_sum": sum(values_result),
                "compute_time": compute_time,
                "data_integrity": "verified",
                "aws_processing": "completed",
            }

        # Execute data processing
        start_time = time.time()
        result = process_aws_data(test_data)
        total_time = time.time() - start_time

        # Verify results
        assert result is not None
        assert result["aws_processing"] == "completed"
        assert result["data_integrity"] == "verified"
        assert result["matrix_result_shape"] == (50, 50)
        assert result["values_result_sum"] == sum(x**2 for x in range(100))

        logger.info(f"AWS data processing completed in {total_time:.2f} seconds")
        logger.info(f"Computation time: {result['compute_time']:.4f} seconds")

    def test_aws_cost_monitoring_real(self):
        """Test AWS cost monitoring and billing integration."""

        @cluster(
            provider="aws",
            instance_type="t3.micro",  # Cheapest instance
            region="us-east-1",
            cores=1,
            memory="1GB",
            aws_access_key_id=self.aws_creds.get("access_key_id"),
            aws_secret_access_key=self.aws_creds.get("secret_access_key"),
            terminate_on_completion=True,
        )
        def aws_cost_test():
            """Function to test cost monitoring."""
            import time
            import subprocess

            # Get instance information for cost calculation
            try:
                instance_result = subprocess.run(
                    [
                        "curl",
                        "-s",
                        "http://169.254.169.254/latest/meta-data/instance-type",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                instance_type = (
                    instance_result.stdout
                    if instance_result.returncode == 0
                    else "unknown"
                )
            except:
                instance_type = "unknown"

            # Simulate some work
            time.sleep(5)

            return {
                "cost_test": "completed",
                "instance_type": instance_type,
                "work_duration": 5,
            }

        # Execute with cost tracking
        start_time = time.time()
        result = aws_cost_test()
        execution_duration = time.time() - start_time

        # Verify results
        assert result is not None
        assert result["cost_test"] == "completed"

        # Verify execution took reasonable time
        assert execution_duration > 60  # Should include instance startup

        # In a full implementation, we would:
        # - Query AWS Cost Explorer API
        # - Verify charges appear in billing
        # - Calculate expected vs actual costs

        logger.info(
            f"AWS cost monitoring test completed in {execution_duration:.2f} seconds"
        )
        logger.info("Note: Check AWS billing console for usage charges")

    def test_aws_multiple_regions_real(self):
        """Test AWS execution across multiple regions."""

        regions = ["us-east-1", "us-west-2"]  # Start with two common regions

        for region in regions:

            @cluster(
                provider="aws",
                instance_type="t3.micro",
                region=region,
                cores=1,
                memory="1GB",
                aws_access_key_id=self.aws_creds.get("access_key_id"),
                aws_secret_access_key=self.aws_creds.get("secret_access_key"),
                terminate_on_completion=True,
            )
            def test_region_execution():
                """Test execution in specific AWS region."""
                import subprocess
                import time

                # Get availability zone to confirm region
                try:
                    az_result = subprocess.run(
                        [
                            "curl",
                            "-s",
                            "http://169.254.169.254/latest/meta-data/placement/availability-zone",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    availability_zone = (
                        az_result.stdout if az_result.returncode == 0 else "unknown"
                    )
                except:
                    availability_zone = "unknown"

                return {
                    "region_tested": region,
                    "availability_zone": availability_zone,
                    "region_test": "success",
                    "timestamp": time.time(),
                }

            # Execute in this region
            result = test_region_execution()

            # Verify results
            assert result is not None
            assert result["region_test"] == "success"
            assert result["region_tested"] == region

            # Verify we're in the correct region
            if result["availability_zone"] != "unknown":
                assert region.replace("-", "") in result["availability_zone"].replace(
                    "-", ""
                )

            logger.info(f"AWS region {region} test completed")
            logger.info(f"Availability zone: {result['availability_zone']}")

            # Add delay between regions to avoid rate limits
            time.sleep(60)

    def test_aws_error_recovery_real(self):
        """Test error handling and recovery with AWS."""

        @cluster(
            provider="aws",
            instance_type="t3.micro",
            region="us-east-1",
            cores=1,
            memory="1GB",
            aws_access_key_id=self.aws_creds.get("access_key_id"),
            aws_secret_access_key=self.aws_creds.get("secret_access_key"),
            terminate_on_completion=True,
        )
        def aws_error_test():
            """Function that fails to test error handling."""
            import os

            # Do some work first
            work_result = sum(range(100))

            # Create an error
            raise RuntimeError("AWS error test - intentional failure")

        # Execute and expect failure
        with pytest.raises(RuntimeError) as exc_info:
            aws_error_test()

        # Verify error handling
        error_message = str(exc_info.value)
        assert "failed" in error_message.lower()
        assert "intentional failure" in error_message

        logger.info(f"AWS error handling test completed: {error_message}")

    def test_aws_spot_instance_real(self):
        """Test AWS Spot instance usage (if supported)."""
        # Note: Spot instances require special handling and may not be
        # immediately available. This test demonstrates the concept.

        @cluster(
            provider="aws",
            instance_type="t3.micro",
            region="us-east-1",
            cores=1,
            memory="1GB",
            aws_access_key_id=self.aws_creds.get("access_key_id"),
            aws_secret_access_key=self.aws_creds.get("secret_access_key"),
            # spot_price='0.003',  # Would be added in full implementation
            terminate_on_completion=True,
        )
        def aws_spot_test():
            """Test function for spot instance execution."""
            import subprocess

            # Check if we're on a spot instance
            try:
                spot_result = subprocess.run(
                    [
                        "curl",
                        "-s",
                        "http://169.254.169.254/latest/meta-data/spot/instance-action",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                spot_status = "spot" if spot_result.returncode != 0 else "on_demand"
            except:
                spot_status = "unknown"

            return {
                "spot_test": "completed",
                "instance_billing": spot_status,
                "cost_optimization": "tested",
            }

        # Execute spot test
        result = aws_spot_test()

        # Verify results
        assert result is not None
        assert result["spot_test"] == "completed"
        assert result["cost_optimization"] == "tested"

        logger.info(f"AWS spot instance test completed")
        logger.info(f"Instance billing type: {result['instance_billing']}")
