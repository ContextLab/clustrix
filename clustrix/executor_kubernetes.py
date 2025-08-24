"""Kubernetes-specific job execution operations.

This module handles Kubernetes job submission, monitoring, and result retrieval
using containerized Python execution.
"""

import time
import base64
import random
import logging
from typing import Dict, Any, Optional
import ast

import cloudpickle

logger = logging.getLogger(__name__)


class KubernetesJobManager:
    """Manages Kubernetes job execution using containerized Python runners."""

    def __init__(self, config, connection_manager):
        """Initialize Kubernetes job manager.

        Args:
            config: ClusterConfig instance with Kubernetes settings
            connection_manager: ConnectionManager instance for K8s client
        """
        self.config = config
        self.connection_manager = connection_manager
        self.active_jobs: Dict[str, Any] = {}

    def submit_k8s_job(
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
            >>> job_id = k8s_manager.submit_k8s_job(func_data, job_config)
            >>> print(job_id)  # "clustrix-job-1234567890"

        Note:
            - Requires kubernetes package: `pip install kubernetes`
            - Assumes kubectl is configured with cluster access
            - Jobs are created in the "default" namespace
            - Cloudpickle is used for function serialization
            - Results are captured via stdout parsing (CLUSTRIX_RESULT: prefix)
        """
        try:
            from kubernetes import client  # type: ignore
        except ImportError:
            raise ImportError(
                "kubernetes package required for Kubernetes support. "
                "Install with: pip install kubernetes"
            )

        # Ensure Kubernetes client is set up
        if (
            not hasattr(self.connection_manager, "k8s_client")
            or self.connection_manager.k8s_client is None
        ):
            self.connection_manager.setup_kubernetes()

        # Create a unique job name
        job_name = f"clustrix-job-{int(time.time())}-{random.randint(1000, 9999)}"

        # Serialize function data
        func_data_serialized = cloudpickle.dumps(func_data)
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
                                "command": ["/bin/bash", "-c"],
                                "args": [
                                    f"""
pip install cloudpickle dill --quiet && python -c "
import base64
import cloudpickle
import traceback
import pickle
import sys
import types

# Fix for Python 2/3 compatibility
import builtins
sys.modules['__builtin__'] = builtins

try:
    # Decode and deserialize function data
    func_data_b64 = '{func_data_b64}'
    func_data_bytes = base64.b64decode(func_data_b64)
    func_data = cloudpickle.loads(func_data_bytes)

    # Get components
    func_bytes = func_data['function']
    args_bytes = func_data['args']
    kwargs_bytes = func_data['kwargs']
    func_source = func_data.get('function_source')

    # Load arguments
    args = pickle.loads(args_bytes)
    kwargs = pickle.loads(kwargs_bytes)

    # Try to load function, with fallback for __main__ issues
    func = None
    try:
        func = cloudpickle.loads(func_bytes)
    except (AttributeError, ImportError) as e:
        if func_source and '__main__' in str(e):
            # Function was defined in __main__, try to recreate from source
            print(f'Recreating function from source due to __main__ issue')

            # Create a temporary module to execute the function in
            temp_module = types.ModuleType('temp_func_module')
            temp_module.__dict__.update(globals())

            # Clean the function source - remove decorators
            import re
            # Remove @cluster decorator lines (handle multi-line decorators)
            lines = func_source.split('\\n')
            cleaned_lines = []
            skip_until_def = False

            for line in lines:
                if line.strip().startswith('@cluster'):
                    skip_until_def = True
                    continue
                elif skip_until_def and line.strip().startswith(')'):
                    skip_until_def = True  # Keep skipping until we see def
                    continue
                elif skip_until_def and line.strip().startswith('def '):
                    skip_until_def = False
                    cleaned_lines.append(line)
                elif not skip_until_def:
                    cleaned_lines.append(line)

            cleaned_source = '\\n'.join(cleaned_lines)

            # Execute the cleaned function source in the temporary module
            exec(cleaned_source, temp_module.__dict__)

            # Extract the function (assume it's the first function defined)
            for name, obj in temp_module.__dict__.items():
                if callable(obj) and hasattr(obj, '__code__') and not name.startswith('_'):
                    func = obj
                    break

            if func is None:
                raise RuntimeError('Could not extract function from source code')
        else:
            # Re-raise the original error
            raise e

    if func is None:
        raise RuntimeError('Failed to load function')

    # Execute function
    result = func(*args, **kwargs)
    print(f'CLUSTRIX_RESULT:{{result}}')

except Exception as e:
    print(f'CLUSTRIX_ERROR:{{str(e)}}')
    print(f'CLUSTRIX_TRACEBACK:{{traceback.format_exc()}}')
    exit(1)
"
"""
                                ],
                                "resources": {
                                    "requests": {
                                        "cpu": f"{job_config.get('cores', 1)}",
                                        "memory": job_config.get("memory", "1Gi"),
                                    },
                                    "limits": {
                                        "cpu": f"{job_config.get('cores', 1)}",
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

    def check_k8s_job_status(self, job_id: str) -> str:
        """Check Kubernetes job status via API."""
        try:
            from kubernetes import client  # type: ignore

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

        except Exception:
            # Job might have been deleted or not found
            if job_id in self.active_jobs:
                # If we're tracking it but can't find it, consider it completed
                return "completed"
            else:
                return "unknown"

    def get_k8s_result(self, job_id: str) -> Any:
        """Get result from Kubernetes job logs."""
        try:
            from kubernetes import client  # type: ignore

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
                                return ast.literal_eval(result_str)
                            except Exception:
                                # If literal_eval fails, return as string
                                return result_str

                    # If no CLUSTRIX_RESULT found, return logs
                    return logs

            raise RuntimeError(f"No successful pod found for job {job_id}")

        except Exception as e:
            raise RuntimeError(f"Failed to get Kubernetes job result: {e}")

    def get_k8s_error_log(self, job_id: str) -> str:
        """Get error log from Kubernetes job."""
        try:
            from kubernetes import client  # type: ignore

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

    def extract_k8s_exception(self, job_id: str) -> Optional[Exception]:
        """Extract original exception from Kubernetes job logs."""
        try:
            error_log = self.get_k8s_error_log(job_id)

            # Look for CLUSTRIX_ERROR and CLUSTRIX_TRACEBACK in logs
            lines = error_log.split("\n")
            error_msg = None

            for line in lines:
                if line.startswith("CLUSTRIX_ERROR:"):
                    error_msg = line[len("CLUSTRIX_ERROR:") :]
                elif line.startswith("CLUSTRIX_TRACEBACK:"):
                    # Found traceback - could be used for more detailed error handling
                    break

            if error_msg:
                # Try to recreate the original exception
                return RuntimeError(error_msg)

            return None

        except Exception:
            return None

    def cleanup_k8s_job(self, job_id: str):
        """Clean up Kubernetes job resources."""
        try:
            from kubernetes import client  # type: ignore

            batch_api = client.BatchV1Api()

            # Delete the job (this will also delete associated pods)
            batch_api.delete_namespaced_job(
                name=job_id,
                namespace=self.config.k8s_namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground"),
            )

        except Exception as e:
            # Log warning but don't fail
            logger.warning(f"Failed to cleanup Kubernetes job {job_id}: {e}")

    def wait_for_k8s_result(self, job_id: str) -> Any:
        """Wait for Kubernetes job completion and return result."""
        job_info = self.active_jobs.get(job_id)
        if not job_info:
            raise ValueError(f"Unknown job ID: {job_id}")

        # Poll for completion
        while True:
            status = self.check_k8s_job_status(job_id)

            if status == "completed":
                # Get result from pod logs
                result = self.get_k8s_result(job_id)

                # Cleanup
                if self.config.cleanup_on_success:
                    self.cleanup_k8s_job(job_id)

                del self.active_jobs[job_id]
                return result

            elif status == "failed":
                # Get error from pod logs
                error_log = self.get_k8s_error_log(job_id)
                original_exception = self.extract_k8s_exception(job_id)

                if original_exception:
                    raise original_exception
                else:
                    raise RuntimeError(
                        f"Kubernetes job {job_id} failed. Error log:\n{error_log}"
                    )

            # Wait before next poll
            time.sleep(self.config.job_poll_interval)
