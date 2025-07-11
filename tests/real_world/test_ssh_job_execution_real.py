"""
Real SSH-based job execution tests using @cluster decorator.

These tests actually execute jobs on remote SSH servers and validate
the complete end-to-end workflow with the @cluster decorator.
"""

import pytest
import os
import time
import uuid
from pathlib import Path
from typing import List, Dict, Any

from clustrix import cluster, configure
from clustrix.config import ClusterConfig
from tests.real_world import TempResourceManager, credentials, test_manager


class TestRealSSHJobExecution:
    """Test real SSH-based job execution using @cluster decorator."""

    @pytest.fixture
    def ssh_config(self):
        """Get SSH configuration for testing."""
        ssh_creds = credentials.get_ssh_credentials()
        if not ssh_creds:
            pytest.skip("No SSH credentials available for testing")

        # Configure clustrix for SSH-based execution
        configure(
            cluster_type="ssh",
            cluster_host=ssh_creds["host"],
            username=ssh_creds["username"],
            password=ssh_creds.get("password"),
            key_file=ssh_creds.get("private_key_path"),
            remote_work_dir=f"/tmp/clustrix_ssh_test_{uuid.uuid4().hex[:8]}",
            python_executable="python3",  # Use python3 on tensor01
            cleanup_on_success=False,  # Don't cleanup so we can inspect files
            job_poll_interval=5,  # Poll every 5 seconds instead of 30
        )

        return ssh_creds

    @pytest.mark.real_world
    def test_simple_function_ssh_execution(self, ssh_config):
        """Test executing a simple function via SSH."""

        @cluster(cores=1, memory="1GB")
        def compute_fibonacci(n: int) -> int:
            """Compute Fibonacci number for testing."""
            if n <= 1:
                return n
            a, b = 0, 1
            for _ in range(2, n + 1):
                a, b = b, a + b
            return b

        # Execute job via SSH
        result = compute_fibonacci(10)

        # Validate result
        assert result == 55  # 10th Fibonacci number
        assert isinstance(result, int)

    @pytest.mark.real_world
    def test_function_with_ssh_environment(self, ssh_config):
        """Test SSH job that accesses remote environment."""

        @cluster(cores=1, memory="1GB")
        def get_remote_environment() -> Dict[str, str]:
            """Get remote system environment information."""
            import os
            import platform
            import socket

            return {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "architecture": platform.architecture()[0],
                "machine": platform.machine(),
                "processor": platform.processor(),
                "user": os.getenv("USER", "unknown"),
                "home": os.getenv("HOME", "unknown"),
                "pwd": os.getenv("PWD", "unknown"),
                "shell": os.getenv("SHELL", "unknown"),
                "term": os.getenv("TERM", "unknown"),
            }

        result = get_remote_environment()

        # Validate remote environment
        assert isinstance(result, dict)
        assert result["hostname"] != ""
        assert result["platform"] != ""
        assert result["python_version"] != ""
        assert result["user"] != "unknown"
        assert result["home"] != "unknown"

    @pytest.mark.real_world
    def test_ssh_file_operations(self, ssh_config):
        """Test SSH job with file operations on remote system."""

        @cluster(cores=1, memory="1GB")
        def remote_file_processing() -> Dict[str, Any]:
            """Process files on remote system."""
            import os
            import tempfile
            import json
            import hashlib

            # Create temporary directory
            work_dir = tempfile.mkdtemp(prefix="ssh_test_")

            try:
                # Create test files
                files_created = []

                for i in range(3):
                    file_path = os.path.join(work_dir, f"test_file_{i}.txt")
                    with open(file_path, "w") as f:
                        content = f"Test file {i}\n" * (i + 1)
                        f.write(content)
                        files_created.append(file_path)

                # Process files
                file_info = []
                total_size = 0

                for file_path in files_created:
                    with open(file_path, "r") as f:
                        content = f.read()

                    # Calculate file hash
                    file_hash = hashlib.md5(content.encode()).hexdigest()
                    file_size = len(content)
                    total_size += file_size

                    file_info.append(
                        {
                            "path": file_path,
                            "size": file_size,
                            "hash": file_hash,
                            "lines": content.count("\n"),
                        }
                    )

                # Create summary file
                summary_path = os.path.join(work_dir, "summary.json")
                summary_data = {
                    "files_processed": len(files_created),
                    "total_size": total_size,
                    "work_dir": work_dir,
                    "file_info": file_info,
                }

                with open(summary_path, "w") as f:
                    json.dump(summary_data, f, indent=2)

                return {
                    "work_dir": work_dir,
                    "files_created": len(files_created),
                    "total_size": total_size,
                    "summary_file_created": os.path.exists(summary_path),
                    "file_details": file_info,
                }

            finally:
                # Cleanup
                import shutil

                try:
                    shutil.rmtree(work_dir)
                except:
                    pass

        result = remote_file_processing()

        # Validate file operations
        assert isinstance(result, dict)
        assert result["files_created"] == 3
        assert result["total_size"] > 0
        assert result["summary_file_created"] is True
        assert len(result["file_details"]) == 3

    @pytest.mark.real_world
    def test_ssh_system_commands(self, ssh_config):
        """Test SSH job that executes system commands."""

        @cluster(cores=1, memory="1GB")
        def execute_system_commands() -> Dict[str, Any]:
            """Execute system commands on remote host."""
            import subprocess
            import os

            commands_result = {}

            # Test basic commands
            commands = [
                ("whoami", "Get current user"),
                ("pwd", "Get current directory"),
                ("date", "Get current date"),
                ("uname -a", "Get system information"),
                ("df -h", "Get disk usage"),
                ("free -h", "Get memory usage"),
                ("ps aux | head -10", "Get process list"),
            ]

            for cmd, description in commands:
                try:
                    result = subprocess.run(
                        cmd, shell=True, capture_output=True, text=True, timeout=10
                    )

                    commands_result[cmd] = {
                        "description": description,
                        "return_code": result.returncode,
                        "stdout": result.stdout.strip(),
                        "stderr": result.stderr.strip(),
                        "success": result.returncode == 0,
                    }
                except subprocess.TimeoutExpired:
                    commands_result[cmd] = {
                        "description": description,
                        "error": "Command timed out",
                    }
                except Exception as e:
                    commands_result[cmd] = {"description": description, "error": str(e)}

            # Get environment variables
            env_vars = ["HOME", "USER", "SHELL", "PATH"]
            environment = {var: os.getenv(var, "not_set") for var in env_vars}

            return {
                "commands_executed": len(commands),
                "successful_commands": sum(
                    1 for r in commands_result.values() if r.get("success", False)
                ),
                "commands_result": commands_result,
                "environment": environment,
            }

        result = execute_system_commands()

        # Validate system commands
        assert isinstance(result, dict)
        assert result["commands_executed"] == 7
        assert result["successful_commands"] > 0
        assert result["environment"]["USER"] != "not_set"
        assert result["environment"]["HOME"] != "not_set"

    @pytest.mark.real_world
    def test_ssh_python_environment(self, ssh_config):
        """Test SSH job that analyzes Python environment."""

        @cluster(cores=1, memory="1GB")
        def analyze_python_environment() -> Dict[str, Any]:
            """Analyze Python environment on remote host."""
            import sys
            import os
            import platform
            import subprocess

            # Get Python information
            python_info: Dict[str, Any] = {
                "version": sys.version,
                "version_info": list(sys.version_info),
                "executable": sys.executable,
                "platform": platform.platform(),
                "architecture": platform.architecture(),
                "prefix": sys.prefix,
                "path": sys.path[:5],  # First 5 paths
            }

            # Get installed packages
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "list"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                if result.returncode == 0:
                    packages = result.stdout.strip().split("\n")[2:]  # Skip headers
                    python_info["installed_packages"] = len(packages)
                    python_info["sample_packages"] = packages[:10]  # First 10 packages
                else:
                    python_info["pip_error"] = result.stderr
            except Exception as e:
                python_info["pip_error"] = str(e)

            # Test standard library imports
            standard_modules = [
                "os",
                "sys",
                "json",
                "time",
                "datetime",
                "math",
                "random",
                "collections",
                "itertools",
                "functools",
                "pathlib",
                "subprocess",
            ]

            import_results = {}
            for module in standard_modules:
                try:
                    __import__(module)
                    import_results[module] = "success"
                except ImportError as e:
                    import_results[module] = f"failed: {e}"

            return {
                "python_info": python_info,
                "standard_library_imports": import_results,
                "successful_imports": sum(
                    1 for r in import_results.values() if r == "success"
                ),
                "total_modules_tested": len(standard_modules),
            }

        result = analyze_python_environment()

        # Validate Python environment
        assert isinstance(result, dict)
        assert "python_info" in result
        assert result["python_info"]["version"] != ""
        assert result["successful_imports"] > 10  # Most standard modules should import
        assert result["total_modules_tested"] == 12

    @pytest.mark.real_world
    def test_ssh_parallel_execution(self, ssh_config):
        """Test parallel execution via SSH."""

        @cluster(cores=2, memory="2GB", parallel=True)
        def parallel_data_processing(data_sets: List[List[int]]) -> Dict[str, Any]:
            """Process multiple datasets in parallel."""
            import time
            import statistics
            import concurrent.futures

            def process_dataset(dataset):
                """Process a single dataset."""
                time.sleep(0.1)  # Simulate processing time
                return {
                    "size": len(dataset),
                    "sum": sum(dataset),
                    "mean": statistics.mean(dataset),
                    "median": statistics.median(dataset),
                    "min": min(dataset),
                    "max": max(dataset),
                }

            start_time = time.time()

            # Process datasets (clustering should parallelize this)
            results = []
            for dataset in data_sets:
                result = process_dataset(dataset)
                results.append(result)

            end_time = time.time()

            return {
                "datasets_processed": len(data_sets),
                "processing_time": end_time - start_time,
                "results": results,
                "total_data_points": sum(r["size"] for r in results),
                "average_processing_time": (end_time - start_time) / len(data_sets),
            }

        # Create test datasets
        test_datasets = [
            [1, 2, 3, 4, 5],
            [10, 20, 30, 40, 50],
            [100, 200, 300, 400, 500],
            [1000, 2000, 3000, 4000, 5000],
        ]

        result = parallel_data_processing(test_datasets)

        # Validate parallel execution
        assert isinstance(result, dict)
        assert result["datasets_processed"] == 4
        assert result["processing_time"] > 0
        assert result["total_data_points"] == 20
        assert len(result["results"]) == 4

    @pytest.mark.real_world
    def test_ssh_error_handling(self, ssh_config):
        """Test SSH job error handling."""

        @cluster(cores=1, memory="1GB")
        def ssh_error_test(error_scenario: str) -> Any:
            """Test different error scenarios via SSH."""
            import os
            import sys

            if error_scenario == "success":
                return {
                    "status": "success",
                    "hostname": os.uname().nodename,
                    "python_version": sys.version,
                    "message": "SSH job completed successfully",
                }
            elif error_scenario == "value_error":
                raise ValueError("Test value error in SSH job")
            elif error_scenario == "file_not_found":
                with open("/nonexistent/file/path.txt", "r") as f:
                    return f.read()
            elif error_scenario == "permission_denied":
                with open("/etc/shadow", "r") as f:
                    return f.read()
            elif error_scenario == "division_by_zero":
                return 42 / 0
            else:
                raise Exception(f"Unknown error scenario: {error_scenario}")

        # Test successful execution
        result = ssh_error_test("success")
        assert result["status"] == "success"
        assert result["hostname"] != ""
        assert result["python_version"] != ""

        # Test error handling
        with pytest.raises(ValueError):
            ssh_error_test("value_error")

        with pytest.raises(FileNotFoundError):
            ssh_error_test("file_not_found")

        with pytest.raises(ZeroDivisionError):
            ssh_error_test("division_by_zero")

    @pytest.mark.real_world
    def test_ssh_resource_monitoring(self, ssh_config):
        """Test SSH job that monitors resource usage."""

        @cluster(cores=1, memory="1GB")
        def monitor_ssh_resources() -> Dict[str, Any]:
            """Monitor resource usage during SSH job execution."""
            import os
            import psutil
            import time

            # Get initial resource usage
            process = psutil.Process(os.getpid())
            initial_memory = process.memory_info().rss / (1024**2)  # MB
            initial_cpu = process.cpu_percent()

            # Get system information
            system_info = {
                "cpu_count": psutil.cpu_count(),
                "memory_total_gb": psutil.virtual_memory().total / (1024**3),
                "memory_available_gb": psutil.virtual_memory().available / (1024**3),
                "memory_percent": psutil.virtual_memory().percent,
                "cpu_percent": psutil.cpu_percent(interval=1),
                "load_average": os.getloadavg() if hasattr(os, "getloadavg") else None,
            }

            # Perform some work to measure resource usage
            work_data = []
            for i in range(50000):
                work_data.append(i**2)

            # Get final resource usage
            final_memory = process.memory_info().rss / (1024**2)  # MB
            final_cpu = process.cpu_percent()

            return {
                "initial_memory_mb": initial_memory,
                "final_memory_mb": final_memory,
                "memory_used_mb": final_memory - initial_memory,
                "initial_cpu_percent": initial_cpu,
                "final_cpu_percent": final_cpu,
                "work_data_size": len(work_data),
                "system_info": system_info,
                "process_id": os.getpid(),
                "parent_process_id": os.getppid(),
            }

        result = monitor_ssh_resources()

        # Validate resource monitoring
        assert isinstance(result, dict)
        assert result["memory_used_mb"] >= 0
        assert result["work_data_size"] == 50000
        assert result["system_info"]["cpu_count"] > 0
        assert result["system_info"]["memory_total_gb"] > 0
        assert result["process_id"] > 0

    @pytest.mark.real_world
    def test_ssh_long_running_job(self, ssh_config):
        """Test long-running SSH job."""

        @cluster(cores=1, memory="1GB")
        def long_running_ssh_job() -> Dict[str, Any]:
            """Execute a long-running job via SSH."""
            import time
            import os

            start_time = time.time()

            # Simulate long-running computation
            progress_checkpoints = []
            total_iterations = 10

            for i in range(total_iterations):
                # Simulate work
                time.sleep(0.5)

                checkpoint_time = time.time()
                progress_checkpoints.append(
                    {
                        "iteration": i + 1,
                        "timestamp": checkpoint_time,
                        "elapsed": checkpoint_time - start_time,
                    }
                )

                # Safety check - don't run too long
                if checkpoint_time - start_time > 30:  # 30 seconds max
                    break

            end_time = time.time()

            return {
                "start_time": start_time,
                "end_time": end_time,
                "total_duration": end_time - start_time,
                "iterations_completed": len(progress_checkpoints),
                "progress_checkpoints": progress_checkpoints,
                "hostname": os.uname().nodename,
                "average_iteration_time": (end_time - start_time)
                / len(progress_checkpoints),
            }

        result = long_running_ssh_job()

        # Validate long-running job
        assert isinstance(result, dict)
        assert result["total_duration"] >= 4.0  # Should run for at least 4 seconds
        assert result["iterations_completed"] >= 8  # Should complete most iterations
        assert len(result["progress_checkpoints"]) >= 8
        assert result["average_iteration_time"] > 0

    @pytest.mark.real_world
    def test_ssh_network_operations(self, ssh_config):
        """Test SSH job with network operations."""

        @cluster(cores=1, memory="1GB")
        def test_network_operations() -> Dict[str, Any]:
            """Test network operations from SSH job."""
            import socket
            import subprocess
            import os

            network_info: Dict[str, Any] = {
                "hostname": socket.gethostname(),
                "fqdn": socket.getfqdn(),
                "local_ip": None,
                "interfaces": [],
                "connectivity_tests": {},
            }
            # Explicitly initialize connectivity_tests as a dict
            network_info["connectivity_tests"] = {}

            # Get local IP
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                network_info["local_ip"] = s.getsockname()[0]
                s.close()
            except:
                network_info["local_ip"] = "unknown"

            # Test connectivity
            test_hosts = ["google.com", "github.com", "8.8.8.8"]

            for host in test_hosts:
                try:
                    result = subprocess.run(
                        ["ping", "-c", "1", "-W", "5", host],
                        capture_output=True,
                        timeout=10,
                    )
                    network_info["connectivity_tests"][host] = {
                        "success": result.returncode == 0,
                        "return_code": result.returncode,
                    }
                except Exception as e:
                    network_info["connectivity_tests"][host] = {
                        "success": False,
                        "error": str(e),
                    }

            # Test DNS resolution
            dns_tests = ["google.com", "github.com", "localhost"]
            network_info["dns_tests"] = {}

            for hostname in dns_tests:
                try:
                    ip = socket.gethostbyname(hostname)
                    network_info["dns_tests"][hostname] = {"success": True, "ip": ip}
                except Exception as e:
                    network_info["dns_tests"][hostname] = {
                        "success": False,
                        "error": str(e),
                    }

            return network_info

        result = test_network_operations()

        # Validate network operations
        assert isinstance(result, dict)
        assert result["hostname"] != ""
        assert result["fqdn"] != ""
        assert "connectivity_tests" in result
        assert "dns_tests" in result
        assert result["dns_tests"]["localhost"]["success"] is True
