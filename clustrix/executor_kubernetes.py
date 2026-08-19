"""Kubernetes-specific job execution operations.

This module handles Kubernetes job submission, monitoring, and result retrieval
using containerized Python execution.
"""

import time
import base64
import hashlib
import hmac
import random
import secrets
import logging
from typing import Dict, Any, Optional

import cloudpickle
import dill
from .utils import normalize_memory

logger = logging.getLogger(__name__)

RESULT_PREFIX = "CLUSTRIX_RESULT_B64:"
SIGNATURE_PREFIX = "CLUSTRIX_RESULT_HMAC:"


def build_worker_program(func_data_b64: str) -> str:
    """The Python program the Kubernetes worker container runs.

    Kept separate from the Job manifest so it can be executed directly --
    ``python -c build_worker_program(...)`` runs the real worker on any
    machine, which is the only way to test this path without a cluster.

    The program writes its result as a base64 pickle plus an HMAC over those
    exact bytes, keyed by ``CLUSTRIX_RESULT_KEY`` from the environment. It
    used to ``print(f'CLUSTRIX_RESULT:{result}')`` -- the *repr* of the
    result -- which the caller then put through ``ast.literal_eval``. Anything
    without a literal repr (a numpy array, a dataclass, any object) came back
    as a string of its repr, silently, and the caller could not tell that from
    a real answer.
    """
    return f"""
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

    # Load arguments with dill: they may carry classes defined in the
    # caller's __main__, which stdlib pickle can only store by name.
    try:
        import dill as _argser
    except ImportError:
        _argser = cloudpickle
    args = _argser.loads(args_bytes)
    kwargs = _argser.loads(kwargs_bytes)

    # Try to load function, with fallback for __main__ issues
    func = None
    try:
        func = cloudpickle.loads(func_bytes)
    except (AttributeError, ImportError) as e:
        if func_source and '__main__' in str(e):
            # Function was defined in __main__, try to recreate from source
            print('Recreating function from source due to __main__ issue')

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

    # Serialize the result rather than printing its repr, and sign it so the
    # caller can tell our output apart from anything else in the pod log.
    import dill as _ser
    import hashlib as _hashlib
    import hmac as _hmac
    import os as _os
    _payload = _ser.dumps(result, protocol=4)
    _key = _os.environ.get('CLUSTRIX_RESULT_KEY', '')
    if not _key:
        raise RuntimeError('CLUSTRIX_RESULT_KEY is not set in this container')
    _tag = _hmac.new(_key.encode(), _payload, _hashlib.sha256).hexdigest()
    print('{RESULT_PREFIX}' + base64.b64encode(_payload).decode())
    print('{SIGNATURE_PREFIX}' + _tag)

except Exception as e:
    print('CLUSTRIX_ERROR:' + str(e))
    print('CLUSTRIX_TRACEBACK:' + traceback.format_exc())
    sys.exit(1)
"""


def build_container_command(worker_program: str) -> str:
    """Wrap the worker program in the shell command the container runs.

    The program is embedded inside a double-quoted shell string, so a ``"``,
    ``$`` or backtick in it would be eaten or expanded by the shell and the
    container would run something other than what was generated. Refuse
    rather than ship a mangled program.
    """
    for char in ('"', "$", "`"):
        if char in worker_program:
            raise ValueError(
                f"Worker program contains {char!r}, which the shell would "
                "reinterpret inside the container command."
            )
    return f"""
pip install cloudpickle dill --quiet && python -c "{worker_program}"
"""


def decode_signed_result(logs: str, result_key: str) -> Any:
    """Recover the result a worker container wrote into its pod log.

    Refuses anything it cannot verify. Unpickling executes code, so a payload
    that is missing, unsigned, or signed with the wrong key is an error --
    never a best-effort string, and never the raw log.
    """
    payload_b64 = None
    signature = None
    for line in logs.split("\n"):
        if line.startswith(RESULT_PREFIX):
            payload_b64 = line[len(RESULT_PREFIX) :].strip()
        elif line.startswith(SIGNATURE_PREFIX):
            signature = line[len(SIGNATURE_PREFIX) :].strip()

    if payload_b64 is None:
        raise RuntimeError(
            "The pod log contains no clustrix result. The job did not "
            "produce one, so there is nothing to return."
        )
    if not signature:
        raise RuntimeError(
            "The pod log contains a result with no signature. Refusing to "
            "deserialize it: loading a pickle executes code."
        )
    if not result_key:
        raise RuntimeError(
            "No result-signing key is known for this job, so its result "
            "cannot be verified. Refusing to deserialize it."
        )

    try:
        payload = base64.b64decode(payload_b64, validate=True)
    except Exception as e:
        raise RuntimeError(f"The result in the pod log is not valid base64: {e}")

    expected = hmac.new(result_key.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise RuntimeError(
            "The result in the pod log failed its integrity check. Refusing "
            "to deserialize it."
        )

    return dill.loads(payload)


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
                      - 'function': The pickled function to execute
                      - 'args': Pickled positional arguments
                      - 'kwargs': Pickled keyword arguments
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
            >>> from clustrix.utils import serialize_function
            >>> func_data = serialize_function(square, (5,), {})
            >>> job_config = {'cores': 2, 'memory': '4Gi'}
            >>> job_id = k8s_manager.submit_k8s_job(func_data, job_config)
            >>> print(job_id)  # "clustrix-job-1234567890"

        Note:
            - Requires kubernetes package: `pip install kubernetes`
            - Assumes kubectl is configured with cluster access
            - Jobs are created in the configured namespace
            - Cloudpickle is used for function serialization
            - The result is written to the pod log as a base64 pickle plus an
              HMAC over those bytes, keyed by a per-job secret passed to the
              container in CLUSTRIX_RESULT_KEY, and is verified before it is
              deserialized (see decode_signed_result)
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

        # Per-job key the worker signs its result with, so the caller can tell
        # the result apart from anything else that reaches the pod log.
        result_key = secrets.token_hex(32)

        container_command = build_container_command(build_worker_program(func_data_b64))

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
                                "env": [
                                    {
                                        "name": "CLUSTRIX_RESULT_KEY",
                                        "value": result_key,
                                    }
                                ],
                                "args": [container_command],
                                "resources": {
                                    # Kubernetes rejects "16GB" outright; its
                                    # quantities are "16G" or "16Gi". Passing
                                    # clustrix's configured spelling straight
                                    # through made default_memory unusable here.
                                    "requests": {
                                        "cpu": f"{job_config.get('cores', 1)}",
                                        "memory": normalize_memory(
                                            job_config.get("memory", "1Gi"),
                                            "kubernetes",
                                        ),
                                    },
                                    "limits": {
                                        "cpu": f"{job_config.get('cores', 1)}",
                                        "memory": normalize_memory(
                                            job_config.get("memory", "1Gi"),
                                            "kubernetes",
                                        ),
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
            "result_key": result_key,
        }

        return job_id

    def check_k8s_job_status(self, job_id: str) -> str:
        """Check Kubernetes job status via API.

        Never invents a status. This used to answer "completed" whenever the
        API call raised -- a job that had been evicted, a namespace the caller
        had lost access to, or a `kubernetes` package that was not installed
        all reported success, and the caller then went looking for a result
        that did not exist. An outcome we cannot read is an error, not a pass.
        """
        from kubernetes import client  # type: ignore

        batch_api = client.BatchV1Api()

        try:
            job = batch_api.read_namespaced_job(
                name=job_id, namespace=self.config.k8s_namespace
            )
        except Exception as e:
            raise RuntimeError(
                f"Could not read the status of Kubernetes job {job_id} in "
                f"namespace {self.config.k8s_namespace}: {e}. Its outcome is "
                "unknown -- it may still be running, or it may have been "
                "deleted before its result was collected."
            ) from e

        # Check job conditions
        if job.status.succeeded:
            return "completed"
        elif job.status.failed:
            return "failed"
        elif job.status.active:
            return "running"
        else:
            return "pending"

    def get_k8s_result(self, job_id: str) -> Any:
        """Get result from Kubernetes job logs.

        The pod log is verified against the key this job was given before
        anything is deserialized, and a log without a verifiable result is an
        error. Previously the log itself was returned as the "result" when no
        marker was found, and a marker that would not ``literal_eval`` came
        back as its own repr string.
        """
        from kubernetes import client  # type: ignore

        core_api = client.CoreV1Api()

        result_key = (self.active_jobs.get(job_id) or {}).get("result_key", "")

        try:
            pods = core_api.list_namespaced_pod(
                namespace=self.config.k8s_namespace,
                label_selector=f"job-name={job_id}",
            )
        except Exception as e:
            raise RuntimeError(
                f"Could not list the pods of Kubernetes job {job_id}: {e}"
            ) from e

        for pod in pods.items:
            if pod.status.phase == "Succeeded":
                try:
                    logs = core_api.read_namespaced_pod_log(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"Kubernetes job {job_id} succeeded but its log could "
                        f"not be read from pod {pod.metadata.name}: {e}"
                    ) from e

                return decode_signed_result(logs, result_key)

        raise RuntimeError(f"No successful pod found for job {job_id}")

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
