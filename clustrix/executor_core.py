"""Core ClusterExecutor class that coordinates all execution types.

This module provides the main ClusterExecutor class that acts as a coordinator
for different job execution backends (schedulers, Kubernetes, cloud providers).
"""

import time
import tempfile
import pickle
import logging
from typing import Any, Dict, Optional

import cloudpickle

from .executor_connections import ConnectionManager
from .executor_schedulers import SchedulerManager
from .executor_kubernetes import KubernetesJobManager
from .executor_cloud import CloudJobManager

logger = logging.getLogger(__name__)


class ClusterExecutor:
    """Handles execution of jobs on various cluster types."""

    def __init__(self, config):
        """Initialize the cluster executor.

        Args:
            config: ClusterConfig instance with execution settings
        """
        self.config = config

        # Initialize sub-managers
        self.connection_manager = ConnectionManager(config)
        self.scheduler_manager = SchedulerManager(config, self.connection_manager)
        self.k8s_manager = KubernetesJobManager(config, self.connection_manager)
        self.cloud_manager = CloudJobManager(config)

        # Combined active jobs tracking
        self.active_jobs: Dict[str, Any] = {}

        # Connection will be established on-demand

    def submit_job(self, func_data: Dict[str, Any], job_config: Dict[str, Any]) -> str:
        """
        Submit a job to the cluster.

        Args:
            func_data: Serialized function and data
            job_config: Job configuration parameters

        Returns:
            Job ID for tracking
        """
        # Check if this is a cloud provider job (but not auto-provisioned Kubernetes)
        provider = job_config.get("provider")
        if provider is not None and not (
            self.config.cluster_type == "kubernetes"
            and getattr(self.config, "auto_provision_k8s", False)
        ):
            # If provider is specified and not auto-provisioned K8s, use cloud provider routing
            supported_providers = ["lambda", "aws", "azure", "gcp", "huggingface"]
            if provider in supported_providers:
                job_id = self.cloud_manager.submit_cloud_job(
                    func_data, job_config, provider
                )
                # Track in combined active jobs
                self.active_jobs[job_id] = {"manager": "cloud", "job_id": job_id}
                return job_id
            else:
                raise ValueError(
                    f"Unsupported cloud provider: {provider}. Supported providers: {supported_providers}"
                )

        # If no provider specified, use traditional cluster routing

        # Ensure connection is established for traditional cluster types
        self.connect()

        if self.config.cluster_type == "slurm":
            job_id = self.scheduler_manager.submit_slurm_job(func_data, job_config)
            self.active_jobs[job_id] = {"manager": "scheduler", "job_id": job_id}
            return job_id
        elif self.config.cluster_type == "pbs":
            job_id = self.scheduler_manager.submit_pbs_job(func_data, job_config)
            self.active_jobs[job_id] = {"manager": "scheduler", "job_id": job_id}
            return job_id
        elif self.config.cluster_type == "sge":
            job_id = self.scheduler_manager.submit_sge_job(func_data, job_config)
            self.active_jobs[job_id] = {"manager": "scheduler", "job_id": job_id}
            return job_id
        elif self.config.cluster_type == "kubernetes":
            job_id = self.k8s_manager.submit_k8s_job(func_data, job_config)
            self.active_jobs[job_id] = {"manager": "kubernetes", "job_id": job_id}
            return job_id
        elif self.config.cluster_type == "ssh":
            job_id = self.scheduler_manager.submit_ssh_job(func_data, job_config)
            self.active_jobs[job_id] = {"manager": "scheduler", "job_id": job_id}
            return job_id
        else:
            raise ValueError(f"Unsupported cluster type: {self.config.cluster_type}")

    def wait_for_result(self, job_id: str) -> Any:
        """
        Wait for job completion and return result.

        Args:
            job_id: Job identifier

        Returns:
            Function execution result
        """
        # Check if this is tracked in our combined active jobs
        if job_id in self.active_jobs:
            manager_type = self.active_jobs[job_id]["manager"]

            if manager_type == "cloud":
                result = self.cloud_manager.wait_for_cloud_result(job_id)
                del self.active_jobs[job_id]
                return result
            elif manager_type == "kubernetes":
                result = self.k8s_manager.wait_for_k8s_result(job_id)
                del self.active_jobs[job_id]
                return result
            elif manager_type == "scheduler":
                # For scheduler jobs, delegate to wait_for_scheduler_result
                result = self._wait_for_scheduler_result(job_id)
                del self.active_jobs[job_id]
                return result

        # Fallback for jobs not in our tracking
        # This handles backward compatibility
        if (
            job_id.startswith("lambda_")
            or job_id.startswith("aws_")
            or job_id.startswith("azure_")
            or job_id.startswith("gcp_")
            or job_id.startswith("huggingface_")
        ):
            return self.cloud_manager.wait_for_cloud_result(job_id)
        elif job_id.startswith("clustrix-job-"):
            return self.k8s_manager.wait_for_k8s_result(job_id)
        else:
            return self._wait_for_scheduler_result(job_id)

    def _wait_for_scheduler_result(self, job_id: str) -> Any:
        """Wait for scheduler job result (SLURM/PBS/SGE/SSH)."""
        job_info = self.scheduler_manager.active_jobs.get(job_id)
        if not job_info:
            raise ValueError(f"Unknown job ID: {job_id}")

        remote_dir = job_info["remote_dir"]

        # Poll for completion
        while True:
            status = self.scheduler_manager.check_job_status(job_id)

            if status == "completed":
                # SSH-based job result collection
                result_path = f"{remote_dir}/result.pkl"

                with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
                    local_result_path = f.name

                try:
                    self.connection_manager.download_file(
                        result_path, local_result_path
                    )

                    with open(local_result_path, "rb") as f:
                        result = pickle.load(f)

                    # Cleanup
                    if self.config.cleanup_on_success:
                        self.connection_manager.execute_remote_command(
                            f"rm -rf {remote_dir}"
                        )

                    del self.scheduler_manager.active_jobs[job_id]
                    return result

                finally:
                    import os

                    if os.path.exists(local_result_path):
                        os.unlink(local_result_path)

            elif status == "failed":
                # SSH-based error handling
                error_log = self.scheduler_manager.get_error_log(job_id)
                original_exception = self.scheduler_manager.extract_original_exception(
                    job_id
                )

                if original_exception:
                    # Re-raise the original exception
                    raise original_exception
                else:
                    # Fallback to RuntimeError with log
                    raise RuntimeError(f"Job {job_id} failed. Error log:\n{error_log}")

            # Wait before next poll
            time.sleep(self.config.job_poll_interval)

    def get_job_status(self, job_id: str) -> str:
        """Get job status (alias for _check_job_status)."""
        # Check if this is a tracked job and delegate to appropriate manager
        if job_id in self.active_jobs:
            manager_type = self.active_jobs[job_id]["manager"]

            if manager_type == "cloud":
                return self.cloud_manager.get_cloud_job_status(job_id)
            elif manager_type == "kubernetes":
                return self.k8s_manager.check_k8s_job_status(job_id)
            elif manager_type == "scheduler":
                return self.scheduler_manager.check_job_status(job_id)

        # Fallback for untracked jobs
        if (
            job_id.startswith("lambda_")
            or job_id.startswith("aws_")
            or job_id.startswith("azure_")
            or job_id.startswith("gcp_")
            or job_id.startswith("huggingface_")
        ):
            return self.cloud_manager.get_cloud_job_status(job_id)
        elif job_id.startswith("clustrix-job-"):
            return self.k8s_manager.check_k8s_job_status(job_id)
        else:
            return self.scheduler_manager.check_job_status(job_id)

    def get_result(self, job_id: str) -> Any:
        """Get result (alias for wait_for_result)."""
        return self.wait_for_result(job_id)

    def cancel_job(self, job_id: str):
        """Cancel a running job."""
        # Check if this is a tracked job and delegate to appropriate manager
        if job_id in self.active_jobs:
            manager_type = self.active_jobs[job_id]["manager"]

            if manager_type == "cloud":
                self.cloud_manager.cancel_cloud_job(job_id)
                del self.active_jobs[job_id]
                return
            elif manager_type == "kubernetes":
                self.k8s_manager.cleanup_k8s_job(job_id)
                del self.k8s_manager.active_jobs[job_id]
                del self.active_jobs[job_id]
                return
            elif manager_type == "scheduler":
                self.scheduler_manager.cancel_job(job_id)
                del self.active_jobs[job_id]
                return

        # Fallback for untracked jobs
        if (
            job_id.startswith("lambda_")
            or job_id.startswith("aws_")
            or job_id.startswith("azure_")
            or job_id.startswith("gcp_")
            or job_id.startswith("huggingface_")
        ):
            self.cloud_manager.cancel_cloud_job(job_id)
        elif job_id.startswith("clustrix-job-"):
            self.k8s_manager.cleanup_k8s_job(job_id)
        else:
            self.scheduler_manager.cancel_job(job_id)

    def connect(self):
        """Establish connection to cluster (for manual connection)."""
        self.connection_manager.connect()

    def disconnect(self):
        """Disconnect from cluster."""
        self.connection_manager.disconnect()

    def execute(self, func, args: tuple, kwargs: dict) -> Any:
        """Execute function on cluster (simplified interface for tests)."""
        job_config = {"cores": 4, "memory": "8GB", "time": "01:00:00"}
        func_data = {
            "function": cloudpickle.dumps(func, protocol=4),
            "args": pickle.dumps(args, protocol=4),
            "kwargs": pickle.dumps(kwargs, protocol=4),
            "requirements": {},
        }

        job_id = self.submit_job(func_data, job_config)
        return self.wait_for_result(job_id)

    def cleanup_auto_provisioned_cluster(self):
        """Clean up auto-provisioned Kubernetes cluster."""
        self.connection_manager.cleanup_auto_provisioned_cluster()

    def get_cluster_status(self) -> Dict[str, Any]:
        """Get status of managed Kubernetes cluster."""
        return self.connection_manager.get_cluster_status()

    def ensure_cluster_ready(self, timeout: int = 900) -> bool:
        """Ensure auto-provisioned cluster is ready for job execution."""
        return self.connection_manager.ensure_cluster_ready(timeout)

    def __del__(self):
        """Cleanup resources."""
        # Clean up auto-provisioned cluster if configured to do so
        if hasattr(self.connection_manager, "_k8s_provisioner") and getattr(
            self.config, "k8s_cleanup_on_exit", True
        ):
            try:
                self.cleanup_auto_provisioned_cluster()
            except Exception as e:
                # Don't raise exceptions in destructor
                logger.error(f"Error during cluster cleanup in destructor: {e}")

        self.disconnect()

    # Backward compatibility properties and methods
    @property
    def ssh_client(self):
        """Access to SSH client for backward compatibility."""
        return self.connection_manager.ssh_client

    @ssh_client.setter
    def ssh_client(self, value):
        """Set SSH client for backward compatibility."""
        self.connection_manager.ssh_client = value

    @property
    def sftp_client(self):
        """Access to SFTP client for backward compatibility."""
        return self.connection_manager.sftp_client

    @sftp_client.setter
    def sftp_client(self, value):
        """Set SFTP client for backward compatibility."""
        self.connection_manager.sftp_client = value

    @property
    def k8s_client(self):
        """Access to Kubernetes client for backward compatibility."""
        return self.connection_manager.k8s_client

    @k8s_client.setter
    def k8s_client(self, value):
        """Set Kubernetes client for backward compatibility."""
        self.connection_manager.k8s_client = value

    def _setup_ssh_connection(self):
        """Backward compatibility method."""
        return self.connection_manager.setup_ssh_connection()

    def _setup_kubernetes(self):
        """Backward compatibility method."""
        return self.connection_manager.setup_kubernetes()

    def _execute_remote_command(self, command: str) -> tuple:
        """Backward compatibility method."""
        return self.connection_manager.execute_remote_command(command)

    def _upload_file(self, local_path: str, remote_path: str):
        """Backward compatibility method."""
        return self.connection_manager.upload_file(local_path, remote_path)

    def _download_file(self, remote_path: str, local_path: str):
        """Backward compatibility method."""
        return self.connection_manager.download_file(remote_path, local_path)

    def _create_remote_file(self, remote_path: str, content: str):
        """Backward compatibility method."""
        return self.connection_manager.create_remote_file(remote_path, content)

    def _remote_file_exists(self, remote_path: str) -> bool:
        """Backward compatibility method."""
        return self.connection_manager.remote_file_exists(remote_path)

    def _check_job_status(self, job_id: str) -> str:
        """Backward compatibility method."""
        return self.get_job_status(job_id)

    def _execute_command(self, command: str) -> tuple:
        """Backward compatibility method."""
        return self.connection_manager.execute_remote_command(command)

    def _prepare_function_data(
        self, func, args: tuple, kwargs: dict, config: dict
    ) -> bytes:
        """Prepare function data for serialization."""
        func_data = {
            "func": func,
            "args": args,
            "kwargs": kwargs,
            "config": config,
        }

        return cloudpickle.dumps(func_data)

    def _get_error_log(self, job_id: str) -> str:
        """Backward compatibility method."""
        # Check manager type for this job
        if job_id in self.active_jobs:
            manager_type = self.active_jobs[job_id]["manager"]
            if manager_type == "scheduler":
                return self.scheduler_manager.get_error_log(job_id)
            elif manager_type == "kubernetes":
                return self.k8s_manager.get_k8s_error_log(job_id)

        # Fallback for untracked jobs
        if job_id.startswith("clustrix-job-"):
            return self.k8s_manager.get_k8s_error_log(job_id)
        else:
            return self.scheduler_manager.get_error_log(job_id)

    def _extract_original_exception(self, job_id: str) -> Optional[Exception]:
        """Backward compatibility method."""
        # Check manager type for this job
        if job_id in self.active_jobs:
            manager_type = self.active_jobs[job_id]["manager"]
            if manager_type == "scheduler":
                return self.scheduler_manager.extract_original_exception(job_id)
            elif manager_type == "kubernetes":
                return self.k8s_manager.extract_k8s_exception(job_id)

        # Fallback for untracked jobs
        if job_id.startswith("clustrix-job-"):
            return self.k8s_manager.extract_k8s_exception(job_id)
        else:
            return self.scheduler_manager.extract_original_exception(job_id)

    def _submit_slurm_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Backward compatibility method."""
        return self.scheduler_manager.submit_slurm_job(func_data, job_config)

    def _submit_pbs_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Backward compatibility method."""
        return self.scheduler_manager.submit_pbs_job(func_data, job_config)

    def _submit_sge_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Backward compatibility method."""
        return self.scheduler_manager.submit_sge_job(func_data, job_config)

    def _submit_k8s_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Backward compatibility method."""
        return self.k8s_manager.submit_k8s_job(func_data, job_config)

    def _check_slurm_status(self, job_id: str) -> str:
        """Backward compatibility method."""
        return self.scheduler_manager.status_manager._check_slurm_job_status_robust(
            job_id, self.scheduler_manager.active_jobs
        )

    def _check_pbs_status(self, job_id: str) -> str:
        """Backward compatibility method."""
        return self.scheduler_manager.status_manager._check_pbs_status(job_id)

    def _check_sge_status(self, job_id: str) -> str:
        """Backward compatibility method."""
        return self.scheduler_manager.status_manager._check_sge_status(job_id)
