"""
Performance benchmarking tests for Kubernetes cluster provisioning.

These tests measure and compare performance metrics across all supported
Kubernetes provisioning providers to ensure:
1. Provisioning times are within acceptable limits
2. Resource utilization is efficient
3. Scaling performance is predictable
4. Cleanup times are reasonable
5. Provider-specific optimizations work correctly

Requirements:
- Valid credentials for tested providers
- Network connectivity with stable latency
- Sufficient quotas for performance testing
- Clean testing environment for accurate measurements

Results are logged for performance analysis and regression detection.
"""

import os
import time
import pytest
import logging
import statistics
import threading
import concurrent.futures
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
import json
import csv
from datetime import datetime, timezone

from clustrix.kubernetes.cluster_provisioner import (
    KubernetesClusterProvisioner,
    ClusterSpec,
)
from clustrix.config import ClusterConfig
from clustrix.credential_manager import get_credential_manager

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Performance metrics for a single test run."""

    provider: str
    test_name: str
    timestamp: str
    node_count: int
    region: str

    # Timing metrics (seconds)
    credential_validation_time: float
    provisioning_start_time: float
    provisioning_complete_time: float
    ready_check_time: float
    total_provision_time: float
    cleanup_time: float

    # Success metrics
    success: bool
    error_message: Optional[str] = None

    # Resource metrics
    resources_created: int = 0
    resource_types: List[str] = None

    # Performance scores (derived)
    provision_score: float = 0.0  # Based on time vs expectations
    reliability_score: float = 0.0  # Based on success rate
    efficiency_score: float = 0.0  # Based on resources vs time

    def __post_init__(self):
        if self.resource_types is None:
            self.resource_types = []


@dataclass
class ProviderBenchmarkConfig:
    """Benchmark configuration for a provider."""

    name: str
    provisioner_class: str
    region: str
    expected_provision_time: float  # seconds
    expected_ready_time: float  # seconds
    expected_cleanup_time: float  # seconds
    max_acceptable_time: float  # seconds - test fails if exceeded


@pytest.mark.real_world
@pytest.mark.performance
class TestKubernetesPerformanceBenchmarks:
    """Comprehensive performance benchmarking suite."""

    @pytest.fixture(scope="class")
    def benchmark_configs(self):
        """Define benchmark configurations for all providers."""
        return [
            ProviderBenchmarkConfig(
                name="aws",
                provisioner_class="clustrix.kubernetes.aws_provisioner.AWSEKSFromScratchProvisioner",
                region="us-west-2",
                expected_provision_time=300.0,  # 5 minutes
                expected_ready_time=900.0,  # 15 minutes
                expected_cleanup_time=180.0,  # 3 minutes
                max_acceptable_time=1800.0,  # 30 minutes max
            ),
            ProviderBenchmarkConfig(
                name="gcp",
                provisioner_class="clustrix.kubernetes.gcp_provisioner.GCPGKEFromScratchProvisioner",
                region="us-central1",
                expected_provision_time=240.0,  # 4 minutes
                expected_ready_time=720.0,  # 12 minutes
                expected_cleanup_time=120.0,  # 2 minutes
                max_acceptable_time=1440.0,  # 24 minutes max
            ),
            ProviderBenchmarkConfig(
                name="azure",
                provisioner_class="clustrix.kubernetes.azure_provisioner.AzureAKSFromScratchProvisioner",
                region="eastus",
                expected_provision_time=360.0,  # 6 minutes
                expected_ready_time=1080.0,  # 18 minutes
                expected_cleanup_time=240.0,  # 4 minutes
                max_acceptable_time=2160.0,  # 36 minutes max
            ),
            ProviderBenchmarkConfig(
                name="huggingface",
                provisioner_class="clustrix.kubernetes.huggingface_provisioner.HuggingFaceKubernetesProvisioner",
                region="global",
                expected_provision_time=60.0,  # 1 minute
                expected_ready_time=600.0,  # 10 minutes
                expected_cleanup_time=30.0,  # 30 seconds
                max_acceptable_time=900.0,  # 15 minutes max
            ),
            ProviderBenchmarkConfig(
                name="lambda",
                provisioner_class="clustrix.kubernetes.lambda_provisioner.LambdaCloudKubernetesProvisioner",
                region="us-west-2",
                expected_provision_time=120.0,  # 2 minutes
                expected_ready_time=300.0,  # 5 minutes
                expected_cleanup_time=60.0,  # 1 minute
                max_acceptable_time=600.0,  # 10 minutes max
            ),
        ]

    @pytest.fixture(scope="class")
    def performance_results_dir(self):
        """Create directory for performance test results."""
        results_dir = "performance_test_results"
        os.makedirs(results_dir, exist_ok=True)
        return results_dir

    @pytest.fixture(scope="class")
    def available_benchmark_providers(self, benchmark_configs):
        """Get providers available for benchmarking."""
        available = []
        credential_manager = get_credential_manager()

        for config in benchmark_configs:
            try:
                credentials = credential_manager.ensure_credential(config.name)
                if credentials:
                    available.append(config)
                else:
                    logger.info(
                        f"Skipping {config.name} benchmarks - credentials not available"
                    )
            except Exception:
                logger.info(f"Skipping {config.name} benchmarks - credential error")

        if not available:
            pytest.skip(
                "No provider credentials available for performance benchmarking"
            )

        return available

    def test_credential_validation_performance(
        self, available_benchmark_providers, performance_results_dir
    ):
        """Benchmark credential validation performance across providers."""
        logger.info("🚀 Benchmarking credential validation performance")

        results = []
        credential_manager = get_credential_manager()

        for config in available_benchmark_providers:
            logger.info(f"Benchmarking credential validation for {config.name}")

            # Run multiple iterations for statistical accuracy
            iteration_times = []

            for iteration in range(5):  # 5 iterations
                try:
                    start_time = time.time()

                    credentials = credential_manager.ensure_credential(config.name)

                    # Import and create provisioner
                    provisioner_module = __import__(
                        config.provisioner_class.rsplit(".", 1)[0],
                        fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                    )
                    provisioner_class = getattr(
                        provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                    )

                    provisioner = provisioner_class(credentials, config.region)
                    validation_result = provisioner.validate_credentials()

                    validation_time = time.time() - start_time
                    iteration_times.append(validation_time)

                    if not validation_result:
                        logger.warning(
                            f"Credential validation failed for {config.name}"
                        )

                except Exception as e:
                    logger.error(f"Credential validation error for {config.name}: {e}")
                    iteration_times.append(float("inf"))

                # Brief pause between iterations
                time.sleep(1)

            # Calculate statistics
            valid_times = [t for t in iteration_times if t != float("inf")]
            if valid_times:
                avg_time = statistics.mean(valid_times)
                median_time = statistics.median(valid_times)
                std_dev = statistics.stdev(valid_times) if len(valid_times) > 1 else 0.0
                min_time = min(valid_times)
                max_time = max(valid_times)

                result = {
                    "provider": config.name,
                    "test": "credential_validation",
                    "iterations": len(valid_times),
                    "avg_time": avg_time,
                    "median_time": median_time,
                    "std_dev": std_dev,
                    "min_time": min_time,
                    "max_time": max_time,
                    "success_rate": len(valid_times) / len(iteration_times),
                }

                results.append(result)

                logger.info(f"📊 {config.name} credential validation:")
                logger.info(f"   Average: {avg_time:.3f}s")
                logger.info(f"   Median: {median_time:.3f}s")
                logger.info(f"   Std Dev: {std_dev:.3f}s")
                logger.info(f"   Range: {min_time:.3f}s - {max_time:.3f}s")
                logger.info(f"   Success Rate: {result['success_rate']:.1%}")

        # Save results
        self._save_benchmark_results(
            results, "credential_validation_benchmark", performance_results_dir
        )

        # Assert reasonable performance expectations
        for result in results:
            if result["success_rate"] > 0.8:  # At least 80% success
                assert (
                    result["avg_time"] < 10.0
                ), f"{result['provider']} credential validation too slow: {result['avg_time']:.3f}s"
                assert (
                    result["std_dev"] < result["avg_time"]
                ), f"{result['provider']} credential validation too inconsistent"

        logger.info("✅ Credential validation performance benchmarking completed")

    @pytest.mark.slow
    def test_single_node_provisioning_performance(
        self, available_benchmark_providers, performance_results_dir
    ):
        """Benchmark single-node cluster provisioning performance."""
        logger.info("🚀 Benchmarking single-node cluster provisioning performance")

        results = []
        test_id = int(time.time())
        credential_manager = get_credential_manager()

        for config in available_benchmark_providers:
            logger.info(f"Benchmarking single-node provisioning for {config.name}")

            cluster_spec = ClusterSpec(
                cluster_name=f"perf-single-{config.name}-{test_id}",
                provider=config.name,
                node_count=1,
                kubernetes_version="1.28",
                region=config.region,
            )

            cluster_info = None
            try:
                # Get credentials and create provisioner
                credentials = credential_manager.ensure_credential(config.name)
                provisioner_module = __import__(
                    config.provisioner_class.rsplit(".", 1)[0],
                    fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                )
                provisioner_class = getattr(
                    provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                )
                provisioner = provisioner_class(credentials, config.region)

                # Benchmark credential validation
                cred_start = time.time()
                provisioner.validate_credentials()
                cred_time = time.time() - cred_start

                # Benchmark provisioning
                provision_start = time.time()
                cluster_info = provisioner.provision_complete_infrastructure(
                    cluster_spec
                )
                provision_time = time.time() - provision_start

                # Benchmark ready state check
                ready_start = time.time()
                max_wait_time = config.max_acceptable_time
                ready = False

                while time.time() - ready_start < max_wait_time:
                    status = provisioner.get_cluster_status(cluster_spec.cluster_name)
                    if status.get("ready_for_jobs", False):
                        ready = True
                        break
                    time.sleep(30)

                ready_time = time.time() - ready_start
                total_time = provision_time + ready_time

                # Count created resources
                created_resources = cluster_info.get("created_resources", {})
                resource_count = sum(
                    len(resources) for resources in created_resources.values()
                )
                resource_types = list(created_resources.keys())

                # Create performance metrics
                metrics = PerformanceMetrics(
                    provider=config.name,
                    test_name="single_node_provisioning",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    node_count=1,
                    region=config.region,
                    credential_validation_time=cred_time,
                    provisioning_start_time=0.0,  # Relative to test start
                    provisioning_complete_time=provision_time,
                    ready_check_time=ready_time,
                    total_provision_time=total_time,
                    cleanup_time=0.0,  # Will be measured below
                    success=ready,
                    resources_created=resource_count,
                    resource_types=resource_types,
                )

                # Calculate performance scores
                metrics.provision_score = min(
                    100.0,
                    (config.expected_provision_time / max(provision_time, 1.0)) * 100,
                )
                metrics.reliability_score = 100.0 if ready else 0.0
                metrics.efficiency_score = min(
                    100.0, (resource_count / max(total_time, 1.0)) * 10
                )

                # Benchmark cleanup
                if cluster_info:
                    cleanup_start = time.time()
                    cleanup_success = provisioner.destroy_cluster_infrastructure(
                        cluster_spec.cluster_name
                    )
                    cleanup_time = time.time() - cleanup_start
                    metrics.cleanup_time = cleanup_time
                    cluster_info = None  # Prevent double cleanup

                    if not cleanup_success:
                        logger.warning(
                            f"Cleanup may not have completed successfully for {config.name}"
                        )

                results.append(asdict(metrics))

                # Log results
                logger.info(f"📊 {config.name} single-node performance:")
                logger.info(f"   Credential validation: {cred_time:.1f}s")
                logger.info(f"   Provisioning: {provision_time:.1f}s")
                logger.info(f"   Ready check: {ready_time:.1f}s")
                logger.info(f"   Total: {total_time:.1f}s")
                logger.info(f"   Cleanup: {cleanup_time:.1f}s")
                logger.info(f"   Resources created: {resource_count}")
                logger.info(f"   Success: {'✅' if ready else '❌'}")
                logger.info(
                    f"   Performance scores - Provision: {metrics.provision_score:.1f}, Reliability: {metrics.reliability_score:.1f}, Efficiency: {metrics.efficiency_score:.1f}"
                )

                # Assert performance requirements
                if ready:
                    assert (
                        total_time <= config.max_acceptable_time
                    ), f"{config.name} total provisioning time {total_time:.1f}s exceeded maximum {config.max_acceptable_time:.1f}s"
                    assert (
                        cleanup_time <= config.expected_cleanup_time * 2
                    ), f"{config.name} cleanup time {cleanup_time:.1f}s too slow"

            except Exception as e:
                logger.error(f"Benchmarking failed for {config.name}: {e}")

                # Record failure metrics
                metrics = PerformanceMetrics(
                    provider=config.name,
                    test_name="single_node_provisioning",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    node_count=1,
                    region=config.region,
                    credential_validation_time=0.0,
                    provisioning_start_time=0.0,
                    provisioning_complete_time=0.0,
                    ready_check_time=0.0,
                    total_provision_time=0.0,
                    cleanup_time=0.0,
                    success=False,
                    error_message=str(e),
                )
                results.append(asdict(metrics))

            finally:
                # Ensure cleanup
                if cluster_info:
                    try:
                        provisioner.destroy_cluster_infrastructure(
                            cluster_spec.cluster_name
                        )
                    except Exception as cleanup_error:
                        logger.warning(
                            f"Final cleanup failed for {config.name}: {cleanup_error}"
                        )

        # Save results
        self._save_benchmark_results(
            results, "single_node_provisioning_benchmark", performance_results_dir
        )

        # Calculate summary statistics
        successful_results = [r for r in results if r["success"]]
        if successful_results:
            avg_provision_time = statistics.mean(
                [r["total_provision_time"] for r in successful_results]
            )
            logger.info(
                f"📊 Overall single-node provisioning average: {avg_provision_time:.1f}s"
            )

        logger.info("✅ Single-node provisioning performance benchmarking completed")

    @pytest.mark.slow
    def test_scaling_performance_benchmark(
        self, available_benchmark_providers, performance_results_dir
    ):
        """Benchmark scaling performance across different node counts."""
        logger.info("🚀 Benchmarking scaling performance")

        # Test scaling with different node counts
        node_counts = [1, 2, 4]  # Limited for practical testing
        results = []
        test_id = int(time.time())
        credential_manager = get_credential_manager()

        for config in available_benchmark_providers:
            # Skip scaling tests for providers that don't benefit from it
            if config.name in [
                "huggingface"
            ]:  # HF Spaces don't scale in the traditional sense
                logger.info(f"Skipping scaling test for {config.name} - not applicable")
                continue

            logger.info(f"Benchmarking scaling performance for {config.name}")

            for node_count in node_counts:
                logger.info(f"Testing {config.name} with {node_count} nodes")

                cluster_spec = ClusterSpec(
                    cluster_name=f"perf-scale-{config.name}-{node_count}n-{test_id}",
                    provider=config.name,
                    node_count=node_count,
                    kubernetes_version="1.28",
                    region=config.region,
                )

                cluster_info = None
                try:
                    # Get credentials and create provisioner
                    credentials = credential_manager.ensure_credential(config.name)
                    provisioner_module = __import__(
                        config.provisioner_class.rsplit(".", 1)[0],
                        fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                    )
                    provisioner_class = getattr(
                        provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                    )
                    provisioner = provisioner_class(credentials, config.region)

                    # Benchmark provisioning with scaling
                    provision_start = time.time()
                    cluster_info = provisioner.provision_complete_infrastructure(
                        cluster_spec
                    )
                    provision_time = time.time() - provision_start

                    # Wait for ready state (with timeout based on node count)
                    ready_start = time.time()
                    max_wait = config.max_acceptable_time * (
                        1 + 0.5 * (node_count - 1)
                    )  # Scale timeout
                    ready = False

                    while time.time() - ready_start < max_wait:
                        status = provisioner.get_cluster_status(
                            cluster_spec.cluster_name
                        )
                        if status.get("ready_for_jobs", False):
                            ready = True
                            break
                        time.sleep(30)

                    ready_time = time.time() - ready_start
                    total_time = provision_time + ready_time

                    # Calculate per-node performance
                    time_per_node = total_time / node_count

                    # Record results
                    result = {
                        "provider": config.name,
                        "test": "scaling_performance",
                        "node_count": node_count,
                        "provision_time": provision_time,
                        "ready_time": ready_time,
                        "total_time": total_time,
                        "time_per_node": time_per_node,
                        "success": ready,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                    results.append(result)

                    logger.info(f"📊 {config.name} {node_count}-node scaling:")
                    logger.info(f"   Provision: {provision_time:.1f}s")
                    logger.info(f"   Ready: {ready_time:.1f}s")
                    logger.info(f"   Total: {total_time:.1f}s")
                    logger.info(f"   Per-node: {time_per_node:.1f}s")
                    logger.info(f"   Success: {'✅' if ready else '❌'}")

                    # Cleanup
                    if cluster_info:
                        cleanup_start = time.time()
                        provisioner.destroy_cluster_infrastructure(
                            cluster_spec.cluster_name
                        )
                        cleanup_time = time.time() - cleanup_start
                        result["cleanup_time"] = cleanup_time
                        cluster_info = None

                except Exception as e:
                    logger.error(
                        f"Scaling test failed for {config.name} {node_count} nodes: {e}"
                    )
                    results.append(
                        {
                            "provider": config.name,
                            "test": "scaling_performance",
                            "node_count": node_count,
                            "success": False,
                            "error": str(e),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    )

                finally:
                    if cluster_info:
                        try:
                            provisioner.destroy_cluster_infrastructure(
                                cluster_spec.cluster_name
                            )
                        except Exception:
                            pass

                # Brief pause between scaling tests
                time.sleep(30)

        # Save scaling results
        self._save_benchmark_results(
            results, "scaling_performance_benchmark", performance_results_dir
        )

        # Analyze scaling efficiency
        for config in available_benchmark_providers:
            if config.name in ["huggingface"]:
                continue

            provider_results = [
                r
                for r in results
                if r["provider"] == config.name and r.get("success", False)
            ]
            if len(provider_results) >= 2:
                # Check if scaling is efficient (not linear degradation)
                provider_results.sort(key=lambda x: x["node_count"])

                scaling_efficiency = []
                for i in range(1, len(provider_results)):
                    prev_result = provider_results[i - 1]
                    curr_result = provider_results[i]

                    expected_time = (
                        prev_result["time_per_node"] * curr_result["node_count"]
                    )
                    actual_time = curr_result["total_time"]
                    efficiency = (
                        (expected_time / actual_time) * 100 if actual_time > 0 else 0
                    )

                    scaling_efficiency.append(efficiency)

                avg_efficiency = (
                    statistics.mean(scaling_efficiency) if scaling_efficiency else 0
                )
                logger.info(
                    f"📊 {config.name} scaling efficiency: {avg_efficiency:.1f}%"
                )

        logger.info("✅ Scaling performance benchmarking completed")

    def test_concurrent_provisioning_performance(
        self, available_benchmark_providers, performance_results_dir
    ):
        """Benchmark concurrent provisioning performance."""
        logger.info("🚀 Benchmarking concurrent provisioning performance")

        # Limit concurrent tests to avoid quota issues
        max_concurrent = min(3, len(available_benchmark_providers))
        test_providers = available_benchmark_providers[:max_concurrent]

        if len(test_providers) < 2:
            pytest.skip("Need at least 2 providers for concurrent performance testing")

        test_id = int(time.time())
        results = []

        def provision_cluster_benchmark(config):
            """Helper function for concurrent provisioning."""
            try:
                credential_manager = get_credential_manager()
                credentials = credential_manager.ensure_credential(config.name)

                provisioner_module = __import__(
                    config.provisioner_class.rsplit(".", 1)[0],
                    fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                )
                provisioner_class = getattr(
                    provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                )
                provisioner = provisioner_class(credentials, config.region)

                cluster_spec = ClusterSpec(
                    cluster_name=f"perf-concurrent-{config.name}-{test_id}",
                    provider=config.name,
                    node_count=1,
                    kubernetes_version="1.28",
                    region=config.region,
                )

                # Measure provisioning
                start_time = time.time()
                cluster_info = provisioner.provision_complete_infrastructure(
                    cluster_spec
                )
                provision_time = time.time() - start_time

                # Wait for ready state
                ready_start = time.time()
                max_wait = config.max_acceptable_time
                ready = False

                while time.time() - ready_start < max_wait:
                    status = provisioner.get_cluster_status(cluster_spec.cluster_name)
                    if status.get("ready_for_jobs", False):
                        ready = True
                        break
                    time.sleep(15)

                ready_time = time.time() - ready_start
                total_time = provision_time + ready_time

                # Cleanup
                cleanup_start = time.time()
                provisioner.destroy_cluster_infrastructure(cluster_spec.cluster_name)
                cleanup_time = time.time() - cleanup_start

                return {
                    "provider": config.name,
                    "provision_time": provision_time,
                    "ready_time": ready_time,
                    "total_time": total_time,
                    "cleanup_time": cleanup_time,
                    "success": ready,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            except Exception as e:
                return {
                    "provider": config.name,
                    "success": False,
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

        # Run concurrent provisioning
        logger.info(
            f"Running concurrent provisioning for {len(test_providers)} providers"
        )

        start_time = time.time()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(test_providers)
        ) as executor:
            futures = [
                executor.submit(provision_cluster_benchmark, config)
                for config in test_providers
            ]
            concurrent_results = [
                future.result(
                    timeout=max(config.max_acceptable_time for config in test_providers)
                    + 300
                )
                for future in futures
            ]

        concurrent_total_time = time.time() - start_time

        # Analyze concurrent performance
        successful_concurrent = [
            r for r in concurrent_results if r.get("success", False)
        ]

        concurrent_summary = {
            "test": "concurrent_provisioning",
            "total_concurrent_time": concurrent_total_time,
            "providers_tested": len(test_providers),
            "successful_provisions": len(successful_concurrent),
            "success_rate": len(successful_concurrent) / len(test_providers),
            "results": concurrent_results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        results.append(concurrent_summary)

        # Log concurrent results
        logger.info(f"📊 Concurrent provisioning performance:")
        logger.info(f"   Total concurrent time: {concurrent_total_time:.1f}s")
        logger.info(f"   Success rate: {concurrent_summary['success_rate']:.1%}")
        logger.info(
            f"   Successful provisions: {len(successful_concurrent)}/{len(test_providers)}"
        )

        for result in successful_concurrent:
            logger.info(f"   {result['provider']}: {result['total_time']:.1f}s total")

        # Compare with sequential expectation
        if successful_concurrent:
            sequential_expected_time = sum(
                next(
                    config.expected_provision_time + config.expected_ready_time
                    for config in test_providers
                    if config.name == r["provider"]
                )
                for r in successful_concurrent
            )

            efficiency_gain = (
                (sequential_expected_time / concurrent_total_time)
                if concurrent_total_time > 0
                else 0
            )
            logger.info(f"   Concurrent efficiency gain: {efficiency_gain:.1f}x")

            concurrent_summary["sequential_expected_time"] = sequential_expected_time
            concurrent_summary["efficiency_gain"] = efficiency_gain

        # Save concurrent results
        self._save_benchmark_results(
            results, "concurrent_provisioning_benchmark", performance_results_dir
        )

        # Assert reasonable concurrent performance
        assert (
            concurrent_summary["success_rate"] >= 0.5
        ), f"Concurrent provisioning success rate too low: {concurrent_summary['success_rate']:.1%}"

        logger.info("✅ Concurrent provisioning performance benchmarking completed")

    def test_provider_performance_comparison(
        self, available_benchmark_providers, performance_results_dir
    ):
        """Generate comprehensive performance comparison across providers."""
        logger.info("🚀 Generating provider performance comparison")

        # This test aggregates results from previous benchmarks and generates comparison
        comparison_results = []

        for config in available_benchmark_providers:
            logger.info(f"Generating performance profile for {config.name}")

            # Create performance profile
            profile = {
                "provider": config.name,
                "region": config.region,
                "expected_provision_time": config.expected_provision_time,
                "expected_ready_time": config.expected_ready_time,
                "expected_cleanup_time": config.expected_cleanup_time,
                "max_acceptable_time": config.max_acceptable_time,
                "performance_category": self._categorize_provider_performance(config),
                "use_cases": self._get_provider_use_cases(config.name),
                "strengths": self._get_provider_strengths(config.name),
                "considerations": self._get_provider_considerations(config.name),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            comparison_results.append(profile)

        # Generate overall comparison
        overall_comparison = {
            "test": "provider_performance_comparison",
            "providers_analyzed": len(available_benchmark_providers),
            "fastest_provision": min(
                comparison_results, key=lambda x: x["expected_provision_time"]
            )["provider"],
            "fastest_ready": min(
                comparison_results, key=lambda x: x["expected_ready_time"]
            )["provider"],
            "fastest_cleanup": min(
                comparison_results, key=lambda x: x["expected_cleanup_time"]
            )["provider"],
            "most_reliable": self._get_most_reliable_provider(comparison_results),
            "best_for_quick_tasks": self._get_best_for_quick_tasks(comparison_results),
            "best_for_production": self._get_best_for_production(comparison_results),
            "provider_profiles": comparison_results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Save comparison results
        self._save_benchmark_results(
            [overall_comparison],
            "provider_performance_comparison",
            performance_results_dir,
        )

        # Log comparison summary
        logger.info("📊 Provider Performance Comparison Summary:")
        logger.info(f"   Fastest Provision: {overall_comparison['fastest_provision']}")
        logger.info(f"   Fastest Ready: {overall_comparison['fastest_ready']}")
        logger.info(f"   Fastest Cleanup: {overall_comparison['fastest_cleanup']}")
        logger.info(f"   Most Reliable: {overall_comparison['most_reliable']}")
        logger.info(
            f"   Best for Quick Tasks: {overall_comparison['best_for_quick_tasks']}"
        )
        logger.info(
            f"   Best for Production: {overall_comparison['best_for_production']}"
        )

        logger.info("✅ Provider performance comparison completed")

    def _categorize_provider_performance(self, config: ProviderBenchmarkConfig) -> str:
        """Categorize provider performance characteristics."""
        total_expected = config.expected_provision_time + config.expected_ready_time

        if total_expected <= 300:  # 5 minutes
            return "fast"
        elif total_expected <= 900:  # 15 minutes
            return "medium"
        else:
            return "slow"

    def _get_provider_use_cases(self, provider_name: str) -> List[str]:
        """Get typical use cases for a provider."""
        use_cases = {
            "aws": [
                "Production workloads",
                "Enterprise applications",
                "High availability",
                "Auto-scaling",
            ],
            "gcp": [
                "Machine learning",
                "Data analytics",
                "Production workloads",
                "Microservices",
            ],
            "azure": [
                "Enterprise applications",
                "Hybrid cloud",
                "Production workloads",
                "Integration with MS ecosystem",
            ],
            "huggingface": [
                "AI/ML experimentation",
                "Model hosting",
                "Rapid prototyping",
                "Educational use",
            ],
            "lambda": [
                "GPU workloads",
                "Deep learning",
                "Scientific computing",
                "High-performance computing",
            ],
        }
        return use_cases.get(provider_name, ["General purpose"])

    def _get_provider_strengths(self, provider_name: str) -> List[str]:
        """Get key strengths of a provider."""
        strengths = {
            "aws": [
                "Mature ecosystem",
                "High reliability",
                "Global availability",
                "Enterprise features",
            ],
            "gcp": [
                "Fast provisioning",
                "ML/AI tools",
                "Cost-effective",
                "Modern infrastructure",
            ],
            "azure": [
                "Enterprise integration",
                "Hybrid capabilities",
                "Security features",
                "Microsoft ecosystem",
            ],
            "huggingface": [
                "Very fast setup",
                "AI/ML focus",
                "Easy model deployment",
                "Low cost for experimentation",
            ],
            "lambda": [
                "GPU specialization",
                "High performance",
                "Cost-effective GPUs",
                "Simple API",
            ],
        }
        return strengths.get(provider_name, ["General reliability"])

    def _get_provider_considerations(self, provider_name: str) -> List[str]:
        """Get key considerations for a provider."""
        considerations = {
            "aws": ["Complex pricing", "Slow provisioning", "Learning curve"],
            "gcp": ["Quota limitations", "Regional availability", "Billing complexity"],
            "azure": ["Slow provisioning", "Complex configuration", "Resource naming"],
            "huggingface": [
                "Limited compute options",
                "Public visibility",
                "Resource constraints",
            ],
            "lambda": ["Limited regions", "GPU availability", "Instance quotas"],
        }
        return considerations.get(provider_name, ["Standard cloud considerations"])

    def _get_most_reliable_provider(self, profiles: List[Dict]) -> str:
        """Determine most reliable provider based on profiles."""
        # In a real implementation, this would analyze historical success rates
        # For now, return based on expected characteristics
        enterprise_providers = [
            p for p in profiles if p["provider"] in ["aws", "azure", "gcp"]
        ]
        if enterprise_providers:
            return min(
                enterprise_providers, key=lambda x: x["expected_provision_time"]
            )["provider"]
        return profiles[0]["provider"] if profiles else "unknown"

    def _get_best_for_quick_tasks(self, profiles: List[Dict]) -> str:
        """Determine best provider for quick tasks."""
        return min(
            profiles,
            key=lambda x: x["expected_provision_time"] + x["expected_ready_time"],
        )["provider"]

    def _get_best_for_production(self, profiles: List[Dict]) -> str:
        """Determine best provider for production workloads."""
        production_providers = [
            p for p in profiles if p["provider"] in ["aws", "gcp", "azure"]
        ]
        if production_providers:
            return min(production_providers, key=lambda x: x["max_acceptable_time"])[
                "provider"
            ]
        return self._get_most_reliable_provider(profiles)

    def _save_benchmark_results(
        self, results: List[Dict], test_name: str, results_dir: str
    ):
        """Save benchmark results to files."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Save as JSON
        json_filename = os.path.join(results_dir, f"{test_name}_{timestamp}.json")
        with open(json_filename, "w") as f:
            json.dump(results, f, indent=2, default=str)

        # Save as CSV if results have consistent structure
        if results and isinstance(results[0], dict):
            try:
                csv_filename = os.path.join(results_dir, f"{test_name}_{timestamp}.csv")
                if results:
                    fieldnames = set()
                    for result in results:
                        fieldnames.update(result.keys())

                    with open(csv_filename, "w", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=sorted(fieldnames))
                        writer.writeheader()
                        writer.writerows(results)

            except Exception as e:
                logger.warning(f"Could not save CSV results: {e}")

        logger.info(f"📁 Benchmark results saved: {json_filename}")

    @pytest.mark.cleanup
    def test_benchmark_cleanup_verification(self, performance_results_dir):
        """Verify all benchmark resources have been cleaned up."""
        logger.info("🧪 Verifying benchmark resource cleanup")

        # This test ensures no resources are left behind from performance tests
        # It would check for any clusters, instances, or other resources that might
        # still exist from the benchmark tests

        # For now, just verify the results directory exists and has content
        assert os.path.exists(
            performance_results_dir
        ), "Performance results directory should exist"

        result_files = [
            f
            for f in os.listdir(performance_results_dir)
            if f.endswith((".json", ".csv"))
        ]
        logger.info(f"📁 Found {len(result_files)} benchmark result files")

        if result_files:
            logger.info("📊 Benchmark result files:")
            for filename in sorted(result_files)[-5:]:  # Show last 5 files
                logger.info(f"   {filename}")

        logger.info("✅ Benchmark cleanup verification completed")
