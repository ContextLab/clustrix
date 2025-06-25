import os
import time
import subprocess
import tempfile
import pickle
import logging
from pathlib import Path
from typing import Any, Dict, Optional
import paramiko
import json

from .config import ClusterConfig
from .utils import create_job_script, setup_remote_environment


logger = logging.getLogger(__name__)


class ClusterExecutor:
    """Handles execution of jobs on various cluster types."""

    def __init__(self, config: ClusterConfig):
        self.config = config
        self.ssh_client = None
        self.active_jobs = {}

        # Initialize connection based on cluster type
        if config.cluster_type in ["slurm", "pbs", "sge", "ssh"]:
            self._setup_ssh_connection()
        elif config.cluster_type == "kubernetes":
            self._setup_kubernetes()

    def _setup_ssh_connection(self):
        """Setup SSH connection to cluster."""
        if not self.config.cluster_host:
            raise ValueError("cluster_host must be specified for SSH-based clusters")

        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Connect using provided credentials
        connect_kwargs = {
            "hostname": self.config.cluster_host,
            "port": self.config.cluster_port,
        }

        if self.config.key_file:
            connect_kwargs["key_filename"] = self.config.key_file
        elif self.config.username and self.config.password:
            connect_kwargs.update(
                {"username": self.config.username, "password": self.config.password}
            )
        else:
            # Try to use SSH agent or default keys
            connect_kwargs["username"] = self.config.username or os.getenv("USER")

        self.ssh_client.connect(**connect_kwargs)

    def _setup_kubernetes(self):
        """Setup Kubernetes client."""
        try:
            from kubernetes import client, config

            config.load_kube_config()
            self.k8s_client = client.ApiClient()
        except ImportError:
            raise ImportError(
                "kubernetes package required for Kubernetes cluster support"
            )

    def submit_job(self, func_data: Dict[str, Any], job_config: Dict[str, Any]) -> str:
        """
        Submit a job to the cluster.

        Args:
            func_data: Serialized function and data
            job_config: Job configuration parameters

        Returns:
            Job ID for tracking
        """

        if self.config.cluster_type == "slurm":
            return self._submit_slurm_job(func_data, job_config)
        elif self.config.cluster_type == "pbs":
            return self._submit_pbs_job(func_data, job_config)
        elif self.config.cluster_type == "sge":
            return self._submit_sge_job(func_data, job_config)
        elif self.config.cluster_type == "kubernetes":
            return self._submit_k8s_job(func_data, job_config)
        elif self.config.cluster_type == "ssh":
            return self._submit_ssh_job(func_data, job_config)
        else:
            raise ValueError(f"Unsupported cluster type: {self.config.cluster_type}")

    def _submit_slurm_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via SLURM."""

        # Create remote working directory
        remote_job_dir = f"{self.config.remote_work_dir}/job_{int(time.time())}"
        self._execute_remote_command(f"mkdir -p {remote_job_dir}")

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f)
            local_pickle_path = f.name

        self._upload_file(local_pickle_path, f"{remote_job_dir}/function_data.pkl")
        os.unlink(local_pickle_path)

        # Setup environment
        setup_remote_environment(
            self.ssh_client, remote_job_dir, func_data["requirements"]
        )

        # Create job script
        script_content = create_job_script(
            cluster_type="slurm",
            job_config=job_config,
            remote_job_dir=remote_job_dir,
            config=self.config,
        )

        # Upload and submit job script
        script_path = f"{remote_job_dir}/job.sh"
        self._create_remote_file(script_path, script_content)

        # Submit job
        cmd = f"cd {remote_job_dir} && sbatch job.sh"
        stdout, stderr = self._execute_remote_command(cmd)

        # Extract job ID from sbatch output
        job_id = stdout.strip().split()[-1]

        # Store job info
        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "submitted",
            "submit_time": time.time(),
        }

        return job_id

    def _submit_pbs_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via PBS."""
        # Similar to SLURM but with PBS commands
        remote_job_dir = f"{self.config.remote_work_dir}/job_{int(time.time())}"
        self._execute_remote_command(f"mkdir -p {remote_job_dir}")

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f)
            local_pickle_path = f.name

        self._upload_file(local_pickle_path, f"{remote_job_dir}/function_data.pkl")
        os.unlink(local_pickle_path)

        # Create PBS script
        script_content = create_job_script(
            cluster_type="pbs",
            job_config=job_config,
            remote_job_dir=remote_job_dir,
            config=self.config,
        )

        script_path = f"{remote_job_dir}/job.pbs"
        self._create_remote_file(script_path, script_content)

        # Submit job
        cmd = f"cd {remote_job_dir} && qsub job.pbs"
        stdout, stderr = self._execute_remote_command(cmd)

        job_id = stdout.strip()

        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "submitted",
            "submit_time": time.time(),
        }

        return job_id

    def _submit_sge_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via SGE."""
        # Similar implementation for Sun Grid Engine
        pass

    def _submit_k8s_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via Kubernetes."""
        # Kubernetes job implementation
        pass

    def _submit_ssh_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via direct SSH (no scheduler)."""
        remote_job_dir = f"{self.config.remote_work_dir}/job_{int(time.time())}"
        self._execute_remote_command(f"mkdir -p {remote_job_dir}")

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f)
            local_pickle_path = f.name

        self._upload_file(local_pickle_path, f"{remote_job_dir}/function_data.pkl")
        os.unlink(local_pickle_path)

        # Create execution script
        script_content = create_job_script(
            cluster_type="ssh",
            job_config=job_config,
            remote_job_dir=remote_job_dir,
            config=self.config,
        )

        script_path = f"{remote_job_dir}/job.sh"
        self._create_remote_file(script_path, script_content)

        # Execute in background
        cmd = f"cd {remote_job_dir} && nohup bash job.sh > job.out 2> job.err < /dev/null &"
        stdout, stderr = self._execute_remote_command(cmd)

        # Use timestamp as job ID for SSH
        job_id = f"ssh_{int(time.time())}"

        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "running",
            "submit_time": time.time(),
        }

        return job_id

    def wait_for_result(self, job_id: str) -> Any:
        """
        Wait for job completion and return result.

        Args:
            job_id: Job identifier

        Returns:
            Function execution result
        """

        job_info = self.active_jobs.get(job_id)
        if not job_info:
            raise ValueError(f"Unknown job ID: {job_id}")

        remote_dir = job_info["remote_dir"]

        # Poll for completion
        while True:
            status = self._check_job_status(job_id)

            if status == "completed":
                # Download result
                result_path = f"{remote_dir}/result.pkl"

                with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
                    local_result_path = f.name

                try:
                    self._download_file(result_path, local_result_path)

                    with open(local_result_path, "rb") as f:
                        result = pickle.load(f)

                    # Cleanup
                    if self.config.cleanup_on_success:
                        self._execute_remote_command(f"rm -rf {remote_dir}")

                    del self.active_jobs[job_id]

                    return result

                finally:
                    if os.path.exists(local_result_path):
                        os.unlink(local_result_path)

            elif status == "failed":
                # Download error logs
                error_log = self._get_error_log(job_id)
                raise RuntimeError(f"Job {job_id} failed. Error log:\n{error_log}")

            # Wait before next poll
            time.sleep(self.config.job_poll_interval)

    def _check_job_status(self, job_id: str) -> str:
        """Check job status."""

        if self.config.cluster_type == "slurm":
            cmd = f"squeue -j {job_id} -h -o %T"
            try:
                stdout, stderr = self._execute_remote_command(cmd)
                if not stdout.strip():
                    # Job not in queue, check if result exists
                    job_info = self.active_jobs[job_id]
                    result_exists = self._remote_file_exists(
                        f"{job_info['remote_dir']}/result.pkl"
                    )
                    return "completed" if result_exists else "failed"
                else:
                    slurm_status = stdout.strip()
                    if slurm_status in ["COMPLETED"]:
                        return "completed"
                    elif slurm_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                        return "failed"
                    else:
                        return "running"
            except:
                return "unknown"

        elif self.config.cluster_type == "pbs":
            cmd = f"qstat -f {job_id}"
            try:
                stdout, stderr = self._execute_remote_command(cmd)
                if "job_state = C" in stdout:
                    return "completed"
                elif "job_state = R" in stdout:
                    return "running"
                else:
                    return "failed"
            except:
                # Job might be completed and removed from queue
                job_info = self.active_jobs[job_id]
                result_exists = self._remote_file_exists(
                    f"{job_info['remote_dir']}/result.pkl"
                )
                return "completed" if result_exists else "failed"

        elif self.config.cluster_type == "ssh":
            # For SSH jobs, check if result file exists
            job_info = self.active_jobs[job_id]
            result_exists = self._remote_file_exists(
                f"{job_info['remote_dir']}/result.pkl"
            )
            error_exists = self._remote_file_exists(f"{job_info['remote_dir']}/job.err")

            if result_exists:
                return "completed"
            elif error_exists:
                # Check if error file has content indicating failure
                try:
                    stdout, _ = self._execute_remote_command(
                        f"wc -l {job_info['remote_dir']}/job.err"
                    )
                    line_count = int(stdout.strip().split()[0])
                    if line_count > 0:
                        return "failed"
                except:
                    pass
                return "running"
            else:
                return "running"

        return "unknown"

    def _execute_remote_command(self, command: str) -> tuple:
        """Execute command on remote cluster."""
        stdin, stdout, stderr = self.ssh_client.exec_command(command)
        return stdout.read().decode(), stderr.read().decode()

    def _upload_file(self, local_path: str, remote_path: str):
        """Upload file to remote cluster."""
        sftp = self.ssh_client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()

    def _download_file(self, remote_path: str, local_path: str):
        """Download file from remote cluster."""
        sftp = self.ssh_client.open_sftp()
        sftp.get(remote_path, local_path)
        sftp.close()

    def _create_remote_file(self, remote_path: str, content: str):
        """Create file with content on remote cluster."""
        sftp = self.ssh_client.open_sftp()
        with sftp.open(remote_path, "w") as f:
            f.write(content)
        sftp.close()

    def _remote_file_exists(self, remote_path: str) -> bool:
        """Check if file exists on remote cluster."""
        try:
            sftp = self.ssh_client.open_sftp()
            sftp.stat(remote_path)
            sftp.close()
            return True
        except:
            return False

    def _get_error_log(self, job_id: str) -> str:
        """Get error log for failed job."""
        job_info = self.active_jobs.get(job_id)
        if not job_info:
            return "No job info available"

        remote_dir = job_info["remote_dir"]
        error_files = ["job.err", "slurm-*.out", "job.e*"]

        for error_file in error_files:
            try:
                stdout, _ = self._execute_remote_command(
                    f"cat {remote_dir}/{error_file} 2>/dev/null"
                )
                if stdout.strip():
                    return stdout
            except:
                continue

        return "No error log found"

    def cancel_job(self, job_id: str):
        """Cancel a running job."""
        if self.config.cluster_type == "slurm":
            self._execute_remote_command(f"scancel {job_id}")
        elif self.config.cluster_type == "pbs":
            self._execute_remote_command(f"qdel {job_id}")

        if job_id in self.active_jobs:
            del self.active_jobs[job_id]

    def __del__(self):
        """Cleanup resources."""
        if self.ssh_client:
            self.ssh_client.close()
