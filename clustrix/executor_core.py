"""Core ClusterExecutor class that coordinates all execution types.

This module provides the main ClusterExecutor class that acts as a coordinator
for the supported job execution backends: local, ssh, slurm and huggingface.
"""

import shlex
import time
import tempfile
import dill
import pickle
import logging
from typing import Any, Dict, Optional

import cloudpickle

from .config import validate_cluster_type
from .executor_connections import ConnectionManager
from .executor_schedulers import SchedulerManager
from .hf_jobs import HFJobsManager
from .local_executor import LocalJobManager
from .utils import verify_signed_payload

logger = logging.getLogger(__name__)


class ClusterExecutor:
    """Handles execution of jobs on various cluster types.

    Use it as a context manager wherever the connection matters::

        with ClusterExecutor(config) as executor:
            job_id = executor.submit_job(func_data, job_config)
            result = executor.wait_for_result(job_id)

    On the way out -- including out of an exception -- the SSH transport and
    any SFTP channel are closed. ``__del__`` still calls ``disconnect()`` as a
    backstop, but a finaliser runs at an interpreter-defined time or not at
    all, so it is not a substitute for the ``with``.
    """

    def __init__(self, config):
        """Initialize the cluster executor.

        Args:
            config: ClusterConfig instance with execution settings
        """
        self.config = config

        # Initialize sub-managers
        self.connection_manager = ConnectionManager(config)
        self.scheduler_manager = SchedulerManager(config, self.connection_manager)
        self.hf_jobs_manager = HFJobsManager(config)
        self.local_manager = LocalJobManager(config)

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
        # "local" runs the function on this machine. It is advertised in the
        # widget's cluster-type dropdown and in the docs, but had no branch
        # here and raised "Unsupported cluster type: local" (#120). Like
        # huggingface below, it must not fall through to connect(): there is
        # no host to SSH into.
        if self.config.cluster_type == "local":
            job_id = self.local_manager.submit_job(func_data, job_config)
            self.active_jobs[job_id] = {"manager": "local", "job_id": job_id}
            return job_id

        # HuggingFace Jobs talks to an HTTP API, not a host: there is nothing
        # to SSH into, and calling connect() here would fail on a config that
        # is perfectly valid for this backend.
        if self.config.cluster_type == "huggingface":
            job_id = self.hf_jobs_manager.submit_job(func_data, job_config)
            self.active_jobs[job_id] = {"manager": "huggingface", "job_id": job_id}
            return job_id

        # Checked before connect(): a cluster type this executor cannot
        # dispatch used to fail *after* an SSH round trip to a host that was
        # never going to be used.
        validate_cluster_type(self.config.cluster_type)

        # Ensure connection is established for traditional cluster types
        self.connect()

        if self.config.cluster_type == "slurm":
            job_id = self.scheduler_manager.submit_slurm_job(func_data, job_config)
            self.active_jobs[job_id] = {"manager": "scheduler", "job_id": job_id}
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

            if manager_type == "huggingface":
                result = self.hf_jobs_manager.wait_for_result(job_id)
                del self.active_jobs[job_id]
                return result
            elif manager_type == "local":
                result = self.local_manager.wait_for_result(job_id)
                del self.active_jobs[job_id]
                return result
            elif manager_type == "scheduler":
                # For scheduler jobs, delegate to wait_for_scheduler_result
                result = self._wait_for_scheduler_result(job_id)
                del self.active_jobs[job_id]
                return result

        # Fallback for jobs not in our tracking
        # This handles backward compatibility
        if job_id.startswith("local_"):
            return self.local_manager.wait_for_result(job_id)
        return self._wait_for_scheduler_result(job_id)

    def _verify_result_signature(
        self, job_id: str, remote_dir: str, payload: bytes
    ) -> None:
        """Check result.pkl against the key this job was given, before loading.

        Unpickling executes arbitrary code, so a result file is not something
        to open on trust (#121). At submission the job directory is created
        0700 with a random key inside it; the job tags its result with an
        HMAC over the exact bytes it wrote, and this refuses anything that
        does not match.

        This bounds the trust to whoever can already read the job directory.
        It is not a defence against a wholly compromised remote host, which
        runs the function anyway -- but it does stop an unrelated user on a
        shared filesystem, a stale file from an earlier run, or a truncated
        transfer from being handed to the unpickler.

        A job with no recorded key is refused rather than loaded with a
        warning. "No key" and "forged" look identical from here, and the
        warning branch meant anyone who could get the key forgotten -- an
        adopted job id, a cleared table -- got an unverified pickle loaded.
        """
        job_info = self.scheduler_manager.active_jobs.get(job_id) or {}
        key = job_info.get("result_key")

        tag = ""
        if key:
            try:
                stdout, _ = self.connection_manager.execute_remote_command(
                    f"cat {shlex.quote(f'{remote_dir}/result.pkl.hmac')} 2>/dev/null"
                )
            except Exception as e:  # pragma: no cover - defensive
                raise RuntimeError(
                    f"Could not read the signature for job {job_id}: {e}"
                ) from e
            tag = stdout or ""

        verify_signed_payload(payload, tag, key, f"Job {job_id}")

    def _wait_for_scheduler_result(self, job_id: str) -> Any:
        """Wait for scheduler job result (SLURM/SSH)."""
        job_info = self.scheduler_manager.active_jobs.get(job_id)
        if not job_info:
            raise ValueError(f"Unknown job ID: {job_id}")

        remote_dir = job_info["remote_dir"]

        # Poll for completion, under a deadline. An unbounded `while True`
        # here meant a job that never reached a terminal state -- held by the
        # scheduler, stuck behind a queue that never cleared -- hung the
        # caller with no way out but Ctrl-C, and no indication of why.
        timeout = getattr(self.config, "job_wait_timeout", None)
        deadline = None if timeout is None else time.monotonic() + timeout
        status = "unknown"

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
                        payload = f.read()

                    self._verify_result_signature(job_id, remote_dir, payload)
                    # dill, not stdlib pickle, because the worker wrote
                    # this with dill. Replaying dill's reconstruction opcodes
                    # through stdlib pickle builds a *fresh* class for anything
                    # defined in the caller's __main__, so a returned instance
                    # failed isinstance() against the very class that defined
                    # it. dill's loader reuses the existing one.
                    result = dill.loads(payload)

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

            if deadline is not None and time.monotonic() >= deadline:
                # The job is left alone deliberately: it may still be
                # queued, and cancelling someone's allocation because the
                # client got bored is not this function's decision. The
                # remote directory is named so the result can be collected
                # by hand.
                raise TimeoutError(
                    f"Job {job_id} did not finish within "
                    f"{timeout}s (config.job_wait_timeout). Its last known "
                    f"status was {status!r}. The job has NOT been cancelled; "
                    f"its files are at {remote_dir} on the cluster. Raise "
                    f"job_wait_timeout, or set it to None to wait "
                    f"indefinitely."
                )

            # Wait before next poll
            time.sleep(self.config.job_poll_interval)

    def get_job_status(self, job_id: str) -> str:
        """Get job status (alias for _check_job_status)."""
        # Check if this is a tracked job and delegate to appropriate manager
        if job_id in self.active_jobs:
            manager_type = self.active_jobs[job_id]["manager"]

            if manager_type == "huggingface":
                return self.hf_jobs_manager.get_job_status(job_id)
            elif manager_type == "local":
                return self.local_manager.get_job_status(job_id)
            elif manager_type == "scheduler":
                return self.scheduler_manager.check_job_status(job_id)

        # Fallback for untracked jobs
        if job_id.startswith("local_"):
            return self.local_manager.get_job_status(job_id)
        return self.scheduler_manager.check_job_status(job_id)

    def get_result(self, job_id: str) -> Any:
        """Get result (alias for wait_for_result)."""
        return self.wait_for_result(job_id)

    def cancel_job(self, job_id: str):
        """Cancel a running job."""
        # Check if this is a tracked job and delegate to appropriate manager
        if job_id in self.active_jobs:
            manager_type = self.active_jobs[job_id]["manager"]

            if manager_type == "local":
                self.local_manager.cancel_job(job_id)
                return
            if manager_type == "huggingface":
                if not self.hf_jobs_manager.cancel_job(job_id):
                    # Keep tracking it: an uncancelled job is still running
                    # and still billing, and forgetting it here would make it
                    # invisible.
                    raise RuntimeError(
                        f"Could not cancel HuggingFace Job {job_id}; it may "
                        "still be running. Check the Jobs page for your "
                        "namespace."
                    )
                del self.active_jobs[job_id]
                return
            if manager_type == "scheduler":
                self.scheduler_manager.cancel_job(job_id)
                del self.active_jobs[job_id]
                return

        # Fallback for untracked jobs
        if job_id.startswith("local_"):
            self.local_manager.cancel_job(job_id)
            return
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

    def __enter__(self) -> "ClusterExecutor":
        """Enter a scope whose exit closes the cluster connection.

        Nothing is connected here. ``submit_job`` connects on demand, and the
        backends that have no host to dial (``local``, ``huggingface``) must
        be usable under ``with`` too.
        """
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Close the connection, including when the body raised."""
        self.disconnect()

    def __del__(self):
        """Backstop for callers who did not use ``with``.

        ``__del__`` runs at an interpreter-defined time, or never, so this is
        not the teardown story -- ``with ClusterExecutor(config) as ex:`` is.
        It stays because a caller who forgets should still release the
        transport eventually rather than hold it until the process exits.

        The swallow is deliberate and is the one place it is right: a finaliser
        can run while modules are already being torn down, an exception raised
        from it is printed and discarded by the interpreter anyway, and there
        is no caller left to give a correct or incorrect answer to.
        """
        try:
            self.disconnect()
        except Exception:  # pragma: no cover - interpreter shutdown only
            pass

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

    def _setup_ssh_connection(self):
        """Backward compatibility method."""
        return self.connection_manager.setup_ssh_connection()

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
            elif manager_type == "local":
                return self.local_manager.get_error_log(job_id)

        # Fallback for untracked jobs
        if job_id.startswith("local_"):
            return self.local_manager.get_error_log(job_id)
        return self.scheduler_manager.get_error_log(job_id)

    def _extract_original_exception(self, job_id: str) -> Optional[Exception]:
        """Backward compatibility method."""
        # Check manager type for this job
        if job_id in self.active_jobs:
            manager_type = self.active_jobs[job_id]["manager"]
            if manager_type == "scheduler":
                return self.scheduler_manager.extract_original_exception(job_id)

        # Fallback for untracked jobs
        return self.scheduler_manager.extract_original_exception(job_id)

    def _submit_slurm_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Backward compatibility method."""
        return self.scheduler_manager.submit_slurm_job(func_data, job_config)

    def _check_slurm_status(self, job_id: str) -> str:
        """Backward compatibility method."""
        return self.scheduler_manager.status_manager._check_slurm_job_status_robust(
            job_id, self.scheduler_manager.active_jobs
        )
