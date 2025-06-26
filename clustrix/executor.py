import os
import time
import tempfile
import pickle
import logging
from typing import Any, Dict, Optional
import paramiko
import cloudpickle

from .config import ClusterConfig
from .utils import create_job_script, setup_remote_environment

logger = logging.getLogger(__name__)


class ClusterExecutor:
    """Handles execution of jobs on various cluster types."""

    def __init__(self, config: ClusterConfig):
        self.config = config
        self.ssh_client = None
        self.sftp_client = None
        self.active_jobs: Dict[str, Any] = {}

        # Connection will be established on-demand

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

        # Always include username if available
        if self.config.username:
            connect_kwargs["username"] = self.config.username
        else:
            connect_kwargs["username"] = os.getenv("USER")

        # Use key file for authentication (recommended)
        if self.config.key_file:
            connect_kwargs["key_filename"] = self.config.key_file
        elif self.config.password:
            # Fallback to password authentication (not recommended)
            connect_kwargs["password"] = self.config.password
        # Otherwise, fall back to SSH agent or default keys

        self.ssh_client.connect(**connect_kwargs)
        self.sftp_client = self.ssh_client.open_sftp()

    def _setup_kubernetes(self):
        """Setup Kubernetes client with optional cloud provider auto-configuration."""
        try:
            from kubernetes import client, config

            # Try cloud provider auto-configuration if enabled
            if (
                self.config.cloud_auto_configure
                and self.config.cluster_type == "kubernetes"
            ):
                try:
                    from .cloud_providers import CloudProviderManager

                    cloud_manager = CloudProviderManager(self.config)
                    result = cloud_manager.auto_configure()

                    if result.get("auto_configured"):
                        logger.info(
                            f"Auto-configured {result.get('provider')} cluster: {result.get('cluster_name')}"
                        )
                    else:
                        logger.info(
                            f"Cloud auto-configuration skipped: {result.get('reason', 'Unknown')}"
                        )
                        if "error" in result:
                            logger.warning(
                                f"Auto-configuration error: {result['error']}"
                            )

                except Exception as e:
                    logger.warning(f"Cloud provider auto-configuration failed: {e}")
                    # Continue with manual configuration

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
        # Ensure connection is established
        self.connect()

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
            self.ssh_client,
            remote_job_dir,
            func_data["requirements"],
            self.config,
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
            self.ssh_client,
            remote_job_dir,
            func_data["requirements"],
            self.config,
        )

        # Create job script
        script_content = create_job_script(
            cluster_type="sge",
            job_config=job_config,
            remote_job_dir=remote_job_dir,
            config=self.config,
        )

        # Upload and submit job script
        script_path = f"{remote_job_dir}/job.sge"
        self._create_remote_file(script_path, script_content)

        # Submit job
        cmd = f"cd {remote_job_dir} && qsub job.sge"
        stdout, stderr = self._execute_remote_command(cmd)

        # Extract job ID from qsub output (SGE format: "Your job 123456 ...")
        job_id = stdout.strip().split()[2] if "Your job" in stdout else stdout.strip()

        # Store job info
        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "submitted",
            "submit_time": time.time(),
        }

        return job_id

    def _submit_k8s_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """
        Submit a job to Kubernetes cluster using containerized Python execution.

        This method implements a sophisticated Kubernetes job submission strategy that
        packages Python functions and data into self-contained container jobs without
        requiring custom Docker images or persistent storage.

        **Architecture:**

        1. **Function Serialization**: Uses cloudpickle to serialize the function and all data
        2. **Base64 Encoding**: Encodes serialized data for safe embedding in container args
        3. **Container Execution**: Creates a Job with inline Python code that:
           - Decodes the base64 data
           - Deserializes the function and arguments
           - Executes the function
           - Captures results or errors
        4. **Resource Management**: Applies CPU and memory limits from job_config

        **Key Features:**
        - **No Custom Images**: Uses standard `python:3.11-slim` image
        - **Self-Contained**: All code and data embedded in Job manifest
        - **Resource Aware**: Respects CPU/memory requirements
        - **Error Handling**: Captures exceptions with full tracebacks
        - **Cloud Native**: Leverages Kubernetes Job semantics for reliability

        **Job Manifest Structure:**
        ```yaml
        apiVersion: batch/v1
        kind: Job
        metadata:
          name: clustrix-job-{timestamp}
        spec:
          template:
            spec:
              containers:
              - name: clustrix-worker
                image: python:3.11-slim
                command: ["python", "-c"]
                args: ["<embedded Python code>"]
                resources:
                  requests/limits: {cpu, memory from job_config}
              restartPolicy: Never
        ```

        Args:
            func_data: Serialized function data containing:
                      - 'func': The function to execute
                      - 'args': Positional arguments
                      - 'kwargs': Keyword arguments
                      - 'requirements': Package dependencies (not used for K8s)
            job_config: Job configuration including:
                       - 'cores': CPU request/limit (default: 1)
                       - 'memory': Memory request/limit (default: "1Gi")
                       - Additional K8s-specific settings

        Returns:
            str: Kubernetes Job name that can be used for status tracking

        Raises:
            ImportError: If kubernetes package is not installed
            Exception: If Kubernetes API calls fail

        Examples:
            >>> func_data = {
            ...     'func': lambda x: x**2,
            ...     'args': (5,),
            ...     'kwargs': {},
            ...     'requirements': {}
            ... }
            >>> job_config = {'cores': 2, 'memory': '4Gi'}
            >>> job_id = executor._submit_k8s_job(func_data, job_config)
            >>> print(job_id)  # "clustrix-job-1234567890"

        Note:
            - Requires kubernetes package: `pip install kubernetes`
            - Assumes kubectl is configured with cluster access
            - Jobs are created in the "default" namespace
            - Cloudpickle is used for function serialization
            - Results are captured via stdout parsing (CLUSTRIX_RESULT: prefix)
        """
        try:
            from kubernetes import client
        except ImportError:
            raise ImportError(
                "kubernetes package required for Kubernetes support. "
                "Install with: pip install kubernetes"
            )

        # Ensure Kubernetes client is set up
        if not hasattr(self, "k8s_client") or self.k8s_client is None:
            self._setup_kubernetes()

        # Create a unique job name
        job_name = f"clustrix-job-{int(time.time())}"

        # Serialize function data
        func_data_serialized = cloudpickle.dumps(func_data)
        import base64

        func_data_b64 = base64.b64encode(func_data_serialized).decode("utf-8")

        # Create Kubernetes Job manifest
        job_manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": job_name},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "clustrix-worker",
                                "image": self.config.k8s_image,
                                "command": ["python", "-c"],
                                "args": [
                                    f"""
import base64
import cloudpickle
import traceback

try:
    # Decode and deserialize function data
    func_data_b64 = "{func_data_b64}"
    func_data_bytes = base64.b64decode(func_data_b64)
    func_data = cloudpickle.loads(func_data_bytes)

    # Execute function
    func = func_data['func']
    args = func_data['args']
    kwargs = func_data['kwargs']

    result = func(*args, **kwargs)
    print(f"CLUSTRIX_RESULT:{{result}}")

except Exception as e:
    print(f"CLUSTRIX_ERROR:{{str(e)}}")
    print(f"CLUSTRIX_TRACEBACK:{{traceback.format_exc()}}")
    exit(1)
"""
                                ],
                                "resources": {
                                    "requests": {
                                        "cpu": str(job_config.get("cores", 1)),
                                        "memory": job_config.get("memory", "1Gi"),
                                    },
                                    "limits": {
                                        "cpu": str(job_config.get("cores", 1)),
                                        "memory": job_config.get("memory", "1Gi"),
                                    },
                                },
                            }
                        ],
                        "restartPolicy": "Never",
                    }
                },
                "backoffLimit": self.config.k8s_backoff_limit,
                "ttlSecondsAfterFinished": self.config.k8s_job_ttl_seconds,
            },
        }

        # Submit job to Kubernetes
        batch_api = client.BatchV1Api()
        response = batch_api.create_namespaced_job(
            namespace=self.config.k8s_namespace, body=job_manifest
        )

        job_id = response.metadata.name

        # Store job info
        self.active_jobs[job_id] = {
            "status": "submitted",
            "submit_time": time.time(),
            "k8s_job": True,
        }

        return job_id

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

        # Check if this is a Kubernetes job
        is_k8s_job = job_info.get("k8s_job", False)

        if not is_k8s_job:
            remote_dir = job_info["remote_dir"]

        # Poll for completion
        while True:
            status = self._check_job_status(job_id)

            if status == "completed":
                if is_k8s_job:
                    # For Kubernetes jobs, get result from pod logs
                    result = self._get_k8s_result(job_id)

                    # Cleanup
                    if self.config.cleanup_on_success:
                        self._cleanup_k8s_job(job_id)

                    del self.active_jobs[job_id]
                    return result
                else:
                    # SSH-based job result collection
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
                if is_k8s_job:
                    # For Kubernetes jobs, get error from pod logs
                    error_log = self._get_k8s_error_log(job_id)
                    original_exception = self._extract_k8s_exception(job_id)

                    if original_exception:
                        raise original_exception
                    else:
                        raise RuntimeError(
                            f"Kubernetes job {job_id} failed. Error log:\n{error_log}"
                        )
                else:
                    # SSH-based error handling
                    error_log = self._get_error_log(job_id)
                    original_exception = self._extract_original_exception(job_id)

                    if original_exception:
                        # Re-raise the original exception
                        raise original_exception
                    else:
                        # Fallback to RuntimeError with log
                        raise RuntimeError(
                            f"Job {job_id} failed. Error log:\n{error_log}"
                        )

            # Wait before next poll
            time.sleep(self.config.job_poll_interval)

    def _check_job_status(self, job_id: str) -> str:
        """
        Check the current status of a job across multiple cluster schedulers.

        This method implements cluster-specific job status checking with intelligent
        fallback mechanisms to handle various edge cases including completed jobs
        that have been removed from scheduler queues.

        **Multi-Scheduler Support:**

        - **SLURM**: Uses `squeue -j {job_id} -h -o %T` to check job status
        - **PBS**: Uses `qstat -f {job_id}` to query detailed job information
        - **SGE**: Job status checking (using similar logic to PBS)
        - **SSH**: File-based status detection (result.pkl vs error files)
        - **Kubernetes**: Pod/Job status via Kubernetes API

        **Status Detection Logic:**

        1. **Active Jobs**: Query scheduler-specific commands for current status
        2. **Completed Jobs**: Many schedulers remove completed jobs from queues,
           requiring file-based detection using result.pkl existence
        3. **Failed Jobs**: Detected through scheduler status or error file presence
        4. **Unknown Status**: Graceful handling when commands fail

        **Return Values:**
        - `"completed"`: Job finished successfully (result.pkl exists)
        - `"failed"`: Job failed (scheduler reports failure or error files exist)
        - `"running"`: Job is currently executing
        - `"queued"`: Job is waiting in scheduler queue
        - `"unknown"`: Status cannot be determined

        Args:
            job_id: Unique job identifier (scheduler-specific format)

        Returns:
            str: Current job status as a standardized string value

        Examples:
            >>> # SLURM job running
            >>> status = executor._check_job_status("12345")
            >>> print(status)  # "running"

            >>> # PBS job completed (removed from queue)
            >>> status = executor._check_job_status("67890.headnode")
            >>> print(status)  # "completed"

            >>> # SSH job failed
            >>> status = executor._check_job_status("ssh_1234567890")
            >>> print(status)  # "failed"

        Note:
            This method is called repeatedly by `wait_for_result()` during job polling.
            The implementation handles scheduler-specific quirks and provides robust
            status detection even when jobs are removed from scheduler queues.
        """

        if self.config.cluster_type == "slurm":
            cmd = f"squeue -j {job_id} -h -o %T"
            try:
                stdout, stderr = self._execute_remote_command(cmd)
                if not stdout.strip():
                    # Job not in queue, check if result exists
                    if job_id in self.active_jobs:
                        job_info = self.active_jobs[job_id]
                        result_exists = self._remote_file_exists(
                            f"{job_info['remote_dir']}/result.pkl"
                        )
                        return "completed" if result_exists else "failed"
                    else:
                        # Job not tracked, assume completed
                        return "completed"
                else:
                    slurm_status = stdout.strip()
                    if slurm_status in ["COMPLETED"]:
                        return "completed"
                    elif slurm_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                        return "failed"
                    else:
                        return "running"
            except Exception:
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
            except Exception:
                # Job might be completed and removed from queue
                if job_id in self.active_jobs:
                    job_info = self.active_jobs[job_id]
                    result_exists = self._remote_file_exists(
                        f"{job_info['remote_dir']}/result.pkl"
                    )
                    return "completed" if result_exists else "failed"
                else:
                    return "completed"

        elif self.config.cluster_type == "ssh":
            # For SSH jobs, check if result file exists
            if job_id in self.active_jobs:
                job_info = self.active_jobs[job_id]
                result_exists = self._remote_file_exists(
                    f"{job_info['remote_dir']}/result.pkl"
                )
                error_exists = self._remote_file_exists(
                    f"{job_info['remote_dir']}/job.err"
                )

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
                    except Exception:
                        pass
                    return "running"
                else:
                    return "running"
            else:
                return "completed"

        elif self.config.cluster_type == "kubernetes":
            # For Kubernetes jobs, check job status via API
            try:
                from kubernetes import client

                batch_api = client.BatchV1Api()

                # Get job status
                job = batch_api.read_namespaced_job(
                    name=job_id, namespace=self.config.k8s_namespace
                )

                # Check job conditions
                if job.status.succeeded:
                    return "completed"
                elif job.status.failed:
                    return "failed"
                elif job.status.active:
                    return "running"
                else:
                    return "pending"

            except Exception as e:
                # Job might have been deleted or not found
                if job_id in self.active_jobs:
                    # If we're tracking it but can't find it, consider it completed
                    return "completed"
                else:
                    return "unknown"

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
        except Exception:
            return False

    def _get_error_log(self, job_id: str) -> str:
        """
        Retrieve comprehensive error information from a failed job using multiple fallback mechanisms.

        This method implements a sophisticated error retrieval strategy that prioritizes
        structured error data (pickled exceptions) over raw log files, providing users
        with the most detailed and useful error information available.

        **Error Retrieval Strategy (in priority order):**

        1. **Pickled Error Data** (Highest Priority): Attempts to download and deserialize
           `error.pkl` containing structured exception information including:
           - Original exception objects
           - Error messages with full context
           - Complete stack traces

        2. **Text Log Files** (Fallback): Searches for various scheduler-specific log files:
           - job.err (standard error output)
           - slurm-*.out (SLURM output files)
           - job.e* (PBS/SGE error files)

        3. **No Error Found**: Returns appropriate message if no error information exists.

        **Structured Error Handling**: When error.pkl is found, the method handles multiple
        data formats gracefully:
        - Dictionary format: {'error': message, 'traceback': trace}
        - Direct exception objects
        - String representations

        Args:
            job_id: Unique identifier for the failed job

        Returns:
            str: Comprehensive error information including error messages and tracebacks.
                 Returns detailed structured information when available, or raw log
                 content as fallback.

        Examples:
            >>> # Structured error (preferred)
            >>> error_log = executor._get_error_log("job_12345")
            >>> # Returns: "ValueError: Division by zero\n\nTraceback:\n  File..."

            >>> # Text log fallback
            >>> error_log = executor._get_error_log("job_67890")
            >>> # Returns: "Error from job.err: Process failed with exit code 1"

            >>> # No error info
            >>> error_log = executor._get_error_log("job_unknown")
            >>> # Returns: "No error log found"

        Note:
            This method is typically called automatically by `wait_for_result()` when
            a job status is detected as "failed". It provides the error information
            used for exception re-raising and user notification.
        """
        job_info = self.active_jobs.get(job_id)
        if not job_info:
            return "No job info available"

        remote_dir = job_info["remote_dir"]

        # First, try to get pickled error data
        error_pkl_path = f"{remote_dir}/error.pkl"
        if self._remote_file_exists(error_pkl_path):
            try:
                with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
                    local_error_path = f.name

                self._download_file(error_pkl_path, local_error_path)

                with open(local_error_path, "rb") as f:
                    error_data = pickle.load(f)

                os.unlink(local_error_path)

                # Handle different error data formats
                if isinstance(error_data, dict):
                    error_msg = error_data.get("error", str(error_data))
                    traceback_info = error_data.get("traceback", "")
                    return f"{error_msg}\n\nTraceback:\n{traceback_info}"
                else:
                    return str(error_data)
            except Exception as e:
                # If error.pkl exists but can't be read, continue to text logs
                pass

        # Fallback to text error files
        error_files = ["job.err", "slurm-*.out", "job.e*"]

        for error_file in error_files:
            try:
                stdout, _ = self._execute_remote_command(
                    f"cat {remote_dir}/{error_file} 2>/dev/null"
                )
                if stdout.strip():
                    return stdout
            except Exception:
                continue

        return "No error log found"

    def _extract_original_exception(self, job_id: str) -> Optional[Exception]:
        """
        Extract and reconstruct the original exception from a failed remote job.

        This method enables proper exception propagation by retrieving and deserializing
        exception objects that were pickled during remote execution. This allows users
        to catch specific exception types (e.g., ValueError, KeyError) rather than
        generic RuntimeError wrappers.

        **Exception Reconstruction Process:**

        1. **Download Pickled Data**: Retrieves the error.pkl file from the remote job directory
        2. **Deserialize Exception**: Safely unpickles the exception data
        3. **Type Preservation**: Maintains original exception types and messages
        4. **Fallback Handling**: Creates RuntimeError for malformed exception data

        **Supported Exception Formats:**
        - **Direct Exception Objects**: Exception instances pickled directly
        - **Dictionary Format**: {'error': message, 'traceback': trace} structures
        - **Graceful Degradation**: Returns None if extraction fails

        Args:
            job_id: Unique identifier for the failed job

        Returns:
            Optional[Exception]: The original exception object if successfully extracted,
                               RuntimeError for recoverable data, or None if extraction
                               fails completely.

        Examples:
            >>> # Original ValueError preserved
            >>> exc = executor._extract_original_exception("job_123")
            >>> isinstance(exc, ValueError)  # True
            >>> str(exc)  # "Division by zero"

            >>> # Dictionary format converted
            >>> exc = executor._extract_original_exception("job_456")
            >>> isinstance(exc, RuntimeError)  # True (fallback)
            >>> str(exc)  # "Original error message"

            >>> # Extraction failed
            >>> exc = executor._extract_original_exception("job_789")
            >>> exc is None  # True

        Note:
            This method is called by `wait_for_result()` to enable proper exception
            re-raising. When successful, users can catch specific exception types
            instead of generic RuntimeError messages.

        See Also:
            _get_error_log(): Retrieves error information for logging/display
            wait_for_result(): Main method that uses both error retrieval functions
        """
        job_info = self.active_jobs.get(job_id)
        if not job_info:
            return None

        remote_dir = job_info["remote_dir"]
        error_pkl_path = f"{remote_dir}/error.pkl"

        if self._remote_file_exists(error_pkl_path):
            try:
                with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
                    local_error_path = f.name

                self._download_file(error_pkl_path, local_error_path)

                with open(local_error_path, "rb") as f:
                    error_data = pickle.load(f)

                os.unlink(local_error_path)

                # Return the exception object if it is one
                if isinstance(error_data, Exception):
                    return error_data
                elif isinstance(error_data, dict) and "error" in error_data:
                    # Try to recreate exception from dict
                    error_str = error_data["error"]
                    # This is a simplified approach - in practice you'd want more sophisticated exception recreation
                    return RuntimeError(error_str)

            except Exception:
                # If we can't extract the exception, return None
                pass

        return None

    def cancel_job(self, job_id: str):
        """Cancel a running job."""
        if self.config.cluster_type == "slurm":
            self._execute_remote_command(f"scancel {job_id}")
        elif self.config.cluster_type == "pbs":
            self._execute_remote_command(f"qdel {job_id}")

        if job_id in self.active_jobs:
            del self.active_jobs[job_id]

    def connect(self):
        """Establish connection to cluster (for manual connection)."""
        if self.config.cluster_type in ["slurm", "pbs", "sge", "ssh"]:
            if not self.ssh_client:
                self._setup_ssh_connection()
        elif self.config.cluster_type == "kubernetes":
            if not hasattr(self, "k8s_client"):
                self._setup_kubernetes()

    def disconnect(self):
        """Disconnect from cluster."""
        if self.sftp_client:
            self.sftp_client.close()
            self.sftp_client = None
        if self.ssh_client:
            self.ssh_client.close()
            self.ssh_client = None

    def _execute_command(self, command: str) -> tuple:
        """Execute command on remote cluster (alias for _execute_remote_command)."""
        if not self.ssh_client:
            raise RuntimeError("Not connected to cluster")
        return self._execute_remote_command(command)

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

    def execute(self, func, args: tuple, kwargs: dict) -> Any:
        """Execute function on cluster (simplified interface for tests)."""
        job_config = {"cores": 4, "memory": "8GB", "time": "01:00:00"}
        func_data = {
            "function": cloudpickle.dumps(func),
            "args": pickle.dumps(args),
            "kwargs": pickle.dumps(kwargs),
            "requirements": {},
        }

        job_id = self.submit_job(func_data, job_config)
        return self.wait_for_result(job_id)

    def _check_slurm_status(self, job_id: str) -> str:
        """Check SLURM job status."""
        cmd = f"squeue -j {job_id} -h -o %T"
        try:
            stdout, stderr = self._execute_remote_command(cmd)
            if not stdout.strip():
                # Job not in queue, assume completed
                return "completed"
            else:
                slurm_status = stdout.strip()
                if slurm_status in ["COMPLETED"]:
                    return "completed"
                elif slurm_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                    return "failed"
                else:
                    return "running"
        except Exception:
            return "unknown"

    def _check_pbs_status(self, job_id: str) -> str:
        """Check PBS job status."""
        cmd = f"qstat -f {job_id}"
        try:
            stdout, stderr = self._execute_remote_command(cmd)
            # Handle full format output (qstat -f)
            if "job_state = C" in stdout:
                return "completed"
            elif "job_state = Q" in stdout:
                return "queued"
            elif "job_state = R" in stdout:
                return "running"
            elif "job_state = E" in stdout:
                return "failed"
            # Handle short format output (qstat)
            elif " R " in stdout:
                return "running"
            elif " Q " in stdout:
                return "queued"
            elif " C " in stdout:
                return "completed"
            elif " E " in stdout:
                return "failed"
            else:
                return "unknown"
        except Exception:
            return "unknown"

    def _get_k8s_result(self, job_id: str) -> Any:
        """Get result from Kubernetes job logs."""
        try:
            from kubernetes import client

            core_api = client.CoreV1Api()

            # Get pods for this job
            pods = core_api.list_namespaced_pod(
                namespace=self.config.k8s_namespace,
                label_selector=f"job-name={job_id}",
            )

            for pod in pods.items:
                if pod.status.phase == "Succeeded":
                    # Get pod logs
                    logs = core_api.read_namespaced_pod_log(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                    )

                    # Parse result from logs
                    for line in logs.split("\n"):
                        if line.startswith("CLUSTRIX_RESULT:"):
                            result_str = line[len("CLUSTRIX_RESULT:") :]
                            # Try to evaluate the result
                            try:
                                import ast

                                return ast.literal_eval(result_str)
                            except Exception:
                                # If literal_eval fails, return as string
                                return result_str

                    # If no CLUSTRIX_RESULT found, return logs
                    return logs

            raise RuntimeError(f"No successful pod found for job {job_id}")

        except Exception as e:
            raise RuntimeError(f"Failed to get Kubernetes job result: {e}")

    def _get_k8s_error_log(self, job_id: str) -> str:
        """Get error log from Kubernetes job."""
        try:
            from kubernetes import client

            core_api = client.CoreV1Api()

            # Get pods for this job
            pods = core_api.list_namespaced_pod(
                namespace=self.config.k8s_namespace,
                label_selector=f"job-name={job_id}",
            )

            error_logs = []
            for pod in pods.items:
                # Get pod logs regardless of status
                try:
                    logs = core_api.read_namespaced_pod_log(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                    )
                    error_logs.append(f"Pod {pod.metadata.name}:\n{logs}")
                except Exception as e:
                    error_logs.append(
                        f"Pod {pod.metadata.name}: Failed to get logs - {e}"
                    )

            return "\n\n".join(error_logs) if error_logs else "No error logs available"

        except Exception as e:
            return f"Failed to get Kubernetes error logs: {e}"

    def _extract_k8s_exception(self, job_id: str) -> Exception:
        """Extract original exception from Kubernetes job logs."""
        try:
            error_log = self._get_k8s_error_log(job_id)

            # Look for CLUSTRIX_ERROR and CLUSTRIX_TRACEBACK in logs
            lines = error_log.split("\n")
            error_msg = None
            traceback_found = False

            for line in lines:
                if line.startswith("CLUSTRIX_ERROR:"):
                    error_msg = line[len("CLUSTRIX_ERROR:") :]
                elif line.startswith("CLUSTRIX_TRACEBACK:"):
                    traceback_found = True
                    break

            if error_msg:
                # Try to recreate the original exception
                return RuntimeError(error_msg)

            return None

        except Exception:
            return None

    def _cleanup_k8s_job(self, job_id: str):
        """Clean up Kubernetes job resources."""
        try:
            from kubernetes import client

            batch_api = client.BatchV1Api()
            core_api = client.CoreV1Api()

            # Delete the job (this will also delete associated pods)
            batch_api.delete_namespaced_job(
                name=job_id,
                namespace=self.config.k8s_namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground"),
            )

        except Exception as e:
            # Log warning but don't fail
            import logging

            logging.warning(f"Failed to cleanup Kubernetes job {job_id}: {e}")

    def get_job_status(self, job_id: str) -> str:
        """Get job status (alias for _check_job_status)."""
        return self._check_job_status(job_id)

    def get_result(self, job_id: str) -> Any:
        """Get result (alias for wait_for_result)."""
        return self.wait_for_result(job_id)

    def __del__(self):
        """Cleanup resources."""
        self.disconnect()
