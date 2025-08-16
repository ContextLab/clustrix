"""
Performance benchmark tests using real infrastructure.

These tests measure and validate clustrix performance characteristics
using actual infrastructure without mocks.
"""

import pytest
import os
import time
import statistics
import json
import tempfile
from pathlib import Path
from typing import List, Dict, Any
import numpy as np
import pandas as pd
from clustrix import cluster, configure
from clustrix.config import ClusterConfig


class PerformanceBenchmark:
    """Base class for performance benchmarks."""

    def __init__(self):
        self.results = []
        self.metrics = {}

    def measure_execution_time(self, func, *args, **kwargs):
        """Measure execution time of a function."""
        start = time.perf_counter()
        result = func(*args, **kwargs)
        duration = time.perf_counter() - start

        return result, duration

    def calculate_statistics(self, measurements: List[float]) -> Dict[str, float]:
        """Calculate statistics from measurements."""
        if not measurements:
            return {}

        return {
            "mean": statistics.mean(measurements),
            "median": statistics.median(measurements),
            "stdev": statistics.stdev(measurements) if len(measurements) > 1 else 0,
            "min": min(measurements),
            "max": max(measurements),
            "p95": (
                np.percentile(measurements, 95)
                if len(measurements) > 1
                else measurements[0]
            ),
            "p99": (
                np.percentile(measurements, 99)
                if len(measurements) > 1
                else measurements[0]
            ),
        }


class TestJobSubmissionPerformance(PerformanceBenchmark):
    """Benchmark job submission performance."""

    def test_single_job_submission_latency(self):
        """
        Measure latency for single job submission.

        Target: < 1 second for local, < 5 seconds for remote.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def minimal_task():
            """Minimal task for latency testing."""
            return "completed"

        # Warm up
        minimal_task()

        # Measure multiple submissions
        latencies = []
        for _ in range(10):
            start = time.perf_counter()
            result = minimal_task()
            latency = time.perf_counter() - start
            latencies.append(latency)

            assert result == "completed"

        stats = self.calculate_statistics(latencies)

        print(f"\nJob Submission Latency:")
        print(f"  Mean: {stats['mean']:.3f}s")
        print(f"  Median: {stats['median']:.3f}s")
        print(f"  P95: {stats['p95']:.3f}s")

        # Performance assertions
        assert stats["median"] < 1.0  # Local should be fast
        assert stats["p95"] < 2.0  # Even 95th percentile should be reasonable

    def test_parallel_job_submission_throughput(self):
        """
        Measure throughput for parallel job submissions.

        Target: > 10 jobs/second for local execution.
        """
        configure(cluster_type="local", max_parallel_jobs=20)

        @cluster(cores=1, memory="512MB")
        def quick_job(job_id):
            """Quick job for throughput testing."""
            return {"job_id": job_id, "status": "completed"}

        # Submit many jobs in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed

        num_jobs = 50
        start = time.perf_counter()

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(quick_job, i): i for i in range(num_jobs)}

            results = []
            for future in as_completed(futures):
                results.append(future.result())

        total_time = time.perf_counter() - start
        throughput = num_jobs / total_time

        print(f"\nParallel Job Submission Throughput:")
        print(f"  Jobs: {num_jobs}")
        print(f"  Time: {total_time:.2f}s")
        print(f"  Throughput: {throughput:.1f} jobs/second")

        assert len(results) == num_jobs
        assert throughput > 10  # Should handle >10 jobs/second locally

    @pytest.mark.real_world
    def test_remote_submission_performance(self):
        """
        Measure performance for remote job submission.

        Tests SSH-based submission performance.
        """
        ssh_host = os.getenv("TEST_SSH_HOST", "localhost")
        ssh_port = int(os.getenv("TEST_SSH_PORT", "2222"))

        if ssh_host == "localhost" and ssh_port == 2222:
            # Check if test SSH server is running
            import socket

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex((ssh_host, ssh_port))
                sock.close()
                if result != 0:
                    pytest.skip("SSH test server not available")
            except:
                pytest.skip("Cannot connect to SSH test server")

        configure(
            cluster_type="ssh",
            cluster_host=ssh_host,
            cluster_port=ssh_port,
            username=os.getenv("TEST_SSH_USER", "testuser"),
            password=os.getenv("TEST_SSH_PASS", "testpass"),
        )

        @cluster(cores=2, memory="2GB")
        def remote_task(size):
            """Task executed on remote cluster."""
            import numpy as np

            data = np.random.randn(size, size)
            return {"mean": float(np.mean(data)), "size": data.shape}

        # Measure remote submission latency
        latencies = []
        for size in [10, 50, 100]:
            start = time.perf_counter()
            result = remote_task(size)
            latency = time.perf_counter() - start
            latencies.append(latency)

            assert result["size"] == (size, size)

        stats = self.calculate_statistics(latencies)

        print(f"\nRemote Job Submission Performance:")
        print(f"  Mean latency: {stats['mean']:.2f}s")
        print(f"  Network overhead: ~{stats['mean'] - stats['min']:.2f}s")

        # Remote submission should complete in reasonable time
        assert stats["mean"] < 30  # Should complete within 30 seconds


class TestDataTransferPerformance(PerformanceBenchmark):
    """Benchmark data transfer performance."""

    def test_input_data_serialization_speed(self):
        """
        Measure serialization speed for input data.

        Target: > 100 MB/s for local serialization.
        """
        configure(cluster_type="local")

        @cluster(cores=2, memory="4GB")
        def process_data(data_dict):
            """Process serialized data."""
            total_size = sum(
                arr.nbytes if hasattr(arr, "nbytes") else len(str(arr))
                for arr in data_dict.values()
            )
            return {"processed_bytes": total_size}

        # Test with different data sizes
        sizes_mb = [1, 10, 50, 100]
        transfer_rates = []

        for size_mb in sizes_mb:
            # Create test data
            num_elements = (size_mb * 1024 * 1024) // 8  # 8 bytes per float64
            test_data = {
                "array": np.random.randn(num_elements),
                "metadata": {"size": size_mb, "type": "numpy"},
            }

            # Measure transfer time
            start = time.perf_counter()
            result = process_data(test_data)
            duration = time.perf_counter() - start

            # Calculate transfer rate
            rate_mbps = size_mb / duration
            transfer_rates.append(rate_mbps)

            print(f"  {size_mb}MB: {rate_mbps:.1f} MB/s")

        avg_rate = statistics.mean(transfer_rates)
        print(f"\nAverage serialization rate: {avg_rate:.1f} MB/s")

        # Local serialization should be fast
        assert avg_rate > 50  # At least 50 MB/s for local

    def test_result_deserialization_speed(self):
        """
        Measure deserialization speed for results.

        Target: > 100 MB/s for local deserialization.
        """
        configure(cluster_type="local")

        @cluster(cores=2, memory="4GB")
        def generate_large_result(size_mb):
            """Generate large result for deserialization testing."""
            import numpy as np

            num_elements = (size_mb * 1024 * 1024) // 8

            return {
                "data": np.random.randn(num_elements).tolist(),
                "stats": {"size_mb": size_mb, "elements": num_elements},
            }

        # Test different result sizes
        sizes_mb = [1, 5, 10, 25]
        deser_rates = []

        for size_mb in sizes_mb:
            start = time.perf_counter()
            result = generate_large_result(size_mb)
            duration = time.perf_counter() - start

            rate_mbps = size_mb / duration
            deser_rates.append(rate_mbps)

            assert result["stats"]["size_mb"] == size_mb

            print(f"  {size_mb}MB result: {rate_mbps:.1f} MB/s")

        avg_rate = statistics.mean(deser_rates)
        print(f"\nAverage deserialization rate: {avg_rate:.1f} MB/s")

        assert avg_rate > 30  # At least 30 MB/s

    def test_file_transfer_performance(self):
        """
        Measure file transfer performance.

        Tests both upload and download speeds.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="2GB")
        def file_transfer_test(file_size_mb):
            """Test file transfer speeds."""
            import tempfile
            import os

            # Create test file
            with tempfile.NamedTemporaryFile(delete=False) as f:
                # Write test data
                data = os.urandom(file_size_mb * 1024 * 1024)

                write_start = time.perf_counter()
                f.write(data)
                f.flush()
                write_time = time.perf_counter() - write_start

                temp_path = f.name

            # Read test file
            read_start = time.perf_counter()
            with open(temp_path, "rb") as f:
                read_data = f.read()
            read_time = time.perf_counter() - read_start

            # Cleanup
            os.unlink(temp_path)

            return {
                "file_size_mb": file_size_mb,
                "write_time": write_time,
                "read_time": read_time,
                "write_rate_mbps": file_size_mb / write_time,
                "read_rate_mbps": file_size_mb / read_time,
            }

        # Test different file sizes
        results = []
        for size_mb in [1, 10, 50]:
            result = file_transfer_test(size_mb)
            results.append(result)

            print(
                f"  {size_mb}MB: Write={result['write_rate_mbps']:.1f} MB/s, "
                f"Read={result['read_rate_mbps']:.1f} MB/s"
            )

        # Calculate averages
        avg_write = statistics.mean(r["write_rate_mbps"] for r in results)
        avg_read = statistics.mean(r["read_rate_mbps"] for r in results)

        print(f"\nAverage file transfer rates:")
        print(f"  Write: {avg_write:.1f} MB/s")
        print(f"  Read: {avg_read:.1f} MB/s")

        # File I/O should be reasonably fast
        assert avg_write > 50  # At least 50 MB/s write
        assert avg_read > 100  # At least 100 MB/s read


class TestComputationPerformance(PerformanceBenchmark):
    """Benchmark computation performance."""

    def test_cpu_bound_performance(self):
        """
        Measure CPU-bound computation performance.

        Tests parallel CPU utilization efficiency.
        """
        configure(cluster_type="local")

        @cluster(cores=4, memory="4GB")
        def cpu_intensive_task(n):
            """CPU-intensive computation."""
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor

            def compute_primes(limit):
                """Find primes up to limit."""
                primes = []
                for num in range(2, limit + 1):
                    is_prime = True
                    for i in range(2, int(num**0.5) + 1):
                        if num % i == 0:
                            is_prime = False
                            break
                    if is_prime:
                        primes.append(num)
                return len(primes)

            # Split work across cores
            chunk_size = n // 4
            ranges = [(i * chunk_size, (i + 1) * chunk_size) for i in range(4)]

            start = time.perf_counter()

            with ProcessPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(compute_primes, end - start)
                    for start, end in ranges
                ]
                results = [f.result() for f in futures]

            parallel_time = time.perf_counter() - start

            # Also measure sequential for comparison
            start = time.perf_counter()
            sequential_result = compute_primes(n)
            sequential_time = time.perf_counter() - start

            return {
                "parallel_time": parallel_time,
                "sequential_time": sequential_time,
                "speedup": sequential_time / parallel_time,
                "efficiency": (sequential_time / parallel_time) / 4,
                "result": sum(results),
            }

        # Test with different problem sizes
        for n in [10000, 50000, 100000]:
            result = cpu_intensive_task(n)

            print(f"\nCPU Performance (n={n}):")
            print(f"  Sequential: {result['sequential_time']:.2f}s")
            print(f"  Parallel: {result['parallel_time']:.2f}s")
            print(f"  Speedup: {result['speedup']:.2f}x")
            print(f"  Efficiency: {result['efficiency']:.1%}")

            # Should achieve reasonable parallel efficiency
            assert result["speedup"] > 1.5  # At least 1.5x speedup
            assert result["efficiency"] > 0.4  # At least 40% efficiency

    def test_memory_intensive_performance(self):
        """
        Measure memory-intensive computation performance.

        Tests memory bandwidth and allocation speed.
        """
        configure(cluster_type="local")

        @cluster(cores=2, memory="8GB")
        def memory_intensive_task(size_gb):
            """Memory-intensive computation."""
            import numpy as np
            import gc

            # Allocate large arrays
            elements = int((size_gb * 1024 * 1024 * 1024) / 8)  # 8 bytes per float64

            allocation_start = time.perf_counter()
            array1 = np.random.randn(elements)
            allocation_time = time.perf_counter() - allocation_start

            # Memory bandwidth test - copy operation
            copy_start = time.perf_counter()
            array2 = array1.copy()
            copy_time = time.perf_counter() - copy_start

            # Computation on large arrays
            compute_start = time.perf_counter()
            result = np.dot(array1[:1000], array2[:1000])
            compute_time = time.perf_counter() - compute_start

            # Calculate bandwidth
            bandwidth_gbps = (size_gb * 2) / copy_time  # Read + write

            # Cleanup
            del array1, array2
            gc.collect()

            return {
                "size_gb": size_gb,
                "allocation_time": allocation_time,
                "copy_time": copy_time,
                "compute_time": compute_time,
                "bandwidth_gbps": bandwidth_gbps,
            }

        # Test with different memory sizes
        for size_gb in [0.1, 0.5, 1.0]:
            result = memory_intensive_task(size_gb)

            print(f"\nMemory Performance ({size_gb}GB):")
            print(f"  Allocation: {result['allocation_time']:.3f}s")
            print(f"  Copy: {result['copy_time']:.3f}s")
            print(f"  Bandwidth: {result['bandwidth_gbps']:.1f} GB/s")

            # Memory operations should be reasonably fast
            assert result["bandwidth_gbps"] > 1.0  # At least 1 GB/s

    def test_io_bound_performance(self):
        """
        Measure I/O-bound computation performance.

        Tests file I/O and data processing.
        """
        configure(cluster_type="local")

        @cluster(cores=2, memory="4GB")
        def io_intensive_task(num_files, file_size_mb):
            """I/O-intensive computation."""
            import tempfile
            import os

            temp_dir = tempfile.mkdtemp()

            try:
                # Write phase
                write_start = time.perf_counter()
                files_written = []

                for i in range(num_files):
                    file_path = os.path.join(temp_dir, f"data_{i}.bin")
                    data = os.urandom(file_size_mb * 1024 * 1024)

                    with open(file_path, "wb") as f:
                        f.write(data)

                    files_written.append(file_path)

                write_time = time.perf_counter() - write_start

                # Read phase
                read_start = time.perf_counter()
                total_bytes = 0

                for file_path in files_written:
                    with open(file_path, "rb") as f:
                        data = f.read()
                        total_bytes += len(data)

                read_time = time.perf_counter() - read_start

                # Cleanup
                for file_path in files_written:
                    os.unlink(file_path)
                os.rmdir(temp_dir)

                total_mb = num_files * file_size_mb

                return {
                    "total_mb": total_mb,
                    "write_time": write_time,
                    "read_time": read_time,
                    "write_throughput_mbps": total_mb / write_time,
                    "read_throughput_mbps": total_mb / read_time,
                }

            except Exception as e:
                # Cleanup on error
                import shutil

                shutil.rmtree(temp_dir, ignore_errors=True)
                raise e

        # Test I/O performance
        result = io_intensive_task(10, 10)  # 10 files, 10MB each

        print(f"\nI/O Performance (100MB total):")
        print(f"  Write: {result['write_throughput_mbps']:.1f} MB/s")
        print(f"  Read: {result['read_throughput_mbps']:.1f} MB/s")

        # I/O should achieve reasonable throughput
        assert result["write_throughput_mbps"] > 50  # At least 50 MB/s write
        assert result["read_throughput_mbps"] > 100  # At least 100 MB/s read


class TestScalabilityPerformance(PerformanceBenchmark):
    """Benchmark scalability characteristics."""

    def test_weak_scaling(self):
        """
        Test weak scaling (constant work per core).

        Efficiency should remain high as cores increase.
        """
        configure(cluster_type="local")

        def create_scaled_task(cores):
            @cluster(cores=cores, memory=f"{cores * 2}GB")
            def scaled_computation(work_per_core):
                """Computation that scales with cores."""
                import multiprocessing
                from concurrent.futures import ProcessPoolExecutor

                def unit_work(size):
                    """Unit of work per core."""
                    total = 0
                    for i in range(size):
                        total += i * i
                    return total

                # Each core does same amount of work
                with ProcessPoolExecutor(max_workers=cores) as executor:
                    futures = [
                        executor.submit(unit_work, work_per_core) for _ in range(cores)
                    ]
                    results = [f.result() for f in futures]

                return sum(results)

            return scaled_computation

        # Test weak scaling
        work_per_core = 1000000
        results = []

        for cores in [1, 2, 4]:
            task = create_scaled_task(cores)

            start = time.perf_counter()
            result = task(work_per_core)
            duration = time.perf_counter() - start

            results.append(
                {
                    "cores": cores,
                    "time": duration,
                    "efficiency": results[0]["time"] / duration if results else 1.0,
                }
            )

            print(f"  {cores} cores: {duration:.2f}s")

        # Weak scaling should maintain efficiency
        for r in results[1:]:
            assert r["efficiency"] > 0.7  # At least 70% efficiency

    def test_strong_scaling(self):
        """
        Test strong scaling (fixed total work).

        Time should decrease as cores increase.
        """
        configure(cluster_type="local")

        def create_fixed_task(cores):
            @cluster(cores=cores, memory="4GB")
            def fixed_computation(total_work):
                """Fixed computation distributed across cores."""
                import multiprocessing
                from concurrent.futures import ProcessPoolExecutor

                def partial_work(start, end):
                    """Compute partial sum."""
                    total = 0
                    for i in range(start, end):
                        total += i * i
                    return total

                # Divide work among cores
                chunk_size = total_work // cores
                ranges = [
                    (
                        i * chunk_size,
                        (i + 1) * chunk_size if i < cores - 1 else total_work,
                    )
                    for i in range(cores)
                ]

                with ProcessPoolExecutor(max_workers=cores) as executor:
                    futures = [
                        executor.submit(partial_work, start, end)
                        for start, end in ranges
                    ]
                    results = [f.result() for f in futures]

                return sum(results)

            return fixed_computation

        # Test strong scaling
        total_work = 10000000
        results = []

        for cores in [1, 2, 4]:
            task = create_fixed_task(cores)

            start = time.perf_counter()
            result = task(total_work)
            duration = time.perf_counter() - start

            speedup = results[0]["time"] / duration if results else 1.0

            results.append({"cores": cores, "time": duration, "speedup": speedup})

            print(f"  {cores} cores: {duration:.2f}s (speedup: {speedup:.2f}x)")

        # Strong scaling should show speedup
        assert results[1]["speedup"] > 1.5  # 2 cores > 1.5x speedup
        if len(results) > 2:
            assert results[2]["speedup"] > 2.0  # 4 cores > 2x speedup


def run_performance_benchmarks():
    """Run all performance benchmarks."""
    print("🚀 Running Performance Benchmarks")
    print("=" * 60)

    benchmarks = [
        ("Job Submission", TestJobSubmissionPerformance()),
        ("Data Transfer", TestDataTransferPerformance()),
        ("Computation", TestComputationPerformance()),
        ("Scalability", TestScalabilityPerformance()),
    ]

    all_results = {}

    for name, benchmark in benchmarks:
        print(f"\n📊 {name} Performance Benchmarks")
        print("-" * 40)

        # Run all test methods
        test_methods = [m for m in dir(benchmark) if m.startswith("test_")]

        for method_name in test_methods:
            print(f"\n▶ {method_name}")
            try:
                method = getattr(benchmark, method_name)
                method()
                print("  ✅ Passed performance targets")
            except AssertionError as e:
                print(f"  ⚠️  Performance below target: {e}")
            except Exception as e:
                print(f"  ❌ Error: {e}")

    print("\n" + "=" * 60)
    print("✨ Performance benchmark suite complete!")

    # Save results to file
    results_file = Path(__file__).parent / "performance_results.json"
    with open(results_file, "w") as f:
        json.dump(
            {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "results": all_results},
            f,
            indent=2,
        )

    print(f"📊 Results saved to {results_file}")


if __name__ == "__main__":
    run_performance_benchmarks()
