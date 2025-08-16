"""
Reference patterns for basic clustrix usage.

These patterns demonstrate how users would typically use the @cluster decorator
for common computational tasks. All patterns use real infrastructure and
meaningful computations.
"""

import pytest
import os
import tempfile
from pathlib import Path
from clustrix import cluster
from clustrix.config import ClusterConfig
import clustrix.config as config_module


def test_basic_data_analysis_workflow():
    """
    Reference pattern for basic data analysis with @cluster.

    This demonstrates:
    - Proper configuration setup
    - Realistic function with meaningful computation
    - Normal function calling pattern
    - Result validation
    """

    # Step 1: User configures their cluster (exactly as documented)
    config = ClusterConfig()
    config.cluster_type = "slurm"
    config.cluster_host = os.getenv("SLURM_CLUSTER_HOST", "cluster.university.edu")
    config.username = os.getenv("SLURM_USERNAME", "researcher")
    config.private_key_path = os.getenv("SLURM_KEY_PATH", "~/.ssh/id_rsa")
    config.remote_work_dir = "/scratch/user/clustrix_jobs"
    config.cleanup_remote_files = True

    # Apply configuration (as users would)
    original_config = config_module._config
    config_module._config = config

    try:
        # Step 2: User defines their analysis function with @cluster decorator
        @cluster(cores=4, memory="16GB", time="01:00:00", partition="compute")
        def analyze_dataset(data_values, threshold=0.5):
            """Real data analysis function users would write."""
            # All imports inside for remote execution
            import numpy as np
            from scipy import stats
            import time

            start_time = time.time()

            # Convert to numpy array for analysis
            data = np.array(data_values)

            # Perform statistical analysis
            results = {
                "count": len(data),
                "mean": float(np.mean(data)),
                "std": float(np.std(data)),
                "median": float(np.median(data)),
                "min": float(np.min(data)),
                "max": float(np.max(data)),
                "percentiles": {
                    "25": float(np.percentile(data, 25)),
                    "50": float(np.percentile(data, 50)),
                    "75": float(np.percentile(data, 75)),
                },
            }

            # Find outliers (values beyond threshold * std from mean)
            mean = results["mean"]
            std = results["std"]
            outliers = data[np.abs(data - mean) > threshold * std]
            results["outliers"] = {
                "count": len(outliers),
                "values": (
                    outliers.tolist()
                    if len(outliers) < 100
                    else outliers[:100].tolist()
                ),
                "threshold_used": threshold,
            }

            # Perform normality test
            if len(data) > 3:
                statistic, p_value = stats.normaltest(data)
                results["normality_test"] = {
                    "statistic": float(statistic),
                    "p_value": float(p_value),
                    "is_normal": p_value > 0.05,
                }

            results["execution_time"] = time.time() - start_time
            return results

        # Step 3: User executes function normally (transparent remote execution)
        test_data = [
            1.2,
            2.3,
            3.1,
            4.5,
            5.2,
            6.8,
            7.1,
            8.9,
            9.2,
            10.1,
            11.5,
            12.3,
            13.1,
            14.2,
            15.5,
            100.0,
        ]  # Include outlier

        results = analyze_dataset(test_data, threshold=3.0)

        # Step 4: Validate realistic results
        assert isinstance(results, dict)
        assert results["count"] == 16
        assert results["mean"] > 0
        assert results["std"] > 0
        assert results["outliers"]["count"] >= 1  # Should detect the outlier (100.0)
        assert results["execution_time"] > 0
        assert "normality_test" in results

    finally:
        # Restore original configuration
        config_module._config = original_config


def test_simple_computation_workflow():
    """
    Reference pattern for simple mathematical computation.

    This demonstrates:
    - Minimal configuration
    - Simple but meaningful computation
    - Error handling
    - Performance metrics
    """

    # Configure for local or test cluster
    config = ClusterConfig()
    config.cluster_type = os.getenv("TEST_CLUSTER_TYPE", "local")
    if config.cluster_type != "local":
        config.cluster_host = os.getenv("TEST_CLUSTER_HOST")
        config.username = os.getenv("TEST_CLUSTER_USER")

    original_config = config_module._config
    config_module._config = config

    try:

        @cluster(
            cores=2,
            memory="4GB",
            parallel=False,  # Explicit control over parallelization
        )
        def compute_prime_factors(n):
            """Compute prime factorization - a real computation users might need."""
            import time
            import math

            if n <= 1:
                raise ValueError("Number must be greater than 1")

            start_time = time.time()

            factors = []
            # Check for 2s
            while n % 2 == 0:
                factors.append(2)
                n = n // 2

            # Check for odd factors
            for i in range(3, int(math.sqrt(n)) + 1, 2):
                while n % i == 0:
                    factors.append(i)
                    n = n // i

            # If n is still greater than 2, it's prime
            if n > 2:
                factors.append(n)

            computation_time = time.time() - start_time

            return {
                "factors": factors,
                "unique_factors": list(set(factors)),
                "factor_count": len(factors),
                "is_prime": len(factors) == 1,
                "computation_time": computation_time,
            }

        # Test with various numbers
        test_cases = [
            (100, [2, 2, 5, 5]),
            (97, [97]),  # Prime
            (1024, [2] * 10),  # Power of 2
            (315, [3, 3, 5, 7]),
        ]

        for number, expected_factors in test_cases:
            result = compute_prime_factors(number)

            assert result["factors"] == expected_factors
            assert result["is_prime"] == (len(expected_factors) == 1)
            assert result["computation_time"] >= 0

    finally:
        config_module._config = original_config


def test_file_processing_workflow():
    """
    Reference pattern for file processing tasks.

    This demonstrates:
    - File I/O operations
    - Data transformation
    - Error handling for missing files
    - Cleanup operations
    """

    config = ClusterConfig()
    config.cluster_type = os.getenv("TEST_CLUSTER_TYPE", "local")

    original_config = config_module._config
    config_module._config = config

    try:

        @cluster(cores=1, memory="2GB")
        def process_csv_file(file_path, output_format="summary"):
            """Process CSV file - common user task."""
            import pandas as pd
            import json
            import os

            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Input file not found: {file_path}")

            # Read and process file
            df = pd.read_csv(file_path)

            if output_format == "summary":
                return {
                    "file": os.path.basename(file_path),
                    "rows": len(df),
                    "columns": list(df.columns),
                    "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                    "memory_usage": int(df.memory_usage(deep=True).sum()),
                    "null_counts": df.isnull().sum().to_dict(),
                }
            elif output_format == "statistics":
                numeric_cols = df.select_dtypes(include=["number"]).columns
                stats = {}
                for col in numeric_cols:
                    stats[col] = {
                        "mean": float(df[col].mean()),
                        "std": float(df[col].std()),
                        "min": float(df[col].min()),
                        "max": float(df[col].max()),
                    }
                return stats
            else:
                raise ValueError(f"Unknown output format: {output_format}")

        # Create test CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("id,value,category\n")
            f.write("1,10.5,A\n")
            f.write("2,20.3,B\n")
            f.write("3,15.7,A\n")
            f.write("4,30.2,C\n")
            test_file = f.name

        try:
            # Process file with summary
            summary = process_csv_file(test_file, "summary")
            assert summary["rows"] == 4
            assert "id" in summary["columns"]
            assert summary["memory_usage"] > 0

            # Process file with statistics
            stats = process_csv_file(test_file, "statistics")
            assert "value" in stats
            assert stats["value"]["mean"] > 0
            assert stats["value"]["max"] == 30.2

        finally:
            # Cleanup
            if os.path.exists(test_file):
                os.unlink(test_file)

    finally:
        config_module._config = original_config
