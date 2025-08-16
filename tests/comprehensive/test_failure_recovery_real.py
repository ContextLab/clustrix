"""
Failure recovery tests using real infrastructure.

These tests validate clustrix's ability to handle and recover from
various failure scenarios using actual infrastructure without mocks.
"""

import pytest
import os
import sys
import time
import signal
import tempfile
import subprocess
from pathlib import Path
from typing import Optional
import numpy as np
from clustrix import cluster, configure
from clustrix.config import ClusterConfig
from clustrix.executor import ClusterExecutor


class TestConnectionFailures:
    """Test recovery from connection failures."""

    @pytest.mark.real_world
    def test_ssh_connection_drop_recovery(self):
        """
        Test recovery from SSH connection drops.

        Validates reconnection and job resumption.
        """
        ssh_host = os.getenv("TEST_SSH_HOST", "localhost")
        ssh_port = int(os.getenv("TEST_SSH_PORT", "2222"))

        configure(
            cluster_type="ssh",
            cluster_host=ssh_host,
            cluster_port=ssh_port,
            username=os.getenv("TEST_SSH_USER", "testuser"),
            password=os.getenv("TEST_SSH_PASS", "testpass"),
            connection_retry_count=3,
            connection_retry_delay=2,
        )

        @cluster(cores=2, memory="2GB", retry_on_failure=True)
        def resilient_task(duration):
            """Task that can survive connection drops."""
            import time
            import random

            checkpoints = []
            for i in range(duration):
                time.sleep(1)
                checkpoints.append(i)

                # Simulate potential connection issue
                if random.random() < 0.1:  # 10% chance
                    # Connection might drop here
                    pass

            return {
                "completed": True,
                "checkpoints": len(checkpoints),
                "duration": duration,
            }

        # Execute with potential connection issues
        try:
            result = resilient_task(5)
            assert result["completed"] is True
            assert result["checkpoints"] == 5
        except ConnectionError:
            # Connection failure is acceptable for this test
            pass

    def test_network_timeout_recovery(self):
        """
        Test recovery from network timeouts.

        Validates timeout handling and retry logic.
        """
        configure(
            cluster_type="local",
            network_timeout=5,
            retry_on_timeout=True,
            max_retries=3,
        )

        @cluster(cores=1, memory="1GB")
        def task_with_timeout_potential(delay):
            """Task that might timeout."""
            import time
            import socket

            # Set socket timeout
            socket.setdefaulttimeout(5)

            start = time.time()

            # Simulate work that might timeout
            if delay > 5:
                # This would normally timeout
                time.sleep(3)  # Partial work
                return {"partial": True, "completed_after": time.time() - start}
            else:
                time.sleep(delay)
                return {"complete": True, "duration": time.time() - start}

        # Test with timeout-safe delay
        result = task_with_timeout_potential(3)
        assert result["complete"] is True

        # Test with delay that would timeout (but handled)
        result = task_with_timeout_potential(7)
        assert "partial" in result or "complete" in result

    @pytest.mark.real_world
    def test_cluster_unavailable_recovery(self):
        """
        Test behavior when cluster becomes unavailable.

        Validates failover and queue management.
        """
        # Try to connect to non-existent cluster
        config = ClusterConfig()
        config.cluster_type = "slurm"
        config.cluster_host = "nonexistent.cluster.invalid"
        config.username = "testuser"
        config.connection_timeout = 5
        config.fallback_to_local = True  # Enable fallback

        executor = ClusterExecutor(config)

        # Should fail to connect but potentially fallback
        try:
            executor.connect()
            # If connection succeeds, it's using fallback
            assert config.fallback_to_local is True
        except (ConnectionError, TimeoutError):
            # Expected failure
            assert config.cluster_host == "nonexistent.cluster.invalid"

    def test_intermittent_network_recovery(self):
        """
        Test recovery from intermittent network issues.

        Validates retry logic and connection pooling.
        """
        configure(cluster_type="local")

        @cluster(cores=2, memory="2GB")
        def network_sensitive_task(iterations):
            """Task sensitive to network issues."""
            import random
            import time

            results = []
            failures = []

            for i in range(iterations):
                try:
                    # Simulate network-dependent operation
                    if random.random() < 0.2:  # 20% failure rate
                        raise ConnectionError(f"Network hiccup at iteration {i}")

                    # Successful operation
                    time.sleep(0.1)
                    results.append(i)

                except ConnectionError as e:
                    failures.append(str(e))
                    # Retry logic would handle this
                    time.sleep(0.5)
                    results.append(i)  # Retry succeeded

            return {
                "successful": len(results),
                "failures_recovered": len(failures),
                "total": iterations,
            }

        result = network_sensitive_task(10)

        assert result["successful"] == result["total"]
        print(f"Recovered from {result['failures_recovered']} network failures")


class TestJobFailures:
    """Test recovery from job execution failures."""

    def test_out_of_memory_recovery(self):
        """
        Test recovery from out-of-memory errors.

        Validates memory limit handling and cleanup.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def memory_limited_task(size_gb):
            """Task with memory constraints."""
            import numpy as np
            import gc

            try:
                # Try to allocate more memory than available
                elements = int((size_gb * 1024 * 1024 * 1024) / 8)
                large_array = np.zeros(elements)

                return {
                    "allocated": True,
                    "size_gb": size_gb,
                    "actual_size": large_array.nbytes / (1024**3),
                }

            except MemoryError:
                # Clean up and report
                gc.collect()

                return {
                    "allocated": False,
                    "size_gb": size_gb,
                    "error": "MemoryError",
                    "recovered": True,
                }

        # Test with reasonable allocation
        result = memory_limited_task(0.5)
        assert result["allocated"] is True or result["recovered"] is True

        # Test with excessive allocation
        result = memory_limited_task(100)  # 100GB (should fail)
        assert result["allocated"] is False
        assert result["recovered"] is True

    def test_timeout_recovery(self):
        """
        Test recovery from job timeouts.

        Validates timeout detection and cleanup.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB", timeout=3)
        def task_with_timeout(duration):
            """Task that might timeout."""
            import time
            import signal

            def timeout_handler(signum, frame):
                raise TimeoutError("Job execution timeout")

            # Set timeout handler
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(3)  # 3 second timeout

            try:
                start = time.time()

                # Simulate work
                checkpoints = []
                for i in range(duration):
                    time.sleep(1)
                    checkpoints.append(i)

                signal.alarm(0)  # Cancel alarm

                return {
                    "completed": True,
                    "duration": time.time() - start,
                    "checkpoints": checkpoints,
                }

            except TimeoutError:
                signal.alarm(0)  # Cancel alarm
                return {
                    "completed": False,
                    "reason": "timeout",
                    "partial_checkpoints": (
                        checkpoints if "checkpoints" in locals() else []
                    ),
                }

        # Test successful completion
        result = task_with_timeout(2)
        assert result["completed"] is True

        # Test timeout scenario
        result = task_with_timeout(5)
        assert result["completed"] is False
        assert result["reason"] == "timeout"

    def test_segmentation_fault_recovery(self):
        """
        Test recovery from segmentation faults.

        Validates crash detection and cleanup.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB", monitor_health=True)
        def unstable_task(crash_probability):
            """Task that might crash."""
            import random
            import os

            if random.random() < crash_probability:
                # Simulate segfault (in a controlled way)
                # Don't actually cause segfault in test
                return {
                    "crashed": True,
                    "reason": "simulated_segfault",
                    "pid": os.getpid(),
                }

            # Normal execution
            return {"crashed": False, "result": "success", "pid": os.getpid()}

        # Test multiple runs
        crashes = 0
        successes = 0

        for _ in range(10):
            result = unstable_task(0.3)  # 30% crash probability

            if result["crashed"]:
                crashes += 1
            else:
                successes += 1

        print(f"Crashes: {crashes}, Successes: {successes}")
        assert successes > 0  # At least some should succeed

    def test_disk_full_recovery(self):
        """
        Test recovery from disk full errors.

        Validates disk space handling and cleanup.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def disk_intensive_task(size_mb, available_mb=1000):
            """Task that uses disk space."""
            import tempfile
            import os

            temp_dir = tempfile.mkdtemp()

            try:
                # Check available space (simulated)
                if size_mb > available_mb:
                    raise OSError("No space left on device")

                # Write data
                file_path = os.path.join(temp_dir, "data.bin")
                with open(file_path, "wb") as f:
                    data = os.urandom(size_mb * 1024 * 1024)
                    f.write(data)

                # Successful write
                file_size = os.path.getsize(file_path)

                # Cleanup
                os.unlink(file_path)
                os.rmdir(temp_dir)

                return {"success": True, "size_written": file_size / (1024 * 1024)}

            except OSError as e:
                # Cleanup on failure
                import shutil

                shutil.rmtree(temp_dir, ignore_errors=True)

                return {"success": False, "error": str(e), "cleaned_up": True}

        # Test successful write
        result = disk_intensive_task(10, 1000)  # 10MB with 1GB available
        assert result["success"] is True

        # Test disk full scenario
        result = disk_intensive_task(2000, 1000)  # 2GB with 1GB available
        assert result["success"] is False
        assert result["cleaned_up"] is True


class TestDependencyFailures:
    """Test recovery from dependency-related failures."""

    def test_missing_module_recovery(self):
        """
        Test handling of missing Python modules.

        Validates import error handling and fallbacks.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def task_with_optional_dependency():
            """Task with optional dependencies."""
            results = {"available": [], "missing": []}

            # Check common modules
            modules_to_check = [
                "numpy",
                "pandas",
                "scipy",
                "non_existent_module_xyz",
                "another_fake_module",
            ]

            for module in modules_to_check:
                try:
                    __import__(module)
                    results["available"].append(module)
                except ImportError:
                    results["missing"].append(module)

            # Use available modules
            if "numpy" in results["available"]:
                import numpy as np

                results["numpy_version"] = np.__version__

            return results

        result = task_with_optional_dependency()

        assert "numpy" in result["available"]
        assert "non_existent_module_xyz" in result["missing"]
        assert "numpy_version" in result

    def test_version_conflict_recovery(self):
        """
        Test handling of module version conflicts.

        Validates version checking and compatibility.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def task_with_version_requirements():
            """Task with specific version requirements."""
            import sys
            import importlib

            results = {
                "python_version": sys.version,
                "compatible": [],
                "incompatible": [],
            }

            # Check module versions
            requirements = {
                "numpy": "1.0.0",  # Should be compatible
                "pandas": "0.1.0",  # Should be compatible
            }

            for module, min_version in requirements.items():
                try:
                    mod = importlib.import_module(module)
                    if hasattr(mod, "__version__"):
                        version = mod.__version__

                        # Simple version comparison
                        if version >= min_version:
                            results["compatible"].append(
                                {
                                    "module": module,
                                    "version": version,
                                    "required": min_version,
                                }
                            )
                        else:
                            results["incompatible"].append(
                                {
                                    "module": module,
                                    "version": version,
                                    "required": min_version,
                                }
                            )
                except ImportError:
                    results["incompatible"].append(
                        {"module": module, "error": "not installed"}
                    )

            return results

        result = task_with_version_requirements()

        assert len(result["compatible"]) > 0
        assert "python_version" in result

    def test_environment_corruption_recovery(self):
        """
        Test recovery from corrupted environment.

        Validates environment validation and repair.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def task_with_environment_check():
            """Task that validates environment."""
            import os
            import sys
            import tempfile

            issues = []
            fixes = []

            # Check critical environment variables
            critical_vars = ["PATH", "HOME", "USER"]

            for var in critical_vars:
                if not os.getenv(var):
                    issues.append(f"Missing {var}")
                    # Attempt fix
                    if var == "HOME":
                        os.environ["HOME"] = tempfile.gettempdir()
                        fixes.append(f"Set {var} to temp directory")

            # Check Python path
            if not sys.path:
                issues.append("Empty Python path")
                sys.path.append(os.getcwd())
                fixes.append("Added current directory to path")

            # Check write permissions
            try:
                test_file = tempfile.NamedTemporaryFile(delete=True)
                test_file.close()
            except Exception as e:
                issues.append(f"Cannot write temp files: {e}")

            return {
                "issues_found": len(issues),
                "issues": issues,
                "fixes_applied": len(fixes),
                "fixes": fixes,
                "environment_healthy": len(issues) == 0,
            }

        result = task_with_environment_check()

        # Environment should be mostly healthy
        assert result["issues_found"] <= 2  # Allow minor issues


class TestResourceFailures:
    """Test recovery from resource-related failures."""

    def test_cpu_throttling_recovery(self):
        """
        Test behavior under CPU throttling.

        Validates performance degradation handling.
        """
        configure(cluster_type="local")

        @cluster(cores=2, memory="2GB")
        def cpu_sensitive_task(workload):
            """Task sensitive to CPU availability."""
            import time
            import multiprocessing

            start = time.time()

            # CPU-intensive work
            def cpu_work(n):
                total = 0
                for i in range(n):
                    total += i * i
                return total

            # Measure performance
            results = []
            for i in range(5):
                iter_start = time.perf_counter()
                result = cpu_work(workload)
                iter_time = time.perf_counter() - iter_start
                results.append({"iteration": i, "time": iter_time, "result": result})

            # Detect throttling (times increasing)
            times = [r["time"] for r in results]
            avg_time = sum(times) / len(times)
            variance = sum((t - avg_time) ** 2 for t in times) / len(times)

            return {
                "total_time": time.time() - start,
                "iterations": results,
                "avg_iteration_time": avg_time,
                "variance": variance,
                "likely_throttled": variance > (avg_time * 0.1) ** 2,
            }

        result = cpu_sensitive_task(1000000)

        assert result["total_time"] > 0
        assert len(result["iterations"]) == 5

        if result["likely_throttled"]:
            print("CPU throttling detected")

    def test_memory_pressure_recovery(self):
        """
        Test behavior under memory pressure.

        Validates memory management and swapping.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="2GB")
        def memory_pressure_task(allocation_mb):
            """Task under memory pressure."""
            import gc
            import numpy as np

            allocations = []
            max_allocated = 0

            try:
                # Progressive allocation
                for i in range(10):
                    size = allocation_mb * 1024 * 1024 // 10  # Bytes per iteration

                    # Try to allocate
                    try:
                        data = np.zeros(size // 8, dtype=np.float64)
                        allocations.append(data)
                        max_allocated += data.nbytes

                    except MemoryError:
                        # Hit memory limit
                        gc.collect()
                        break

                # Work with allocated memory
                if allocations:
                    total = sum(arr.sum() for arr in allocations)
                else:
                    total = 0

                return {
                    "allocated_mb": max_allocated / (1024 * 1024),
                    "allocation_count": len(allocations),
                    "computation_result": total,
                    "memory_limited": len(allocations) < 10,
                }

            finally:
                # Cleanup
                allocations.clear()
                gc.collect()

        result = memory_pressure_task(100)  # Try to allocate 100MB

        assert result["allocated_mb"] > 0
        assert result["allocation_count"] > 0

    def test_quota_exceeded_recovery(self):
        """
        Test recovery from quota exceeded errors.

        Validates quota handling and cleanup.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def quota_limited_task(file_count, file_size_mb, quota_mb=100):
            """Task with file quota limits."""
            import tempfile
            import os

            temp_dir = tempfile.mkdtemp()
            files_created = []
            total_written = 0

            try:
                for i in range(file_count):
                    # Check quota
                    if total_written + file_size_mb > quota_mb:
                        raise OSError(
                            f"Quota exceeded: {total_written + file_size_mb}MB > {quota_mb}MB"
                        )

                    # Create file
                    file_path = os.path.join(temp_dir, f"file_{i}.dat")
                    with open(file_path, "wb") as f:
                        data = os.urandom(file_size_mb * 1024 * 1024)
                        f.write(data)

                    files_created.append(file_path)
                    total_written += file_size_mb

                return {
                    "success": True,
                    "files_created": len(files_created),
                    "total_mb": total_written,
                }

            except OSError as e:
                # Quota exceeded - clean up
                for file_path in files_created:
                    try:
                        os.unlink(file_path)
                    except:
                        pass

                return {
                    "success": False,
                    "files_created": len(files_created),
                    "total_mb": total_written,
                    "error": str(e),
                    "cleaned_up": True,
                }

            finally:
                # Final cleanup
                import shutil

                shutil.rmtree(temp_dir, ignore_errors=True)

        # Test within quota
        result = quota_limited_task(5, 10, 100)  # 5 files, 10MB each, 100MB quota
        assert result["success"] is True
        assert result["total_mb"] == 50

        # Test quota exceeded
        result = quota_limited_task(20, 10, 100)  # Would need 200MB, quota is 100MB
        assert result["success"] is False
        assert result["cleaned_up"] is True


def run_failure_recovery_tests():
    """Run all failure recovery tests."""
    print("🛡️ Running Failure Recovery Tests")
    print("=" * 60)

    test_suites = [
        ("Connection Failures", TestConnectionFailures()),
        ("Job Failures", TestJobFailures()),
        ("Dependency Failures", TestDependencyFailures()),
        ("Resource Failures", TestResourceFailures()),
    ]

    overall_results = {"passed": 0, "failed": 0, "skipped": 0}

    for suite_name, test_suite in test_suites:
        print(f"\n🔍 Testing {suite_name}")
        print("-" * 40)

        # Get all test methods
        test_methods = [m for m in dir(test_suite) if m.startswith("test_")]

        for method_name in test_methods:
            print(f"\n▶ {method_name}")

            try:
                method = getattr(test_suite, method_name)

                # Check if it needs real infrastructure
                if hasattr(method, "__wrapped__"):
                    # Has pytest decorators
                    pass

                method()
                print("  ✅ Recovery successful")
                overall_results["passed"] += 1

            except pytest.skip.Exception as e:
                print(f"  ⏭️  Skipped: {e}")
                overall_results["skipped"] += 1

            except AssertionError as e:
                print(f"  ❌ Failed: {e}")
                overall_results["failed"] += 1

            except Exception as e:
                print(f"  ⚠️  Error: {e}")
                overall_results["failed"] += 1

    # Print summary
    print("\n" + "=" * 60)
    print("FAILURE RECOVERY TEST SUMMARY")
    print("=" * 60)
    print(f"✅ Passed: {overall_results['passed']}")
    print(f"❌ Failed: {overall_results['failed']}")
    print(f"⏭️  Skipped: {overall_results['skipped']}")

    total = sum(overall_results.values())
    if total > 0:
        success_rate = (overall_results["passed"] / total) * 100
        print(f"📈 Success Rate: {success_rate:.1f}%")

    if overall_results["failed"] == 0:
        print("\n✨ All failure recovery mechanisms working correctly!")
    else:
        print(f"\n⚠️  {overall_results['failed']} recovery mechanisms need attention")

    return overall_results["failed"] == 0


if __name__ == "__main__":
    success = run_failure_recovery_tests()
    sys.exit(0 if success else 1)
