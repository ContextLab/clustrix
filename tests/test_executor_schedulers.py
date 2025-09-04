"""
Comprehensive tests for executor scheduler operations.

This module tests scheduler-specific job submission, monitoring, and management
for SLURM, PBS, SGE, SSH, and Kubernetes cluster types.

Tests focus on:
- Job submission workflows for each scheduler type
- Status monitoring and tracking
- Error handling and cancellation
- Two-venv environment setup
- Scheduler command mocking
"""

import os
import time
import pytest
import pickle
import tempfile
import threading
from unittest.mock import Mock, patch, MagicMock, call
from typing import Dict, Any

from clustrix.executor_schedulers import SchedulerManager
from clustrix.executor_kubernetes import KubernetesJobManager
from clustrix.config import ClusterConfig


class TestSchedulerManager:
    """Test the SchedulerManager class for traditional HPC schedulers."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock configuration for testing."""
        config = Mock(spec=ClusterConfig)
        config.cluster_type = "slurm"
        config.remote_work_dir = "/tmp/clustrix"
        config.use_two_venv = True
        config.venv_setup_timeout = 300
        config.cleanup_remote_files = True
        config.module_loads = []  # Empty list to avoid iteration issues
        config.environment_variables = {}  # Empty dict to avoid iteration issues
        config.python_executable = "python3.11"
        config.package_manager = "pip"
        return config

    @pytest.fixture
    def mock_connection_manager(self):
        """Create a mock connection manager."""
        conn_mgr = Mock()
        conn_mgr.ssh_client = Mock()
        conn_mgr.execute_remote_command = Mock()
        conn_mgr.upload_file = Mock()
        conn_mgr.create_remote_file = Mock()
        conn_mgr.remote_file_exists = Mock()
        conn_mgr.download_file = Mock()
        return conn_mgr

    @pytest.fixture
    def scheduler_manager(self, mock_config, mock_connection_manager):
        """Create a SchedulerManager instance for testing."""
        return SchedulerManager(mock_config, mock_connection_manager)

    def test_scheduler_manager_initialization(
        self, scheduler_manager, mock_config, mock_connection_manager
    ):
        """Test SchedulerManager initialization."""
        assert scheduler_manager.config == mock_config
        assert scheduler_manager.connection_manager == mock_connection_manager
        assert scheduler_manager.active_jobs == {}
        assert scheduler_manager.status_manager is not None

    @patch("tempfile.NamedTemporaryFile")
    @patch("pickle.dump")
    @patch("os.unlink")
    @patch("clustrix.executor_schedulers.setup_remote_environment")
    @patch("clustrix.executor_schedulers.create_job_script")
    def test_submit_slurm_job_basic(
        self,
        mock_create_script,
        mock_setup_env,
        mock_unlink,
        mock_pickle_dump,
        mock_tempfile,
        scheduler_manager,
    ):
        """Test basic SLURM job submission without two-venv."""
        # Configure for basic setup
        scheduler_manager.config.use_two_venv = False

        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_file.pkl"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock job script creation
        mock_create_script.return_value = "#!/bin/bash\necho test"

        # Mock remote commands
        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "Submitted batch job 12345",
            "",
        )

        # Test data
        func_data = {
            "function": b"test_func",
            "args": b"test_args",
            "kwargs": b"test_kwargs",
            "requirements": ["numpy", "scipy"],
        }
        job_config = {"cores": 4, "memory": "8GB", "time": "01:00:00"}

        # Execute
        job_id = scheduler_manager.submit_slurm_job(func_data, job_config)

        # Verify results
        assert job_id == "12345"
        assert "12345" in scheduler_manager.active_jobs
        assert scheduler_manager.active_jobs["12345"]["status"] == "submitted"

        # Verify method calls
        mock_pickle_dump.assert_called_once()
        scheduler_manager.connection_manager.upload_file.assert_called_once()
        scheduler_manager.connection_manager.create_remote_file.assert_called_once()
        mock_create_script.assert_called_once_with(
            cluster_type="slurm",
            job_config=job_config,
            remote_job_dir=scheduler_manager.active_jobs["12345"]["remote_dir"],
            config=scheduler_manager.config,
        )

    @patch("threading.Thread")
    @patch("tempfile.NamedTemporaryFile")
    @patch("pickle.dump")
    @patch("os.unlink")
    @patch("clustrix.executor_schedulers.setup_remote_environment")
    @patch("clustrix.executor_schedulers.create_job_script")
    def test_submit_slurm_job_with_two_venv(
        self,
        mock_create_script,
        mock_setup_env,
        mock_unlink,
        mock_pickle_dump,
        mock_tempfile,
        mock_thread,
        scheduler_manager,
    ):
        """Test SLURM job submission with two-venv setup."""
        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_file.pkl"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock enhanced_setup_two_venv_environment import
        mock_enhanced_setup = Mock()
        mock_venv_info = {
            "venv1_python": "/path/to/venv1/python",
            "venv2_python": "/path/to/venv2/python",
        }

        # Mock threading - need to properly handle the venv_info setup in the thread
        mock_thread_instance = Mock()
        mock_thread.return_value = mock_thread_instance
        mock_thread_instance.is_alive.return_value = False

        # Mock job script and submission
        mock_create_script.return_value = "#!/bin/bash\necho test"
        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "Submitted batch job 54321",
            "",
        )

        # Mock the utils module with enhanced_setup_two_venv_environment
        mock_utils = Mock()
        mock_utils.enhanced_setup_two_venv_environment = Mock(
            return_value=mock_venv_info
        )

        # Mock the threading to prevent actual execution and set venv_info on the scheduler_manager
        def mock_setup_thread():
            # Simulate successful venv setup by setting the result directly
            scheduler_manager.config.venv_info = mock_venv_info
            scheduler_manager.config.python_executable = mock_venv_info["venv1_python"]

        mock_thread_instance.start.side_effect = mock_setup_thread

        with patch.dict("sys.modules", {"clustrix.utils": mock_utils}):
            func_data = {
                "function": b"test_func",
                "args": b"test_args",
                "kwargs": b"test_kwargs",
                "requirements": ["numpy"],
            }
            job_config = {"cores": 2, "memory": "4GB"}

            job_id = scheduler_manager.submit_slurm_job(func_data, job_config)

            # Verify two-venv setup was attempted
            mock_thread.assert_called_once()
            mock_thread_instance.start.assert_called_once()
            mock_thread_instance.join.assert_called_once_with(timeout=300)

            assert job_id == "54321"

    @patch("threading.Thread")
    @patch("tempfile.NamedTemporaryFile")
    @patch("pickle.dump")
    @patch("os.unlink")
    @patch("clustrix.executor_schedulers.setup_remote_environment")
    @patch("clustrix.executor_schedulers.create_job_script")
    def test_submit_slurm_job_two_venv_timeout(
        self,
        mock_create_script,
        mock_setup_env,
        mock_unlink,
        mock_pickle_dump,
        mock_tempfile,
        mock_thread,
        scheduler_manager,
    ):
        """Test SLURM job submission when two-venv setup times out."""
        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_file.pkl"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock threading to simulate timeout
        mock_thread_instance = Mock()
        mock_thread.return_value = mock_thread_instance
        mock_thread_instance.is_alive.return_value = True  # Simulate timeout

        # Mock fallback setup and job script
        mock_create_script.return_value = "#!/bin/bash\necho test"
        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "Submitted batch job 99999",
            "",
        )

        mock_utils = Mock()
        mock_utils.enhanced_setup_two_venv_environment = Mock()

        with patch.dict("sys.modules", {"clustrix.utils": mock_utils}):
            func_data = {
                "function": b"test_func",
                "args": b"test_args",
                "kwargs": b"test_kwargs",
                "requirements": [],
            }
            job_config = {"cores": 1}

            job_id = scheduler_manager.submit_slurm_job(func_data, job_config)

            # Verify fallback to basic setup was called
            mock_setup_env.assert_called_once()
            assert job_id == "99999"

    @patch("tempfile.NamedTemporaryFile")
    @patch("pickle.dump")
    @patch("os.unlink")
    @patch("clustrix.executor_schedulers.create_job_script")
    def test_submit_pbs_job(
        self,
        mock_create_script,
        mock_unlink,
        mock_pickle_dump,
        mock_tempfile,
        scheduler_manager,
    ):
        """Test PBS job submission."""
        # Configure for PBS
        scheduler_manager.config.cluster_type = "pbs"

        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_file.pkl"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock job script and submission
        mock_create_script.return_value = "#PBS -l nodes=1\necho test"
        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "67890.headnode",
            "",
        )

        func_data = {
            "function": b"test_func",
            "args": b"test_args",
            "kwargs": b"test_kwargs",
            "requirements": ["pandas"],
        }
        job_config = {"cores": 8, "memory": "16GB", "time": "02:00:00"}

        job_id = scheduler_manager.submit_pbs_job(func_data, job_config)

        # Verify PBS-specific behavior
        assert job_id == "67890.headnode"
        assert "67890.headnode" in scheduler_manager.active_jobs

        # Verify PBS script creation
        mock_create_script.assert_called_once_with(
            cluster_type="pbs",
            job_config=job_config,
            remote_job_dir=scheduler_manager.active_jobs["67890.headnode"][
                "remote_dir"
            ],
            config=scheduler_manager.config,
        )

        # Verify PBS job file naming
        create_file_call = (
            scheduler_manager.connection_manager.create_remote_file.call_args
        )
        assert "job.pbs" in create_file_call[0][0]

    @patch("tempfile.NamedTemporaryFile")
    @patch("pickle.dump")
    @patch("os.unlink")
    @patch("clustrix.executor_schedulers.setup_remote_environment")
    @patch("clustrix.executor_schedulers.create_job_script")
    def test_submit_sge_job(
        self,
        mock_create_script,
        mock_setup_env,
        mock_unlink,
        mock_pickle_dump,
        mock_tempfile,
        scheduler_manager,
    ):
        """Test SGE job submission."""
        # Configure for SGE
        scheduler_manager.config.cluster_type = "sge"

        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_file.pkl"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock job script and submission
        mock_create_script.return_value = "#$ -cwd\necho test"
        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "Your job 135790 has been submitted",
            "",
        )

        func_data = {
            "function": b"test_func",
            "args": b"test_args",
            "kwargs": b"test_kwargs",
            "requirements": ["matplotlib"],
        }
        job_config = {"cores": 16, "memory": "32GB"}

        job_id = scheduler_manager.submit_sge_job(func_data, job_config)

        # Verify SGE-specific behavior
        assert job_id == "135790"  # Extracted from "Your job 135790 has been submitted"
        assert "135790" in scheduler_manager.active_jobs

        # Verify SGE environment setup
        mock_setup_env.assert_called_once()

        # Verify SGE script creation
        mock_create_script.assert_called_once_with(
            cluster_type="sge",
            job_config=job_config,
            remote_job_dir=scheduler_manager.active_jobs["135790"]["remote_dir"],
            config=scheduler_manager.config,
        )

        # Verify SGE job file naming
        create_file_call = (
            scheduler_manager.connection_manager.create_remote_file.call_args
        )
        assert "job.sge" in create_file_call[0][0]

    def test_submit_sge_job_fallback_id_extraction(self, scheduler_manager):
        """Test SGE job ID extraction fallback when format is different."""
        scheduler_manager.config.cluster_type = "sge"

        # Mock submission with different output format
        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "246810",
            "",  # Simple job ID format
        )

        with patch("tempfile.NamedTemporaryFile"), patch("pickle.dump"), patch(
            "os.unlink"
        ), patch("clustrix.executor_schedulers.setup_remote_environment"), patch(
            "clustrix.executor_schedulers.create_job_script"
        ):

            func_data = {
                "function": b"test",
                "args": b"",
                "kwargs": b"",
                "requirements": [],
            }
            job_config = {"cores": 1}

            job_id = scheduler_manager.submit_sge_job(func_data, job_config)
            assert job_id == "246810"

    @patch("threading.Thread")
    @patch("tempfile.NamedTemporaryFile")
    @patch("pickle.dump")
    @patch("os.unlink")
    @patch("clustrix.executor_schedulers.create_job_script")
    def test_submit_ssh_job_with_two_venv(
        self,
        mock_create_script,
        mock_unlink,
        mock_pickle_dump,
        mock_tempfile,
        mock_thread,
        scheduler_manager,
    ):
        """Test SSH job submission with two-venv setup."""
        # Configure for SSH
        scheduler_manager.config.cluster_type = "ssh"

        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_file.pkl"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock enhanced_setup_two_venv_environment
        mock_enhanced_setup = Mock()
        mock_venv_info = {
            "venv1_python": "/remote/venv1/bin/python",
            "venv2_python": "/remote/venv2/bin/python",
        }
        mock_enhanced_setup.return_value = mock_venv_info

        # Mock threading
        mock_thread_instance = Mock()
        mock_thread.return_value = mock_thread_instance
        mock_thread_instance.is_alive.return_value = False

        # Mock job script and execution
        mock_create_script.return_value = "#!/bin/bash\npython script.py"
        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "",
            "",
        )

        mock_utils = Mock()
        mock_utils.enhanced_setup_two_venv_environment = mock_enhanced_setup

        with patch.dict("sys.modules", {"clustrix.utils": mock_utils}):
            func_data = {
                "function": b"test_func",
                "args": b"test_args",
                "kwargs": b"test_kwargs",
                "requirements": ["requests"],
            }
            job_config = {"cores": 4}

            job_id = scheduler_manager.submit_ssh_job(func_data, job_config)

            # Verify SSH-specific behavior
            assert job_id.startswith("ssh_")
            assert job_id in scheduler_manager.active_jobs
            assert scheduler_manager.active_jobs[job_id]["status"] == "running"

            # Verify SSH script creation
            mock_create_script.assert_called_once_with(
                cluster_type="ssh",
                job_config=job_config,
                remote_job_dir=scheduler_manager.active_jobs[job_id]["remote_dir"],
                config=scheduler_manager.config,
            )

            # Verify nohup command execution
            execute_calls = (
                scheduler_manager.connection_manager.execute_remote_command.call_args_list
            )
            nohup_call = next(
                (call for call in execute_calls if "nohup" in str(call)), None
            )
            assert nohup_call is not None

    def test_submit_ssh_job_two_venv_disabled(self, scheduler_manager):
        """Test SSH job submission with two-venv disabled."""
        # Configure SSH with two-venv disabled
        scheduler_manager.config.cluster_type = "ssh"
        scheduler_manager.config.use_two_venv = False

        with patch("tempfile.NamedTemporaryFile"), patch("pickle.dump"), patch(
            "os.unlink"
        ), patch("clustrix.executor_schedulers.create_job_script") as mock_script:

            mock_script.return_value = "#!/bin/bash\necho test"
            scheduler_manager.connection_manager.execute_remote_command.return_value = (
                "",
                "",
            )

            func_data = {
                "function": b"test",
                "args": b"",
                "kwargs": b"",
                "requirements": [],
            }
            job_config = {"cores": 1}

            job_id = scheduler_manager.submit_ssh_job(func_data, job_config)

            assert job_id.startswith("ssh_")
            # Verify config shows no venv_info was set
            assert (
                not hasattr(scheduler_manager.config, "venv_info")
                or scheduler_manager.config.venv_info is None
            )

    def test_check_job_status_delegation(self, scheduler_manager):
        """Test that job status checking is delegated to status manager."""
        mock_status = "running"
        scheduler_manager.status_manager.check_job_status = Mock(
            return_value=mock_status
        )

        result = scheduler_manager.check_job_status("test_job")

        scheduler_manager.status_manager.check_job_status.assert_called_once_with(
            "test_job", scheduler_manager.active_jobs
        )
        assert result == mock_status

    def test_get_error_log_delegation(self, scheduler_manager):
        """Test that error log retrieval is delegated to status manager."""
        mock_error_log = "Test error occurred"
        scheduler_manager.status_manager.get_error_log = Mock(
            return_value=mock_error_log
        )

        result = scheduler_manager.get_error_log("failed_job")

        scheduler_manager.status_manager.get_error_log.assert_called_once_with(
            "failed_job", scheduler_manager.active_jobs
        )
        assert result == mock_error_log

    def test_extract_original_exception_delegation(self, scheduler_manager):
        """Test that exception extraction is delegated to status manager."""
        mock_exception = ValueError("Test error")
        scheduler_manager.status_manager.extract_original_exception = Mock(
            return_value=mock_exception
        )

        result = scheduler_manager.extract_original_exception("failed_job")

        scheduler_manager.status_manager.extract_original_exception.assert_called_once_with(
            "failed_job", scheduler_manager.active_jobs
        )
        assert result == mock_exception

    def test_cancel_job_slurm(self, scheduler_manager):
        """Test SLURM job cancellation."""
        scheduler_manager.config.cluster_type = "slurm"
        scheduler_manager.active_jobs["12345"] = {"remote_dir": "/tmp/job_12345"}

        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "",
            "",
        )

        scheduler_manager.cancel_job("12345")

        # Verify scancel command
        scheduler_manager.connection_manager.execute_remote_command.assert_called_once_with(
            "scancel 12345"
        )
        # Verify job removed from active jobs
        assert "12345" not in scheduler_manager.active_jobs

    def test_cancel_job_pbs(self, scheduler_manager):
        """Test PBS job cancellation."""
        scheduler_manager.config.cluster_type = "pbs"
        scheduler_manager.active_jobs["67890.headnode"] = {
            "remote_dir": "/tmp/job_67890"
        }

        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "",
            "",
        )

        scheduler_manager.cancel_job("67890.headnode")

        # Verify qdel command
        scheduler_manager.connection_manager.execute_remote_command.assert_called_once_with(
            "qdel 67890.headnode"
        )
        # Verify job removed from active jobs
        assert "67890.headnode" not in scheduler_manager.active_jobs

    def test_cancel_job_sge(self, scheduler_manager):
        """Test SGE job cancellation."""
        scheduler_manager.config.cluster_type = "sge"
        scheduler_manager.active_jobs["135790"] = {"remote_dir": "/tmp/job_135790"}

        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "",
            "",
        )

        scheduler_manager.cancel_job("135790")

        # Verify qdel command
        scheduler_manager.connection_manager.execute_remote_command.assert_called_once_with(
            "qdel 135790"
        )
        # Verify job removed from active jobs
        assert "135790" not in scheduler_manager.active_jobs

    def test_cancel_job_unknown_type(self, scheduler_manager):
        """Test job cancellation for unknown cluster type."""
        scheduler_manager.config.cluster_type = "unknown"
        scheduler_manager.active_jobs["test_job"] = {"remote_dir": "/tmp/test"}

        scheduler_manager.cancel_job("test_job")

        # Verify no command executed for unknown type
        scheduler_manager.connection_manager.execute_remote_command.assert_not_called()
        # Verify job still removed from active jobs
        assert "test_job" not in scheduler_manager.active_jobs


class TestKubernetesJobManager:
    """Test the KubernetesJobManager class."""

    @pytest.fixture
    def mock_k8s_config(self):
        """Create a mock Kubernetes configuration."""
        config = Mock(spec=ClusterConfig)
        config.cluster_type = "kubernetes"
        config.k8s_image = "python:3.11-slim"
        config.k8s_namespace = "default"
        config.k8s_backoff_limit = 3
        config.k8s_job_ttl_seconds = 3600
        config.cleanup_on_success = True
        config.job_poll_interval = 5
        return config

    @pytest.fixture
    def mock_connection_manager_k8s(self):
        """Create a mock connection manager for Kubernetes."""
        conn_mgr = Mock()
        conn_mgr.k8s_client = Mock()
        conn_mgr.setup_kubernetes = Mock()
        return conn_mgr

    @pytest.fixture
    def k8s_manager(self, mock_k8s_config, mock_connection_manager_k8s):
        """Create a KubernetesJobManager instance for testing."""
        return KubernetesJobManager(mock_k8s_config, mock_connection_manager_k8s)

    def test_k8s_manager_initialization(
        self, k8s_manager, mock_k8s_config, mock_connection_manager_k8s
    ):
        """Test KubernetesJobManager initialization."""
        assert k8s_manager.config == mock_k8s_config
        assert k8s_manager.connection_manager == mock_connection_manager_k8s
        assert k8s_manager.active_jobs == {}

    @patch("kubernetes.client")
    @patch("cloudpickle.dumps")
    @patch("base64.b64encode")
    @patch("random.randint")
    @patch("time.time")
    def test_submit_k8s_job(
        self,
        mock_time,
        mock_randint,
        mock_b64encode,
        mock_cloudpickle,
        mock_k8s_client,
        k8s_manager,
    ):
        """Test Kubernetes job submission."""
        # Setup mocks
        mock_time.return_value = 1609459200  # Fixed timestamp
        mock_randint.return_value = 1234
        mock_cloudpickle.return_value = b"serialized_function_data"
        mock_b64encode.return_value = Mock()
        mock_b64encode.return_value.decode.return_value = "base64_encoded_data"

        # Mock Kubernetes API
        mock_batch_api = Mock()
        mock_response = Mock()
        mock_response.metadata.name = "clustrix-job-1609459200-1234"
        mock_batch_api.create_namespaced_job.return_value = mock_response
        mock_k8s_client.BatchV1Api.return_value = mock_batch_api

        # Ensure connection manager has k8s_client
        k8s_manager.connection_manager.k8s_client = Mock()

        func_data = {
            "function": b"test_function",
            "args": b"test_args",
            "kwargs": b"test_kwargs",
            "requirements": ["numpy", "pandas"],
        }
        job_config = {"cores": 2, "memory": "4Gi"}

        job_id = k8s_manager.submit_k8s_job(func_data, job_config)

        # Verify job submission
        assert job_id == "clustrix-job-1609459200-1234"
        assert job_id in k8s_manager.active_jobs
        assert k8s_manager.active_jobs[job_id]["status"] == "submitted"
        assert k8s_manager.active_jobs[job_id]["k8s_job"] == True

        # Verify Kubernetes API calls
        mock_batch_api.create_namespaced_job.assert_called_once()
        call_args = mock_batch_api.create_namespaced_job.call_args

        # Verify namespace and manifest structure
        assert call_args[1]["namespace"] == "default"
        manifest = call_args[1]["body"]
        assert manifest["kind"] == "Job"
        assert manifest["metadata"]["name"] == "clustrix-job-1609459200-1234"

        # Verify container configuration
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        assert container["name"] == "clustrix-worker"
        assert container["image"] == "python:3.11-slim"
        assert container["resources"]["requests"]["cpu"] == "2"
        assert container["resources"]["requests"]["memory"] == "4Gi"
        assert container["resources"]["limits"]["cpu"] == "2"
        assert container["resources"]["limits"]["memory"] == "4Gi"

    @patch("kubernetes.client")
    def test_submit_k8s_job_without_k8s_client(self, mock_k8s_client, k8s_manager):
        """Test K8s job submission when client needs setup."""
        # Simulate no existing k8s_client
        k8s_manager.connection_manager.k8s_client = None

        mock_batch_api = Mock()
        mock_response = Mock()
        mock_response.metadata.name = "test-job"
        mock_batch_api.create_namespaced_job.return_value = mock_response
        mock_k8s_client.BatchV1Api.return_value = mock_batch_api

        with patch("cloudpickle.dumps"), patch("base64.b64encode") as mock_b64:
            mock_b64.return_value.decode.return_value = "encoded_data"

            func_data = {
                "function": b"test",
                "args": b"",
                "kwargs": b"",
                "requirements": [],
            }
            job_config = {"cores": 1}

            job_id = k8s_manager.submit_k8s_job(func_data, job_config)

            # Verify Kubernetes setup was called
            k8s_manager.connection_manager.setup_kubernetes.assert_called_once()
            assert job_id == "test-job"

    def test_submit_k8s_job_import_error(self, k8s_manager):
        """Test K8s job submission when kubernetes package is not available."""
        with patch.dict("sys.modules", {"kubernetes": None}):
            func_data = {
                "function": b"test",
                "args": b"",
                "kwargs": b"",
                "requirements": [],
            }
            job_config = {"cores": 1}

            with pytest.raises(ImportError, match="kubernetes package required"):
                k8s_manager.submit_k8s_job(func_data, job_config)

    @patch("kubernetes.client")
    def test_check_k8s_job_status_completed(self, mock_k8s_client, k8s_manager):
        """Test checking Kubernetes job status - completed."""
        mock_batch_api = Mock()
        mock_job = Mock()
        mock_job.status.succeeded = 1
        mock_job.status.failed = None
        mock_job.status.active = None
        mock_batch_api.read_namespaced_job.return_value = mock_job
        mock_k8s_client.BatchV1Api.return_value = mock_batch_api

        status = k8s_manager.check_k8s_job_status("test-job")

        assert status == "completed"
        mock_batch_api.read_namespaced_job.assert_called_once_with(
            name="test-job", namespace="default"
        )

    @patch("kubernetes.client")
    def test_check_k8s_job_status_failed(self, mock_k8s_client, k8s_manager):
        """Test checking Kubernetes job status - failed."""
        mock_batch_api = Mock()
        mock_job = Mock()
        mock_job.status.succeeded = None
        mock_job.status.failed = 1
        mock_job.status.active = None
        mock_batch_api.read_namespaced_job.return_value = mock_job
        mock_k8s_client.BatchV1Api.return_value = mock_batch_api

        status = k8s_manager.check_k8s_job_status("test-job")

        assert status == "failed"

    @patch("kubernetes.client")
    def test_check_k8s_job_status_running(self, mock_k8s_client, k8s_manager):
        """Test checking Kubernetes job status - running."""
        mock_batch_api = Mock()
        mock_job = Mock()
        mock_job.status.succeeded = None
        mock_job.status.failed = None
        mock_job.status.active = 1
        mock_batch_api.read_namespaced_job.return_value = mock_job
        mock_k8s_client.BatchV1Api.return_value = mock_batch_api

        status = k8s_manager.check_k8s_job_status("test-job")

        assert status == "running"

    @patch("kubernetes.client")
    def test_check_k8s_job_status_pending(self, mock_k8s_client, k8s_manager):
        """Test checking Kubernetes job status - pending."""
        mock_batch_api = Mock()
        mock_job = Mock()
        mock_job.status.succeeded = None
        mock_job.status.failed = None
        mock_job.status.active = None
        mock_batch_api.read_namespaced_job.return_value = mock_job
        mock_k8s_client.BatchV1Api.return_value = mock_batch_api

        status = k8s_manager.check_k8s_job_status("test-job")

        assert status == "pending"

    @patch("kubernetes.client")
    def test_check_k8s_job_status_exception(self, mock_k8s_client, k8s_manager):
        """Test checking Kubernetes job status with exception."""
        mock_batch_api = Mock()
        mock_batch_api.read_namespaced_job.side_effect = Exception("API Error")
        mock_k8s_client.BatchV1Api.return_value = mock_batch_api

        # Job is tracked but API call fails
        k8s_manager.active_jobs["test-job"] = {"status": "submitted"}
        status = k8s_manager.check_k8s_job_status("test-job")
        assert status == "completed"

        # Job not tracked and API call fails
        del k8s_manager.active_jobs["test-job"]
        status = k8s_manager.check_k8s_job_status("unknown-job")
        assert status == "unknown"

    @patch("kubernetes.client")
    @patch("ast.literal_eval")
    def test_get_k8s_result(self, mock_literal_eval, mock_k8s_client, k8s_manager):
        """Test getting result from Kubernetes job."""
        # Mock pod and logs
        mock_core_api = Mock()
        mock_pod = Mock()
        mock_pod.status.phase = "Succeeded"
        mock_pod.metadata.name = "test-pod"
        mock_pod.metadata.namespace = "default"

        mock_pod_list = Mock()
        mock_pod_list.items = [mock_pod]
        mock_core_api.list_namespaced_pod.return_value = mock_pod_list
        mock_core_api.read_namespaced_pod_log.return_value = (
            "Some output\nCLUSTRIX_RESULT:42\nMore output"
        )

        mock_k8s_client.CoreV1Api.return_value = mock_core_api
        mock_literal_eval.return_value = 42

        result = k8s_manager.get_k8s_result("test-job")

        assert result == 42
        mock_literal_eval.assert_called_once_with("42")
        mock_core_api.list_namespaced_pod.assert_called_once_with(
            namespace="default", label_selector="job-name=test-job"
        )

    @patch("kubernetes.client")
    def test_get_k8s_result_no_pods(self, mock_k8s_client, k8s_manager):
        """Test getting result when no successful pods found."""
        mock_core_api = Mock()
        mock_pod_list = Mock()
        mock_pod_list.items = []
        mock_core_api.list_namespaced_pod.return_value = mock_pod_list
        mock_k8s_client.CoreV1Api.return_value = mock_core_api

        with pytest.raises(RuntimeError, match="No successful pod found"):
            k8s_manager.get_k8s_result("test-job")

    @patch("kubernetes.client")
    def test_get_k8s_error_log(self, mock_k8s_client, k8s_manager):
        """Test getting error log from Kubernetes job."""
        mock_core_api = Mock()
        mock_pod1 = Mock()
        mock_pod1.metadata.name = "pod1"
        mock_pod1.metadata.namespace = "default"
        mock_pod2 = Mock()
        mock_pod2.metadata.name = "pod2"
        mock_pod2.metadata.namespace = "default"

        mock_pod_list = Mock()
        mock_pod_list.items = [mock_pod1, mock_pod2]
        mock_core_api.list_namespaced_pod.return_value = mock_pod_list

        def mock_read_log(name, namespace):
            if name == "pod1":
                return "Error in pod1"
            elif name == "pod2":
                return "Error in pod2"

        mock_core_api.read_namespaced_pod_log.side_effect = mock_read_log
        mock_k8s_client.CoreV1Api.return_value = mock_core_api

        error_log = k8s_manager.get_k8s_error_log("test-job")

        assert "Pod pod1:" in error_log
        assert "Error in pod1" in error_log
        assert "Pod pod2:" in error_log
        assert "Error in pod2" in error_log

    @patch("kubernetes.client")
    def test_extract_k8s_exception(self, mock_k8s_client, k8s_manager):
        """Test extracting original exception from Kubernetes job."""
        # Mock get_k8s_error_log method
        k8s_manager.get_k8s_error_log = Mock(
            return_value="CLUSTRIX_ERROR:Test error message\nCLUSTRIX_TRACEBACK:...\n"
        )

        exception = k8s_manager.extract_k8s_exception("test-job")

        assert isinstance(exception, RuntimeError)
        assert str(exception) == "Test error message"

    def test_extract_k8s_exception_no_error(self, k8s_manager):
        """Test extracting exception when no error info found."""
        k8s_manager.get_k8s_error_log = Mock(
            return_value="Regular output without errors"
        )

        exception = k8s_manager.extract_k8s_exception("test-job")

        assert exception is None

    @patch("kubernetes.client")
    def test_cleanup_k8s_job(self, mock_k8s_client, k8s_manager):
        """Test cleaning up Kubernetes job."""
        mock_batch_api = Mock()
        mock_k8s_client.BatchV1Api.return_value = mock_batch_api

        # Mock the V1DeleteOptions class
        mock_delete_options = Mock()
        mock_k8s_client.V1DeleteOptions.return_value = mock_delete_options

        k8s_manager.cleanup_k8s_job("test-job")

        mock_batch_api.delete_namespaced_job.assert_called_once_with(
            name="test-job", namespace="default", body=mock_delete_options
        )
        mock_k8s_client.V1DeleteOptions.assert_called_once_with(
            propagation_policy="Foreground"
        )

    @patch("kubernetes.client")
    def test_cleanup_k8s_job_failure(self, mock_k8s_client, k8s_manager):
        """Test cleanup handling when deletion fails."""
        mock_batch_api = Mock()
        mock_batch_api.delete_namespaced_job.side_effect = Exception("Deletion failed")
        mock_k8s_client.BatchV1Api.return_value = mock_batch_api

        # Should not raise exception, just log warning
        k8s_manager.cleanup_k8s_job("test-job")

    @patch("time.sleep")
    def test_wait_for_k8s_result_success(self, mock_sleep, k8s_manager):
        """Test waiting for Kubernetes job completion - success."""
        k8s_manager.active_jobs["test-job"] = {"status": "submitted"}

        # Mock status progression: running -> completed
        k8s_manager.check_k8s_job_status = Mock(side_effect=["running", "completed"])
        k8s_manager.get_k8s_result = Mock(return_value="success_result")
        k8s_manager.cleanup_k8s_job = Mock()

        result = k8s_manager.wait_for_k8s_result("test-job")

        assert result == "success_result"
        assert "test-job" not in k8s_manager.active_jobs
        k8s_manager.cleanup_k8s_job.assert_called_once_with("test-job")
        mock_sleep.assert_called_once_with(5)  # job_poll_interval

    @patch("time.sleep")
    def test_wait_for_k8s_result_failure(self, mock_sleep, k8s_manager):
        """Test waiting for Kubernetes job completion - failure."""
        k8s_manager.active_jobs["test-job"] = {"status": "submitted"}

        k8s_manager.check_k8s_job_status = Mock(return_value="failed")
        k8s_manager.get_k8s_error_log = Mock(return_value="Job failed with error")
        k8s_manager.extract_k8s_exception = Mock(
            return_value=ValueError("Original error")
        )

        with pytest.raises(ValueError, match="Original error"):
            k8s_manager.wait_for_k8s_result("test-job")

    def test_wait_for_k8s_result_unknown_job(self, k8s_manager):
        """Test waiting for result of unknown job."""
        with pytest.raises(ValueError, match="Unknown job ID"):
            k8s_manager.wait_for_k8s_result("unknown-job")


class TestSchedulerCommandMocking:
    """Test that scheduler commands are properly mocked and not executed."""

    @pytest.fixture
    def scheduler_manager(self):
        """Create scheduler manager with mocked connection."""
        config = Mock()
        config.cluster_type = "slurm"
        config.remote_work_dir = "/tmp"
        config.use_two_venv = False

        conn_mgr = Mock()
        return SchedulerManager(config, conn_mgr)

    def test_slurm_commands_mocked(self, scheduler_manager):
        """Test that SLURM commands (sbatch, squeue, scancel) are mocked."""
        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "Submitted batch job 12345",
            "",
        )

        with patch("tempfile.NamedTemporaryFile"), patch("pickle.dump"), patch(
            "os.unlink"
        ), patch("clustrix.executor_schedulers.setup_remote_environment"), patch(
            "clustrix.executor_schedulers.create_job_script"
        ):

            func_data = {
                "function": b"test",
                "args": b"",
                "kwargs": b"",
                "requirements": [],
            }
            job_config = {"cores": 1}

            job_id = scheduler_manager.submit_slurm_job(func_data, job_config)

            # Verify sbatch command was "executed" via mock
            execute_calls = (
                scheduler_manager.connection_manager.execute_remote_command.call_args_list
            )
            sbatch_calls = [call for call in execute_calls if "sbatch" in str(call)]
            assert len(sbatch_calls) > 0
            assert job_id == "12345"

    def test_pbs_commands_mocked(self, scheduler_manager):
        """Test that PBS commands (qsub, qstat, qdel) are mocked."""
        scheduler_manager.config.cluster_type = "pbs"
        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "67890.headnode",
            "",
        )

        with patch("tempfile.NamedTemporaryFile"), patch("pickle.dump"), patch(
            "os.unlink"
        ), patch("clustrix.executor_schedulers.create_job_script"):

            func_data = {
                "function": b"test",
                "args": b"",
                "kwargs": b"",
                "requirements": [],
            }
            job_config = {"cores": 1}

            job_id = scheduler_manager.submit_pbs_job(func_data, job_config)

            # Verify qsub command was "executed" via mock
            execute_calls = (
                scheduler_manager.connection_manager.execute_remote_command.call_args_list
            )
            qsub_calls = [call for call in execute_calls if "qsub" in str(call)]
            assert len(qsub_calls) > 0
            assert job_id == "67890.headnode"

    def test_sge_commands_mocked(self, scheduler_manager):
        """Test that SGE commands (qsub, qstat, qdel) are mocked."""
        scheduler_manager.config.cluster_type = "sge"
        scheduler_manager.connection_manager.execute_remote_command.return_value = (
            "Your job 135790 has been submitted",
            "",
        )

        with patch("tempfile.NamedTemporaryFile"), patch("pickle.dump"), patch(
            "os.unlink"
        ), patch("clustrix.executor_schedulers.setup_remote_environment"), patch(
            "clustrix.executor_schedulers.create_job_script"
        ):

            func_data = {
                "function": b"test",
                "args": b"",
                "kwargs": b"",
                "requirements": [],
            }
            job_config = {"cores": 1}

            job_id = scheduler_manager.submit_sge_job(func_data, job_config)

            # Verify qsub command was "executed" via mock
            execute_calls = (
                scheduler_manager.connection_manager.execute_remote_command.call_args_list
            )
            qsub_calls = [call for call in execute_calls if "qsub" in str(call)]
            assert len(qsub_calls) > 0
            assert job_id == "135790"

    @patch("kubernetes.client")
    def test_kubernetes_commands_mocked(self, mock_k8s_client):
        """Test that Kubernetes API calls are mocked."""
        config = Mock()
        config.cluster_type = "kubernetes"
        config.k8s_image = "python:3.11-slim"
        config.k8s_namespace = "default"
        config.k8s_backoff_limit = 3
        config.k8s_job_ttl_seconds = 3600

        conn_mgr = Mock()
        conn_mgr.k8s_client = Mock()

        k8s_manager = KubernetesJobManager(config, conn_mgr)

        # Mock Kubernetes API
        mock_batch_api = Mock()
        mock_response = Mock()
        mock_response.metadata.name = "test-k8s-job"
        mock_batch_api.create_namespaced_job.return_value = mock_response
        mock_k8s_client.BatchV1Api.return_value = mock_batch_api

        with patch("cloudpickle.dumps"), patch("base64.b64encode") as mock_b64:
            mock_b64.return_value.decode.return_value = "encoded_data"

            func_data = {
                "function": b"test",
                "args": b"",
                "kwargs": b"",
                "requirements": [],
            }
            job_config = {"cores": 1}

            job_id = k8s_manager.submit_k8s_job(func_data, job_config)

            # Verify kubectl equivalent API calls were "executed" via mock
            mock_batch_api.create_namespaced_job.assert_called_once()
            assert job_id == "test-k8s-job"


class TestErrorHandling:
    """Test error handling scenarios for scheduler operations."""

    @pytest.fixture
    def scheduler_manager(self):
        """Create scheduler manager for error testing."""
        config = Mock()
        config.cluster_type = "slurm"
        config.remote_work_dir = "/tmp"
        config.use_two_venv = True
        config.venv_setup_timeout = 300

        conn_mgr = Mock()
        return SchedulerManager(config, conn_mgr)

    def test_job_submission_connection_error(self, scheduler_manager):
        """Test job submission when connection fails."""
        scheduler_manager.connection_manager.execute_remote_command.side_effect = (
            Exception("Connection lost")
        )

        with patch("tempfile.NamedTemporaryFile"), patch("pickle.dump"), patch(
            "os.unlink"
        ), patch("clustrix.executor_schedulers.setup_remote_environment"), patch(
            "clustrix.executor_schedulers.create_job_script"
        ):

            func_data = {
                "function": b"test",
                "args": b"",
                "kwargs": b"",
                "requirements": [],
            }
            job_config = {"cores": 1}

            with pytest.raises(Exception, match="Connection lost"):
                scheduler_manager.submit_slurm_job(func_data, job_config)

    def test_two_venv_setup_exception(self, scheduler_manager):
        """Test handling of two-venv setup exceptions."""
        with patch("threading.Thread") as mock_thread:
            mock_thread_instance = Mock()
            mock_thread.return_value = mock_thread_instance
            mock_thread_instance.is_alive.return_value = False

            # Mock exception during venv setup
            def setup_venv_with_exception():
                raise ValueError("Venv setup failed")

            mock_utils = Mock()
            mock_utils.enhanced_setup_two_venv_environment = Mock(
                side_effect=setup_venv_with_exception
            )

            with patch.dict("sys.modules", {"clustrix.utils": mock_utils}):
                with patch(
                    "clustrix.executor_schedulers.setup_remote_environment"
                ) as mock_fallback:
                    with patch("tempfile.NamedTemporaryFile"), patch(
                        "pickle.dump"
                    ), patch("os.unlink"), patch(
                        "clustrix.executor_schedulers.create_job_script"
                    ):

                        scheduler_manager.connection_manager.execute_remote_command.return_value = (
                            "Submitted batch job 99999",
                            "",
                        )

                        func_data = {
                            "function": b"test",
                            "args": b"",
                            "kwargs": b"",
                            "requirements": [],
                        }
                        job_config = {"cores": 1}

                        job_id = scheduler_manager.submit_slurm_job(
                            func_data, job_config
                        )

                        # Verify fallback was used
                        mock_fallback.assert_called_once()
                        assert job_id == "99999"

    @patch("kubernetes.client")
    def test_k8s_api_exception_handling(self, mock_k8s_client):
        """Test Kubernetes API exception handling."""
        config = Mock()
        config.k8s_namespace = "default"
        conn_mgr = Mock()
        conn_mgr.k8s_client = Mock()

        k8s_manager = KubernetesJobManager(config, conn_mgr)

        # Mock API exception
        mock_batch_api = Mock()
        mock_batch_api.create_namespaced_job.side_effect = Exception("API server error")
        mock_k8s_client.BatchV1Api.return_value = mock_batch_api

        with patch("cloudpickle.dumps"), patch("base64.b64encode") as mock_b64:
            mock_b64.return_value.decode.return_value = "encoded_data"

            func_data = {
                "function": b"test",
                "args": b"",
                "kwargs": b"",
                "requirements": [],
            }
            job_config = {"cores": 1}

            with pytest.raises(Exception, match="API server error"):
                k8s_manager.submit_k8s_job(func_data, job_config)
