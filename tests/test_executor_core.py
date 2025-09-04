"""Comprehensive tests for executor_core module.

Tests focused on job lifecycle coordination, status tracking, result processing,
and orchestration across different cluster managers.
"""

import pytest
import pickle
import tempfile
import time
from unittest.mock import Mock, patch, MagicMock, mock_open
from clustrix.executor_core import ClusterExecutor
from clustrix.config import ClusterConfig


class TestClusterExecutorInitialization:
    """Test ClusterExecutor initialization and setup."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock configuration for testing."""
        return ClusterConfig(
            cluster_host="test.cluster.com",
            cluster_type="slurm",
            username="testuser",
            remote_work_dir="/tmp/clustrix",
            job_poll_interval=1.0,
            cleanup_on_success=True,
        )

    @patch("clustrix.executor_core.ConnectionManager")
    @patch("clustrix.executor_core.SchedulerManager")
    @patch("clustrix.executor_core.KubernetesJobManager")
    @patch("clustrix.executor_core.CloudJobManager")
    def test_initialization_creates_all_managers(
        self, mock_cloud, mock_k8s, mock_scheduler, mock_connection, mock_config
    ):
        """Test that ClusterExecutor initializes all sub-managers correctly."""
        executor = ClusterExecutor(mock_config)

        # Verify all managers were created
        mock_connection.assert_called_once_with(mock_config)
        mock_scheduler.assert_called_once_with(mock_config, executor.connection_manager)
        mock_k8s.assert_called_once_with(mock_config, executor.connection_manager)
        mock_cloud.assert_called_once_with(mock_config)

        # Verify configuration and active jobs tracking
        assert executor.config == mock_config
        assert executor.active_jobs == {}

    @patch("clustrix.executor_core.ConnectionManager")
    @patch("clustrix.executor_core.SchedulerManager")
    @patch("clustrix.executor_core.KubernetesJobManager")
    @patch("clustrix.executor_core.CloudJobManager")
    def test_initialization_with_different_configs(
        self, mock_cloud, mock_k8s, mock_scheduler, mock_connection
    ):
        """Test initialization with different cluster configurations."""
        configs = [
            ClusterConfig(cluster_type="kubernetes"),
            ClusterConfig(cluster_type="pbs"),
            ClusterConfig(cluster_type="ssh"),
        ]

        for config in configs:
            executor = ClusterExecutor(config)
            assert executor.config == config
            assert executor.active_jobs == {}


class TestJobSubmissionCoordination:
    """Test job submission routing and coordination across different cluster types."""

    @pytest.fixture
    def mock_executor(self):
        """Create executor with all managers mocked."""
        config = ClusterConfig(cluster_type="slurm", cluster_host="test.com")

        with patch("clustrix.executor_core.ConnectionManager") as mock_conn, patch(
            "clustrix.executor_core.SchedulerManager"
        ) as mock_sched, patch(
            "clustrix.executor_core.KubernetesJobManager"
        ) as mock_k8s, patch(
            "clustrix.executor_core.CloudJobManager"
        ) as mock_cloud:

            executor = ClusterExecutor(config)
            executor.connection_manager.connect = Mock()
            return executor, mock_conn, mock_sched, mock_k8s, mock_cloud

    def test_submit_slurm_job_coordination(self, mock_executor):
        """Test SLURM job submission coordination."""
        executor, _, mock_sched, _, _ = mock_executor
        executor.config.cluster_type = "slurm"

        # Mock scheduler manager submission
        mock_sched.return_value.submit_slurm_job.return_value = "slurm_12345"

        func_data = {"function": b"test", "args": b"test", "kwargs": b"test"}
        job_config = {"cores": 4, "memory": "8GB"}

        job_id = executor.submit_job(func_data, job_config)

        # Verify connection was established
        executor.connection_manager.connect.assert_called_once()

        # Verify scheduler manager was called
        executor.scheduler_manager.submit_slurm_job.assert_called_once_with(
            func_data, job_config
        )

        # Verify job tracking
        assert job_id == "slurm_12345"
        assert job_id in executor.active_jobs
        assert executor.active_jobs[job_id]["manager"] == "scheduler"

    def test_submit_pbs_job_coordination(self, mock_executor):
        """Test PBS job submission coordination."""
        executor, _, mock_sched, _, _ = mock_executor
        executor.config.cluster_type = "pbs"

        mock_sched.return_value.submit_pbs_job.return_value = "pbs_67890"

        func_data = {"function": b"test", "args": b"test", "kwargs": b"test"}
        job_config = {"cores": 2, "memory": "4GB"}

        job_id = executor.submit_job(func_data, job_config)

        executor.connection_manager.connect.assert_called_once()
        executor.scheduler_manager.submit_pbs_job.assert_called_once_with(
            func_data, job_config
        )

        assert job_id == "pbs_67890"
        assert job_id in executor.active_jobs
        assert executor.active_jobs[job_id]["manager"] == "scheduler"

    def test_submit_sge_job_coordination(self, mock_executor):
        """Test SGE job submission coordination."""
        executor, _, mock_sched, _, _ = mock_executor
        executor.config.cluster_type = "sge"

        mock_sched.return_value.submit_sge_job.return_value = "sge_54321"

        func_data = {"function": b"test", "args": b"test", "kwargs": b"test"}
        job_config = {"cores": 8, "memory": "16GB"}

        job_id = executor.submit_job(func_data, job_config)

        executor.connection_manager.connect.assert_called_once()
        executor.scheduler_manager.submit_sge_job.assert_called_once_with(
            func_data, job_config
        )

        assert job_id == "sge_54321"
        assert job_id in executor.active_jobs
        assert executor.active_jobs[job_id]["manager"] == "scheduler"

    def test_submit_kubernetes_job_coordination(self, mock_executor):
        """Test Kubernetes job submission coordination."""
        executor, _, _, mock_k8s, _ = mock_executor
        executor.config.cluster_type = "kubernetes"

        mock_k8s.return_value.submit_k8s_job.return_value = "clustrix-job-12345"

        func_data = {"function": b"test", "args": b"test", "kwargs": b"test"}
        job_config = {"cores": 4, "memory": "8Gi"}

        job_id = executor.submit_job(func_data, job_config)

        executor.connection_manager.connect.assert_called_once()
        executor.k8s_manager.submit_k8s_job.assert_called_once_with(
            func_data, job_config
        )

        assert job_id == "clustrix-job-12345"
        assert job_id in executor.active_jobs
        assert executor.active_jobs[job_id]["manager"] == "kubernetes"

    def test_submit_ssh_job_coordination(self, mock_executor):
        """Test SSH job submission coordination."""
        executor, _, mock_sched, _, _ = mock_executor
        executor.config.cluster_type = "ssh"

        mock_sched.return_value.submit_ssh_job.return_value = "ssh_98765"

        func_data = {"function": b"test", "args": b"test", "kwargs": b"test"}
        job_config = {"cores": 1, "memory": "2GB"}

        job_id = executor.submit_job(func_data, job_config)

        executor.connection_manager.connect.assert_called_once()
        executor.scheduler_manager.submit_ssh_job.assert_called_once_with(
            func_data, job_config
        )

        assert job_id == "ssh_98765"
        assert job_id in executor.active_jobs
        assert executor.active_jobs[job_id]["manager"] == "scheduler"

    def test_submit_cloud_job_coordination(self, mock_executor):
        """Test cloud provider job submission coordination."""
        executor, _, _, _, mock_cloud = mock_executor

        mock_cloud.return_value.submit_cloud_job.return_value = "lambda_abc123"

        func_data = {"function": b"test", "args": b"test", "kwargs": b"test"}
        job_config = {"provider": "lambda", "memory": "512MB"}

        job_id = executor.submit_job(func_data, job_config)

        # Verify no connection was established for cloud jobs
        executor.connection_manager.connect.assert_not_called()

        # Verify cloud manager was called
        executor.cloud_manager.submit_cloud_job.assert_called_once_with(
            func_data, job_config, "lambda"
        )

        assert job_id == "lambda_abc123"
        assert job_id in executor.active_jobs
        assert executor.active_jobs[job_id]["manager"] == "cloud"

    def test_submit_unsupported_cluster_type(self, mock_executor):
        """Test submission with unsupported cluster type."""
        executor, _, _, _, _ = mock_executor
        executor.config.cluster_type = "unsupported"

        func_data = {"function": b"test", "args": b"test", "kwargs": b"test"}
        job_config = {"cores": 4}

        with pytest.raises(ValueError, match="Unsupported cluster type"):
            executor.submit_job(func_data, job_config)

    def test_submit_unsupported_cloud_provider(self, mock_executor):
        """Test submission with unsupported cloud provider."""
        executor, _, _, _, _ = mock_executor

        func_data = {"function": b"test", "args": b"test", "kwargs": b"test"}
        job_config = {"provider": "unsupported_provider"}

        with pytest.raises(ValueError, match="Unsupported cloud provider"):
            executor.submit_job(func_data, job_config)

    def test_submit_auto_provisioned_kubernetes_routing(self, mock_executor):
        """Test that auto-provisioned K8s doesn't route to cloud provider."""
        executor, _, _, mock_k8s, _ = mock_executor
        executor.config.cluster_type = "kubernetes"
        executor.config.auto_provision_k8s = True

        mock_k8s.return_value.submit_k8s_job.return_value = "clustrix-job-auto-123"

        func_data = {"function": b"test", "args": b"test", "kwargs": b"test"}
        job_config = {"provider": "aws"}  # Provider specified but should use K8s

        job_id = executor.submit_job(func_data, job_config)

        # Should route to K8s, not cloud
        executor.k8s_manager.submit_k8s_job.assert_called_once()
        executor.cloud_manager.submit_cloud_job.assert_not_called()

        assert job_id == "clustrix-job-auto-123"
        assert executor.active_jobs[job_id]["manager"] == "kubernetes"


class TestJobStatusMonitoring:
    """Test job status monitoring and tracking coordination."""

    @pytest.fixture
    def mock_executor_with_jobs(self):
        """Create executor with tracked jobs."""
        config = ClusterConfig(cluster_type="slurm")

        with patch("clustrix.executor_core.ConnectionManager"), patch(
            "clustrix.executor_core.SchedulerManager"
        ), patch("clustrix.executor_core.KubernetesJobManager"), patch(
            "clustrix.executor_core.CloudJobManager"
        ):

            executor = ClusterExecutor(config)

            # Add some tracked jobs
            executor.active_jobs = {
                "scheduler_123": {"manager": "scheduler", "job_id": "scheduler_123"},
                "clustrix-job-456": {
                    "manager": "kubernetes",
                    "job_id": "clustrix-job-456",
                },
                "lambda_789": {"manager": "cloud", "job_id": "lambda_789"},
            }

            return executor

    def test_get_job_status_scheduler_tracked(self, mock_executor_with_jobs):
        """Test status checking for tracked scheduler job."""
        executor = mock_executor_with_jobs
        executor.scheduler_manager.check_job_status.return_value = "running"

        status = executor.get_job_status("scheduler_123")

        executor.scheduler_manager.check_job_status.assert_called_once_with(
            "scheduler_123"
        )
        assert status == "running"

    def test_get_job_status_kubernetes_tracked(self, mock_executor_with_jobs):
        """Test status checking for tracked Kubernetes job."""
        executor = mock_executor_with_jobs
        executor.k8s_manager.check_k8s_job_status.return_value = "completed"

        status = executor.get_job_status("clustrix-job-456")

        executor.k8s_manager.check_k8s_job_status.assert_called_once_with(
            "clustrix-job-456"
        )
        assert status == "completed"

    def test_get_job_status_cloud_tracked(self, mock_executor_with_jobs):
        """Test status checking for tracked cloud job."""
        executor = mock_executor_with_jobs
        executor.cloud_manager.get_cloud_job_status.return_value = "failed"

        status = executor.get_job_status("lambda_789")

        executor.cloud_manager.get_cloud_job_status.assert_called_once_with(
            "lambda_789"
        )
        assert status == "failed"

    def test_get_job_status_fallback_scheduling(self, mock_executor_with_jobs):
        """Test status checking fallback for untracked jobs via job ID pattern."""
        executor = mock_executor_with_jobs
        executor.scheduler_manager.check_job_status.return_value = "queued"

        # Untracked job - should fallback based on ID pattern
        status = executor.get_job_status("unknown_job_123")

        executor.scheduler_manager.check_job_status.assert_called_once_with(
            "unknown_job_123"
        )
        assert status == "queued"

    def test_get_job_status_fallback_kubernetes(self, mock_executor_with_jobs):
        """Test status checking fallback for Kubernetes job pattern."""
        executor = mock_executor_with_jobs
        executor.k8s_manager.check_k8s_job_status.return_value = "running"

        status = executor.get_job_status("clustrix-job-unknown")

        executor.k8s_manager.check_k8s_job_status.assert_called_once_with(
            "clustrix-job-unknown"
        )
        assert status == "running"

    def test_get_job_status_fallback_cloud_lambda(self, mock_executor_with_jobs):
        """Test status checking fallback for cloud lambda job pattern."""
        executor = mock_executor_with_jobs
        executor.cloud_manager.get_cloud_job_status.return_value = "completed"

        status = executor.get_job_status("lambda_unknown")

        executor.cloud_manager.get_cloud_job_status.assert_called_once_with(
            "lambda_unknown"
        )
        assert status == "completed"

    def test_get_job_status_fallback_cloud_aws(self, mock_executor_with_jobs):
        """Test status checking fallback for cloud AWS job pattern."""
        executor = mock_executor_with_jobs
        executor.cloud_manager.get_cloud_job_status.return_value = "running"

        status = executor.get_job_status("aws_unknown")

        executor.cloud_manager.get_cloud_job_status.assert_called_once_with(
            "aws_unknown"
        )
        assert status == "running"


class TestResultCollection:
    """Test result collection and aggregation workflows."""

    @pytest.fixture
    def mock_executor_with_active_jobs(self):
        """Create executor with active jobs for result testing."""
        config = ClusterConfig(
            cluster_type="slurm", cleanup_on_success=True, job_poll_interval=0.1
        )

        with patch("clustrix.executor_core.ConnectionManager"), patch(
            "clustrix.executor_core.SchedulerManager"
        ), patch("clustrix.executor_core.KubernetesJobManager"), patch(
            "clustrix.executor_core.CloudJobManager"
        ):

            executor = ClusterExecutor(config)
            return executor

    def test_wait_for_result_cloud_job(self, mock_executor_with_active_jobs):
        """Test waiting for cloud job result."""
        executor = mock_executor_with_active_jobs
        executor.active_jobs["lambda_123"] = {
            "manager": "cloud",
            "job_id": "lambda_123",
        }
        executor.cloud_manager.wait_for_cloud_result.return_value = {
            "result": "success"
        }

        result = executor.wait_for_result("lambda_123")

        executor.cloud_manager.wait_for_cloud_result.assert_called_once_with(
            "lambda_123"
        )
        assert result == {"result": "success"}
        assert (
            "lambda_123" not in executor.active_jobs
        )  # Should be removed after completion

    def test_wait_for_result_kubernetes_job(self, mock_executor_with_active_jobs):
        """Test waiting for Kubernetes job result."""
        executor = mock_executor_with_active_jobs
        executor.active_jobs["clustrix-job-456"] = {
            "manager": "kubernetes",
            "job_id": "clustrix-job-456",
        }
        executor.k8s_manager.wait_for_k8s_result.return_value = [1, 2, 3, 4, 5]

        result = executor.wait_for_result("clustrix-job-456")

        executor.k8s_manager.wait_for_k8s_result.assert_called_once_with(
            "clustrix-job-456"
        )
        assert result == [1, 2, 3, 4, 5]
        assert "clustrix-job-456" not in executor.active_jobs

    def test_wait_for_result_scheduler_job_success(
        self, mock_executor_with_active_jobs
    ):
        """Test waiting for successful scheduler job result."""
        executor = mock_executor_with_active_jobs
        job_id = "scheduler_789"
        remote_dir = "/tmp/test_job"

        executor.active_jobs[job_id] = {"manager": "scheduler", "job_id": job_id}
        executor.scheduler_manager.active_jobs = {job_id: {"remote_dir": remote_dir}}

        # Mock successful result polling
        executor.scheduler_manager.check_job_status.return_value = "completed"

        # Mock result file download and deserialization
        test_result = {"computation": "completed", "value": 42}

        with patch("tempfile.NamedTemporaryFile") as mock_temp, patch(
            "builtins.open", mock_open()
        ) as mock_file_open, patch("pickle.load") as mock_pickle, patch(
            "os.unlink"
        ) as mock_unlink:

            mock_file = Mock()
            mock_file.name = "/tmp/test_result.pkl"
            mock_temp.return_value.__enter__.return_value = mock_file

            mock_pickle.return_value = test_result

            result = executor.wait_for_result(job_id)

            # Verify file download was attempted
            executor.connection_manager.download_file.assert_called_once_with(
                f"{remote_dir}/result.pkl", mock_file.name
            )

            # Verify file was opened for reading
            mock_file_open.assert_called_once_with(mock_file.name, "rb")

            # Verify cleanup if configured
            executor.connection_manager.execute_remote_command.assert_called_with(
                f"rm -rf {remote_dir}"
            )

            assert result == test_result
            assert job_id not in executor.active_jobs
            assert job_id not in executor.scheduler_manager.active_jobs

    def test_wait_for_result_scheduler_job_failure(
        self, mock_executor_with_active_jobs
    ):
        """Test waiting for failed scheduler job with error handling."""
        executor = mock_executor_with_active_jobs
        job_id = "scheduler_failed"
        remote_dir = "/tmp/failed_job"

        executor.active_jobs[job_id] = {"manager": "scheduler", "job_id": job_id}
        executor.scheduler_manager.active_jobs = {job_id: {"remote_dir": remote_dir}}

        # Mock failed job status
        executor.scheduler_manager.check_job_status.return_value = "failed"
        executor.scheduler_manager.get_error_log.return_value = (
            "Job failed due to memory limit"
        )
        executor.scheduler_manager.extract_original_exception.return_value = None

        with pytest.raises(RuntimeError, match="Job .* failed"):
            executor.wait_for_result(job_id)

        executor.scheduler_manager.get_error_log.assert_called_once_with(job_id)
        executor.scheduler_manager.extract_original_exception.assert_called_once_with(
            job_id
        )

    def test_wait_for_result_scheduler_job_original_exception(
        self, mock_executor_with_active_jobs
    ):
        """Test waiting for failed scheduler job with original exception."""
        executor = mock_executor_with_active_jobs
        job_id = "scheduler_exception"

        executor.active_jobs[job_id] = {"manager": "scheduler", "job_id": job_id}
        executor.scheduler_manager.active_jobs = {job_id: {"remote_dir": "/tmp/test"}}

        original_error = ValueError("Custom error from remote execution")
        executor.scheduler_manager.check_job_status.return_value = "failed"
        executor.scheduler_manager.extract_original_exception.return_value = (
            original_error
        )

        with pytest.raises(ValueError, match="Custom error from remote execution"):
            executor.wait_for_result(job_id)

    @patch("time.sleep")
    def test_wait_for_result_polling_behavior(
        self, mock_sleep, mock_executor_with_active_jobs
    ):
        """Test that result polling respects job_poll_interval."""
        executor = mock_executor_with_active_jobs
        job_id = "scheduler_polling"

        executor.active_jobs[job_id] = {"manager": "scheduler", "job_id": job_id}
        executor.scheduler_manager.active_jobs = {job_id: {"remote_dir": "/tmp/test"}}

        # Mock status progression: running -> running -> completed
        status_sequence = ["running", "running", "completed"]
        executor.scheduler_manager.check_job_status.side_effect = status_sequence

        # Mock successful result
        with patch("tempfile.NamedTemporaryFile"), patch(
            "pickle.load", return_value={"result": "polled"}
        ):

            result = executor.wait_for_result(job_id)

            # Should have slept twice (between the three status checks)
            assert mock_sleep.call_count == 2
            mock_sleep.assert_called_with(executor.config.job_poll_interval)
            assert result == {"result": "polled"}

    def test_wait_for_result_fallback_patterns(self, mock_executor_with_active_jobs):
        """Test fallback result handling for untracked jobs."""
        executor = mock_executor_with_active_jobs

        # Test cloud job fallback
        executor.cloud_manager.wait_for_cloud_result.return_value = {"cloud": "result"}
        result = executor.wait_for_result("lambda_untracked")
        executor.cloud_manager.wait_for_cloud_result.assert_called_once_with(
            "lambda_untracked"
        )
        assert result == {"cloud": "result"}

        # Test Kubernetes job fallback
        executor.k8s_manager.wait_for_k8s_result.return_value = {"k8s": "result"}
        result = executor.wait_for_result("clustrix-job-untracked")
        executor.k8s_manager.wait_for_k8s_result.assert_called_once_with(
            "clustrix-job-untracked"
        )
        assert result == {"k8s": "result"}

    def test_get_result_alias(self, mock_executor_with_active_jobs):
        """Test get_result method as alias for wait_for_result."""
        executor = mock_executor_with_active_jobs
        executor.active_jobs["test_job"] = {"manager": "cloud", "job_id": "test_job"}
        executor.cloud_manager.wait_for_cloud_result.return_value = {"alias": "test"}

        result = executor.get_result("test_job")

        executor.cloud_manager.wait_for_cloud_result.assert_called_once_with("test_job")
        assert result == {"alias": "test"}


class TestErrorHandlingAndRecovery:
    """Test error handling, job failure recovery, and edge cases."""

    @pytest.fixture
    def mock_executor(self):
        """Create executor for error testing."""
        config = ClusterConfig(cluster_type="slurm")

        with patch("clustrix.executor_core.ConnectionManager"), patch(
            "clustrix.executor_core.SchedulerManager"
        ), patch("clustrix.executor_core.KubernetesJobManager"), patch(
            "clustrix.executor_core.CloudJobManager"
        ):

            return ClusterExecutor(config)

    def test_submit_job_connection_failure(self, mock_executor):
        """Test job submission when connection fails."""
        executor = mock_executor
        executor.connection_manager.connect.side_effect = Exception("Connection failed")

        func_data = {"function": b"test"}
        job_config = {"cores": 4}

        with pytest.raises(Exception, match="Connection failed"):
            executor.submit_job(func_data, job_config)

    def test_submit_job_scheduler_failure(self, mock_executor):
        """Test job submission when scheduler submission fails."""
        executor = mock_executor
        executor.scheduler_manager.submit_slurm_job.side_effect = RuntimeError(
            "SLURM unavailable"
        )

        func_data = {"function": b"test"}
        job_config = {"cores": 4}

        with pytest.raises(RuntimeError, match="SLURM unavailable"):
            executor.submit_job(func_data, job_config)

    def test_wait_for_result_unknown_job(self, mock_executor):
        """Test waiting for result of unknown job."""
        executor = mock_executor
        executor.scheduler_manager.active_jobs = {}  # No jobs tracked

        with pytest.raises(ValueError, match="Unknown job ID"):
            executor._wait_for_scheduler_result("unknown_job")

    def test_wait_for_result_file_download_failure(self, mock_executor):
        """Test result collection when file download fails."""
        executor = mock_executor
        job_id = "download_fail"

        executor.scheduler_manager.active_jobs = {job_id: {"remote_dir": "/tmp/test"}}
        executor.scheduler_manager.check_job_status.return_value = "completed"
        executor.connection_manager.download_file.side_effect = Exception(
            "Download failed"
        )

        with patch("tempfile.NamedTemporaryFile"), patch("os.unlink"):
            with pytest.raises(Exception, match="Download failed"):
                executor._wait_for_scheduler_result(job_id)

    def test_wait_for_result_pickle_load_failure(self, mock_executor):
        """Test result collection when pickle deserialization fails."""
        executor = mock_executor
        job_id = "pickle_fail"

        executor.scheduler_manager.active_jobs = {job_id: {"remote_dir": "/tmp/test"}}
        executor.scheduler_manager.check_job_status.return_value = "completed"

        with patch("tempfile.NamedTemporaryFile"), patch(
            "pickle.load", side_effect=pickle.UnpicklingError("Invalid pickle")
        ), patch("os.unlink"):

            with pytest.raises(pickle.UnpicklingError):
                executor._wait_for_scheduler_result(job_id)


class TestResourceCleanup:
    """Test resource cleanup and job cancellation."""

    @pytest.fixture
    def mock_executor_with_jobs(self):
        """Create executor with jobs for cleanup testing."""
        config = ClusterConfig(cluster_type="slurm")

        with patch("clustrix.executor_core.ConnectionManager"), patch(
            "clustrix.executor_core.SchedulerManager"
        ), patch("clustrix.executor_core.KubernetesJobManager"), patch(
            "clustrix.executor_core.CloudJobManager"
        ):

            executor = ClusterExecutor(config)
            executor.active_jobs = {
                "sched_job": {"manager": "scheduler", "job_id": "sched_job"},
                "k8s_job": {"manager": "kubernetes", "job_id": "k8s_job"},
                "cloud_job": {"manager": "cloud", "job_id": "cloud_job"},
            }
            return executor

    def test_cancel_scheduler_job(self, mock_executor_with_jobs):
        """Test canceling tracked scheduler job."""
        executor = mock_executor_with_jobs

        executor.cancel_job("sched_job")

        executor.scheduler_manager.cancel_job.assert_called_once_with("sched_job")
        assert "sched_job" not in executor.active_jobs

    def test_cancel_kubernetes_job(self, mock_executor_with_jobs):
        """Test canceling tracked Kubernetes job."""
        executor = mock_executor_with_jobs
        executor.k8s_manager.active_jobs = {"k8s_job": {"test": "data"}}

        executor.cancel_job("k8s_job")

        executor.k8s_manager.cleanup_k8s_job.assert_called_once_with("k8s_job")
        assert "k8s_job" not in executor.active_jobs
        assert "k8s_job" not in executor.k8s_manager.active_jobs

    def test_cancel_cloud_job(self, mock_executor_with_jobs):
        """Test canceling tracked cloud job."""
        executor = mock_executor_with_jobs

        executor.cancel_job("cloud_job")

        executor.cloud_manager.cancel_cloud_job.assert_called_once_with("cloud_job")
        assert "cloud_job" not in executor.active_jobs

    def test_cancel_untracked_job_fallback_kubernetes(self, mock_executor_with_jobs):
        """Test canceling untracked Kubernetes job via fallback."""
        executor = mock_executor_with_jobs

        executor.cancel_job("clustrix-job-untracked")

        executor.k8s_manager.cleanup_k8s_job.assert_called_once_with(
            "clustrix-job-untracked"
        )

    def test_cancel_untracked_job_fallback_cloud(self, mock_executor_with_jobs):
        """Test canceling untracked cloud job via fallback."""
        executor = mock_executor_with_jobs

        executor.cancel_job("lambda_untracked")

        executor.cloud_manager.cancel_cloud_job.assert_called_once_with(
            "lambda_untracked"
        )

    def test_cancel_untracked_job_fallback_scheduler(self, mock_executor_with_jobs):
        """Test canceling untracked scheduler job via fallback."""
        executor = mock_executor_with_jobs

        executor.cancel_job("untracked_scheduler_job")

        executor.scheduler_manager.cancel_job.assert_called_once_with(
            "untracked_scheduler_job"
        )


class TestConnectionManagement:
    """Test connection establishment and management."""

    @pytest.fixture
    def mock_executor(self):
        """Create executor for connection testing."""
        config = ClusterConfig(cluster_type="slurm", cluster_host="test.com")

        with patch("clustrix.executor_core.ConnectionManager") as mock_conn, patch(
            "clustrix.executor_core.SchedulerManager"
        ), patch("clustrix.executor_core.KubernetesJobManager"), patch(
            "clustrix.executor_core.CloudJobManager"
        ):

            executor = ClusterExecutor(config)
            return executor, mock_conn

    def test_connect_delegation(self, mock_executor):
        """Test connect method delegates to connection manager."""
        executor, mock_conn = mock_executor

        executor.connect()

        executor.connection_manager.connect.assert_called_once()

    def test_disconnect_delegation(self, mock_executor):
        """Test disconnect method delegates to connection manager."""
        executor, mock_conn = mock_executor

        executor.disconnect()

        executor.connection_manager.disconnect.assert_called_once()


class TestSimplifiedExecuteInterface:
    """Test the simplified execute interface for testing."""

    @pytest.fixture
    def mock_executor(self):
        """Create executor for execute interface testing."""
        config = ClusterConfig(cluster_type="slurm")

        with patch("clustrix.executor_core.ConnectionManager"), patch(
            "clustrix.executor_core.SchedulerManager"
        ), patch("clustrix.executor_core.KubernetesJobManager"), patch(
            "clustrix.executor_core.CloudJobManager"
        ):

            return ClusterExecutor(config)

    @patch("cloudpickle.dumps")
    @patch("pickle.dumps")
    def test_execute_function_serialization(
        self, mock_pickle, mock_cloudpickle, mock_executor
    ):
        """Test execute method properly serializes function and arguments."""
        executor = mock_executor

        # Mock serialization
        mock_cloudpickle.return_value = b"serialized_func"
        mock_pickle.return_value = b"serialized_data"

        # Mock job submission and result
        executor.submit_job = Mock(return_value="test_job_123")
        executor.wait_for_result = Mock(return_value="execution_result")

        def test_function(x, y):
            return x + y

        result = executor.execute(test_function, (5, 3), {"multiplier": 2})

        # Verify serialization calls
        mock_cloudpickle.assert_called_once_with(test_function, protocol=4)
        assert mock_pickle.call_count == 2  # args and kwargs serialized separately

        # Verify job submission structure
        executor.submit_job.assert_called_once()
        func_data = executor.submit_job.call_args[0][0]
        assert func_data["function"] == b"serialized_func"
        assert func_data["args"] == b"serialized_data"
        assert func_data["kwargs"] == b"serialized_data"
        assert func_data["requirements"] == {}

        # Verify job config defaults
        job_config = executor.submit_job.call_args[0][1]
        assert job_config["cores"] == 4
        assert job_config["memory"] == "8GB"
        assert job_config["time"] == "01:00:00"

        # Verify result
        executor.wait_for_result.assert_called_once_with("test_job_123")
        assert result == "execution_result"


class TestClusterManagementMethods:
    """Test cluster management and status methods."""

    @pytest.fixture
    def mock_executor(self):
        """Create executor for cluster management testing."""
        config = ClusterConfig(cluster_type="kubernetes", auto_provision_k8s=True)

        with patch("clustrix.executor_core.ConnectionManager") as mock_conn, patch(
            "clustrix.executor_core.SchedulerManager"
        ), patch("clustrix.executor_core.KubernetesJobManager"), patch(
            "clustrix.executor_core.CloudJobManager"
        ):

            executor = ClusterExecutor(config)
            return executor, mock_conn

    def test_cleanup_auto_provisioned_cluster(self, mock_executor):
        """Test cleanup of auto-provisioned cluster."""
        executor, mock_conn = mock_executor

        executor.cleanup_auto_provisioned_cluster()

        executor.connection_manager.cleanup_auto_provisioned_cluster.assert_called_once()

    def test_get_cluster_status(self, mock_executor):
        """Test getting cluster status."""
        executor, mock_conn = mock_executor
        expected_status = {"status": "ready", "nodes": 3}
        executor.connection_manager.get_cluster_status.return_value = expected_status

        status = executor.get_cluster_status()

        executor.connection_manager.get_cluster_status.assert_called_once()
        assert status == expected_status

    def test_ensure_cluster_ready(self, mock_executor):
        """Test ensuring cluster is ready."""
        executor, mock_conn = mock_executor
        executor.connection_manager.ensure_cluster_ready.return_value = True

        is_ready = executor.ensure_cluster_ready(timeout=600)

        executor.connection_manager.ensure_cluster_ready.assert_called_once_with(600)
        assert is_ready is True


class TestBackwardCompatibilityMethods:
    """Test backward compatibility methods."""

    @pytest.fixture
    def mock_executor(self):
        """Create executor for backward compatibility testing."""
        config = ClusterConfig(cluster_type="slurm")

        with patch("clustrix.executor_core.ConnectionManager"), patch(
            "clustrix.executor_core.SchedulerManager"
        ), patch("clustrix.executor_core.KubernetesJobManager"), patch(
            "clustrix.executor_core.CloudJobManager"
        ):

            return ClusterExecutor(config)

    def test_ssh_client_property_access(self, mock_executor):
        """Test SSH client property access."""
        executor = mock_executor
        mock_ssh_client = Mock()
        executor.connection_manager.ssh_client = mock_ssh_client

        # Test getter
        assert executor.ssh_client == mock_ssh_client

        # Test setter
        new_ssh_client = Mock()
        executor.ssh_client = new_ssh_client
        assert executor.connection_manager.ssh_client == new_ssh_client

    def test_sftp_client_property_access(self, mock_executor):
        """Test SFTP client property access."""
        executor = mock_executor
        mock_sftp_client = Mock()
        executor.connection_manager.sftp_client = mock_sftp_client

        # Test getter
        assert executor.sftp_client == mock_sftp_client

        # Test setter
        new_sftp_client = Mock()
        executor.sftp_client = new_sftp_client
        assert executor.connection_manager.sftp_client == new_sftp_client

    def test_k8s_client_property_access(self, mock_executor):
        """Test Kubernetes client property access."""
        executor = mock_executor
        mock_k8s_client = Mock()
        executor.connection_manager.k8s_client = mock_k8s_client

        # Test getter
        assert executor.k8s_client == mock_k8s_client

        # Test setter
        new_k8s_client = Mock()
        executor.k8s_client = new_k8s_client
        assert executor.connection_manager.k8s_client == new_k8s_client

    def test_backward_compatibility_method_delegation(self, mock_executor):
        """Test that backward compatibility methods delegate properly."""
        executor = mock_executor

        # Test connection setup methods
        executor._setup_ssh_connection()
        executor.connection_manager.setup_ssh_connection.assert_called_once()

        executor._setup_kubernetes()
        executor.connection_manager.setup_kubernetes.assert_called_once()

        # Test command execution methods
        executor._execute_remote_command("test command")
        executor.connection_manager.execute_remote_command.assert_called_once_with(
            "test command"
        )

        executor._execute_command("test command")
        executor.connection_manager.execute_remote_command.assert_called_with(
            "test command"
        )

        # Test file operation methods
        executor._upload_file("/local/path", "/remote/path")
        executor.connection_manager.upload_file.assert_called_once_with(
            "/local/path", "/remote/path"
        )

        executor._download_file("/remote/path", "/local/path")
        executor.connection_manager.download_file.assert_called_once_with(
            "/remote/path", "/local/path"
        )

        executor._create_remote_file("/remote/file", "content")
        executor.connection_manager.create_remote_file.assert_called_once_with(
            "/remote/file", "content"
        )

        executor._remote_file_exists("/remote/file")
        executor.connection_manager.remote_file_exists.assert_called_once_with(
            "/remote/file"
        )

    def test_job_status_backward_compatibility(self, mock_executor):
        """Test job status backward compatibility method."""
        executor = mock_executor
        executor.scheduler_manager.check_job_status.return_value = "running"

        status = executor._check_job_status("job_123")

        executor.scheduler_manager.check_job_status.assert_called_once_with("job_123")
        assert status == "running"

    @patch("cloudpickle.dumps")
    def test_prepare_function_data_backward_compatibility(
        self, mock_cloudpickle, mock_executor
    ):
        """Test function data preparation backward compatibility."""
        executor = mock_executor
        mock_cloudpickle.return_value = b"serialized"

        def test_func():
            pass

        result = executor._prepare_function_data(
            test_func, (1, 2), {"key": "value"}, {"cores": 4}
        )

        assert result == b"serialized"
        mock_cloudpickle.assert_called_once()

        # Verify the structure passed to cloudpickle
        call_args = mock_cloudpickle.call_args[0][0]
        assert call_args["func"] == test_func
        assert call_args["args"] == (1, 2)
        assert call_args["kwargs"] == {"key": "value"}
        assert call_args["config"] == {"cores": 4}

    def test_error_handling_backward_compatibility(self, mock_executor):
        """Test error handling backward compatibility methods."""
        executor = mock_executor

        # Test with tracked job
        executor.active_jobs["test_job"] = {
            "manager": "scheduler",
            "job_id": "test_job",
        }
        executor.scheduler_manager.get_error_log.return_value = "Error log content"

        error_log = executor._get_error_log("test_job")
        executor.scheduler_manager.get_error_log.assert_called_once_with("test_job")
        assert error_log == "Error log content"

        # Test extract original exception
        test_exception = ValueError("Test error")
        executor.scheduler_manager.extract_original_exception.return_value = (
            test_exception
        )

        exception = executor._extract_original_exception("test_job")
        executor.scheduler_manager.extract_original_exception.assert_called_once_with(
            "test_job"
        )
        assert exception == test_exception

    def test_job_submission_backward_compatibility(self, mock_executor):
        """Test job submission backward compatibility methods."""
        executor = mock_executor

        func_data = {"function": b"test"}
        job_config = {"cores": 4}

        # Test SLURM submission
        executor.scheduler_manager.submit_slurm_job.return_value = "slurm_123"
        job_id = executor._submit_slurm_job(func_data, job_config)
        executor.scheduler_manager.submit_slurm_job.assert_called_once_with(
            func_data, job_config
        )
        assert job_id == "slurm_123"

        # Test PBS submission
        executor.scheduler_manager.submit_pbs_job.return_value = "pbs_456"
        job_id = executor._submit_pbs_job(func_data, job_config)
        executor.scheduler_manager.submit_pbs_job.assert_called_once_with(
            func_data, job_config
        )
        assert job_id == "pbs_456"

        # Test SGE submission
        executor.scheduler_manager.submit_sge_job.return_value = "sge_789"
        job_id = executor._submit_sge_job(func_data, job_config)
        executor.scheduler_manager.submit_sge_job.assert_called_once_with(
            func_data, job_config
        )
        assert job_id == "sge_789"

        # Test Kubernetes submission
        executor.k8s_manager.submit_k8s_job.return_value = "clustrix-job-123"
        job_id = executor._submit_k8s_job(func_data, job_config)
        executor.k8s_manager.submit_k8s_job.assert_called_once_with(
            func_data, job_config
        )
        assert job_id == "clustrix-job-123"

    def test_status_checking_backward_compatibility(self, mock_executor):
        """Test status checking backward compatibility methods."""
        executor = mock_executor

        # Mock status manager for detailed status checking
        executor.scheduler_manager.status_manager = Mock()
        executor.scheduler_manager.active_jobs = {"job_123": {"test": "data"}}

        # Test SLURM status checking
        executor.scheduler_manager.status_manager._check_slurm_job_status_robust.return_value = (
            "running"
        )
        status = executor._check_slurm_status("job_123")
        executor.scheduler_manager.status_manager._check_slurm_job_status_robust.assert_called_once_with(
            "job_123", executor.scheduler_manager.active_jobs
        )
        assert status == "running"

        # Test PBS status checking
        executor.scheduler_manager.status_manager._check_pbs_status.return_value = (
            "queued"
        )
        status = executor._check_pbs_status("job_456")
        executor.scheduler_manager.status_manager._check_pbs_status.assert_called_once_with(
            "job_456"
        )
        assert status == "queued"

        # Test SGE status checking
        executor.scheduler_manager.status_manager._check_sge_status.return_value = (
            "completed"
        )
        status = executor._check_sge_status("job_789")
        executor.scheduler_manager.status_manager._check_sge_status.assert_called_once_with(
            "job_789"
        )
        assert status == "completed"


class TestDestructorBehavior:
    """Test destructor and cleanup behavior."""

    def test_destructor_cleanup_auto_provisioned_cluster(self):
        """Test destructor cleans up auto-provisioned cluster."""
        config = ClusterConfig(cluster_type="kubernetes", k8s_auto_cleanup=True)

        with patch("clustrix.executor_core.ConnectionManager") as mock_conn, patch(
            "clustrix.executor_core.SchedulerManager"
        ), patch("clustrix.executor_core.KubernetesJobManager"), patch(
            "clustrix.executor_core.CloudJobManager"
        ), patch(
            "clustrix.executor_core.logger"
        ):

            executor = ClusterExecutor(config)

            # Mock the provisioner attribute to trigger cleanup path
            executor.connection_manager._k8s_provisioner = Mock()

            # Mock the cleanup method
            executor.cleanup_auto_provisioned_cluster = Mock()
            executor.disconnect = Mock()

            # Call destructor explicitly for testing
            executor.__del__()

            # Verify cleanup was called
            executor.cleanup_auto_provisioned_cluster.assert_called_once()
            executor.disconnect.assert_called_once()

    def test_destructor_exception_handling(self):
        """Test destructor handles cleanup exceptions gracefully."""
        config = ClusterConfig(cluster_type="kubernetes", k8s_auto_cleanup=True)

        with patch("clustrix.executor_core.ConnectionManager") as mock_conn, patch(
            "clustrix.executor_core.SchedulerManager"
        ), patch("clustrix.executor_core.KubernetesJobManager"), patch(
            "clustrix.executor_core.CloudJobManager"
        ), patch(
            "clustrix.executor_core.logger"
        ) as mock_logger:

            executor = ClusterExecutor(config)
            executor.connection_manager._k8s_provisioner = Mock()

            # Mock cleanup to raise exception
            executor.cleanup_auto_provisioned_cluster = Mock(
                side_effect=Exception("Cleanup failed")
            )
            executor.disconnect = Mock()

            # Call destructor explicitly for testing
            try:
                executor.__del__()
            except Exception:
                pytest.fail("Destructor should not raise exceptions")

            # Verify error was logged but not raised
            mock_logger.error.assert_called_once()
            executor.disconnect.assert_called_once()
