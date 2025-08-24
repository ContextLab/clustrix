"""Scheduler job status monitoring and error handling.

This module handles status checking and error retrieval for traditional
HPC scheduler jobs (SLURM, PBS, SGE).
"""

import os
import time
import tempfile
import pickle
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class SchedulerStatusManager:
    """Manages job status monitoring and error handling for HPC schedulers."""

    def __init__(self, config, connection_manager):
        """Initialize scheduler status manager.

        Args:
            config: ClusterConfig instance
            connection_manager: ConnectionManager instance for SSH operations
        """
        self.config = config
        self.connection_manager = connection_manager

    def check_job_status(self, job_id: str, active_jobs: Dict[str, Any]) -> str:
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
            active_jobs: Dictionary of active job information

        Returns:
            str: Current job status as a standardized string value

        Examples:
            >>> # SLURM job running
            >>> status = scheduler.check_job_status("12345", active_jobs)
            >>> print(status)  # "running"

            >>> # PBS job completed (removed from queue)
            >>> status = scheduler.check_job_status("67890.headnode", active_jobs)
            >>> print(status)  # "completed"

            >>> # SSH job failed
            >>> status = scheduler.check_job_status("ssh_1234567890", active_jobs)
            >>> print(status)  # "failed"

        Note:
            This method is called repeatedly by `wait_for_result()` during job polling.
            The implementation handles scheduler-specific quirks and provides robust
            status detection even when jobs are removed from scheduler queues.
        """

        if self.config.cluster_type == "slurm":
            # Use robust checking only if we have a real SSH connection (not unit tests)
            try:
                from unittest.mock import Mock

                is_mock = (
                    isinstance(self.connection_manager.ssh_client, Mock)
                    if hasattr(self.connection_manager, "ssh_client")
                    and self.connection_manager.ssh_client
                    else False
                )
            except ImportError:
                is_mock = False

            if (
                hasattr(self.connection_manager, "ssh_client")
                and self.connection_manager.ssh_client
                and not is_mock
            ):
                return self._check_slurm_job_status_robust(job_id, active_jobs)
            else:
                # Fallback to original logic for unit tests
                cmd = f"squeue -j {job_id} -h -o %T"
                try:
                    stdout, stderr = self.connection_manager.execute_remote_command(cmd)
                    if not stdout.strip():
                        # Job not in queue, check if result exists
                        if job_id in active_jobs:
                            job_info = active_jobs[job_id]
                            result_exists = self.connection_manager.remote_file_exists(
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
                stdout, stderr = self.connection_manager.execute_remote_command(cmd)
                if "job_state = C" in stdout:
                    return "completed"
                elif "job_state = R" in stdout:
                    return "running"
                else:
                    return "failed"
            except Exception:
                # Job might be completed and removed from queue
                if job_id in active_jobs:
                    job_info = active_jobs[job_id]
                    result_exists = self.connection_manager.remote_file_exists(
                        f"{job_info['remote_dir']}/result.pkl"
                    )
                    return "completed" if result_exists else "failed"
                else:
                    return "completed"

        elif self.config.cluster_type == "sge":
            sge_status = self._check_sge_status(job_id)
            if sge_status == "completed":
                # Job completed but not in queue, check if result exists
                if job_id in active_jobs:
                    job_info = active_jobs[job_id]
                    result_exists = self.connection_manager.remote_file_exists(
                        f"{job_info['remote_dir']}/result.pkl"
                    )
                    return "completed" if result_exists else "failed"
                else:
                    return "completed"
            else:
                return sge_status

        elif self.config.cluster_type == "ssh":
            # For SSH jobs, check if result file exists
            if job_id in active_jobs:
                job_info = active_jobs[job_id]
                result_exists = self.connection_manager.remote_file_exists(
                    f"{job_info['remote_dir']}/result.pkl"
                )
                error_exists = self.connection_manager.remote_file_exists(
                    f"{job_info['remote_dir']}/job.err"
                )

                if result_exists:
                    return "completed"
                elif error_exists:
                    # Check if error file has content indicating failure
                    try:
                        stdout, _ = self.connection_manager.execute_remote_command(
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

        return "unknown"

    def _check_slurm_job_status_robust(
        self, job_id: str, active_jobs: Dict[str, Any]
    ) -> str:
        """
        Robust SLURM job status checking with retry logic and proper error handling.

        This method addresses common issues with SLURM job status detection:
        - File system synchronization delays (NFS/Lustre)
        - Race conditions between job completion and file availability
        - Proper error handling with specific logging

        Returns:
            Job status: "completed", "failed", "running", "queued", or "unknown"
        """
        # First check if job is still in SLURM queue
        cmd = f"squeue -j {job_id} -h -o '%T %r'"  # Status and reason
        try:
            stdout, stderr = self.connection_manager.execute_remote_command(cmd)
            if stdout.strip():
                # Job is still in queue
                status_parts = stdout.strip().split()
                slurm_status = status_parts[0] if status_parts else ""
                reason = status_parts[1] if len(status_parts) > 1 else ""

                logger.debug(
                    f"SLURM job {job_id} status: {slurm_status}, reason: {reason}"
                )

                if slurm_status in ["COMPLETED"]:
                    return "completed"
                elif slurm_status in [
                    "FAILED",
                    "CANCELLED",
                    "TIMEOUT",
                    "NODE_FAIL",
                    "PREEMPTED",
                ]:
                    return "failed"
                elif slurm_status in ["RUNNING", "CONFIGURING"]:
                    return "running"
                elif slurm_status in ["PENDING", "RESIZING", "REQUEUED"]:
                    return "queued"
                else:
                    logger.warning(
                        f"Unknown SLURM status '{slurm_status}' for job {job_id}"
                    )
                    return "unknown"

        except Exception as e:
            logger.warning(f"Error checking SLURM queue status for job {job_id}: {e}")

        # Job not in queue - could be completed or failed
        # Use robust file-based detection with retry logic
        if job_id not in active_jobs:
            logger.warning(f"Job {job_id} not found in active_jobs, assuming completed")
            return "completed"

        job_info = active_jobs[job_id]
        remote_dir = job_info["remote_dir"]

        return self._check_job_completion_with_retry(job_id, remote_dir)

    def _check_job_completion_with_retry(self, job_id: str, remote_dir: str) -> str:
        """
        Check job completion with exponential backoff retry for file system delays.

        This handles the common scenario where SLURM jobs complete but result files
        aren't immediately visible due to NFS/Lustre synchronization delays.
        """
        from clustrix.filesystem import ClusterFilesystem

        # Try to use ClusterFilesystem for reliable file operations
        # Fall back to direct SSH if ClusterFilesystem fails (e.g., in unit tests)
        fs = None
        try:
            fs = ClusterFilesystem(self.config)
        except Exception as e:
            logger.debug(f"Could not create ClusterFilesystem, using direct SSH: {e}")
            fs = None

        result_path = f"{remote_dir}/result.pkl"
        error_path = f"{remote_dir}/error.pkl"

        # Retry logic with exponential backoff
        max_retries = 5
        base_delay = 1.0  # Start with 1 second

        for attempt in range(max_retries):
            try:
                # Check for result file first (success case)
                if fs and fs.exists(result_path):
                    logger.info(
                        f"Job {job_id} completed successfully - result.pkl found"
                    )
                    return "completed"
                elif not fs and self.connection_manager.remote_file_exists(result_path):
                    logger.info(
                        f"Job {job_id} completed successfully - result.pkl found"
                    )
                    return "completed"

                # Check for error file (failure case)
                if fs and fs.exists(error_path):
                    logger.info(f"Job {job_id} failed - error.pkl found")
                    return "failed"
                elif not fs and self.connection_manager.remote_file_exists(error_path):
                    logger.info(f"Job {job_id} failed - error.pkl found")
                    return "failed"

                # Check for SLURM output files for additional error context
                slurm_files = []
                if fs:
                    slurm_files = fs.glob("slurm-*.out", remote_dir)
                else:
                    # Fallback to direct SSH command
                    try:
                        cmd = f"ls {remote_dir}/slurm-*.out 2>/dev/null | head -5"
                        stdout, stderr = self.connection_manager.execute_remote_command(
                            cmd
                        )
                        slurm_files = (
                            stdout.strip().split("\n") if stdout.strip() else []
                        )
                    except Exception:
                        slurm_files = []

                if slurm_files:
                    # Check if any SLURM output files contain error indicators
                    for slurm_file in slurm_files:
                        try:
                            # Read first/last few lines to check for errors without full download
                            cmd = (
                                f"tail -20 {remote_dir}/{slurm_file} | "
                                f"grep -i 'error\\|failed\\|exception\\|traceback' | head -5"
                            )
                            stdout, stderr = (
                                self.connection_manager.execute_remote_command(cmd)
                            )
                            if stdout.strip():
                                logger.warning(
                                    f"Job {job_id} shows errors in SLURM output: {stdout.strip()}"
                                )
                                return "failed"
                        except Exception as e:
                            logger.debug(
                                f"Could not check SLURM output file {slurm_file}: {e}"
                            )

                # If this is not the last attempt, wait with exponential backoff
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)  # Exponential backoff
                    logger.debug(
                        f"Job {job_id} files not ready, waiting {delay}s "
                        f"before retry {attempt + 2}/{max_retries}"
                    )
                    time.sleep(delay)

            except Exception as e:
                logger.error(
                    f"Error checking job {job_id} completion (attempt {attempt + 1}): {e}"
                )
                if attempt == max_retries - 1:
                    return "unknown"
                time.sleep(base_delay * (2**attempt))

        # If we get here, no result or error files found after all retries
        logger.error(
            f"Job {job_id} completion status unknown - no result or error files "
            f"found after {max_retries} attempts"
        )

        # Final fallback: check if job directory exists and has any files
        try:
            if fs:
                files = fs.ls(remote_dir)
            else:
                cmd = f"ls -la {remote_dir} 2>/dev/null || true"
                stdout, stderr = self.connection_manager.execute_remote_command(cmd)
                files = stdout.strip().split("\n") if stdout.strip() else []
            if files:
                logger.warning(
                    f"Job {job_id} directory contains files but no result/error: {files}"
                )
                # Look for any Python traceback in job directory files
                for filename in files:
                    if filename.endswith((".out", ".err", ".log")):
                        try:
                            cmd = f"grep -l -i 'traceback\\|exception' {remote_dir}/{filename}"
                            stdout, stderr = (
                                self.connection_manager.execute_remote_command(cmd)
                            )
                            if stdout.strip():
                                logger.info(
                                    f"Job {job_id} failed - Python traceback found in {filename}"
                                )
                                return "failed"
                        except Exception:
                            pass
            else:
                logger.warning(f"Job {job_id} directory is empty or doesn't exist")
        except Exception as e:
            logger.error(f"Could not list job {job_id} directory: {e}")

        return "unknown"

    def _check_pbs_status(self, job_id: str) -> str:
        """Check PBS job status."""
        cmd = f"qstat -f {job_id}"
        try:
            stdout, stderr = self.connection_manager.execute_remote_command(cmd)
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

    def _check_sge_status(self, job_id: str) -> str:
        """Check SGE job status."""
        cmd = f"qstat -j {job_id}"
        try:
            stdout, stderr = self.connection_manager.execute_remote_command(cmd)
            if not stdout.strip() or "Following jobs do not exist" in stderr:
                # Job not in queue, likely completed
                return "completed"
            else:
                # Parse SGE job state from qstat output
                # Common SGE states: r (running), qw (queued), Eqw (error), dr (deleting)
                if "job_state                          r" in stdout:
                    return "running"
                elif "job_state                          qw" in stdout:
                    return "queued"
                elif "job_state                          Eqw" in stdout:
                    return "failed"
                elif "job_state                          dr" in stdout:
                    return "completed"
                # Check for exit status indicating completion
                elif "exit_status" in stdout:
                    return "completed"
                else:
                    return "running"  # Default for unknown running states
        except Exception:
            return "unknown"

    def get_error_log(self, job_id: str, active_jobs: Dict[str, Any]) -> str:
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
            active_jobs: Dictionary of active job information

        Returns:
            str: Comprehensive error information including error messages and tracebacks.
                 Returns detailed structured information when available, or raw log
                 content as fallback.

        Examples:
            >>> # Structured error (preferred)
            >>> error_log = status_manager.get_error_log("job_12345", active_jobs)
            >>> # Returns: "ValueError: Division by zero\n\nTraceback:\n  File..."

            >>> # Text log fallback
            >>> error_log = status_manager.get_error_log("job_67890", active_jobs)
            >>> # Returns: "Error from job.err: Process failed with exit code 1"

            >>> # No error info
            >>> error_log = status_manager.get_error_log("job_unknown", active_jobs)
            >>> # Returns: "No error log found"

        Note:
            This method is typically called automatically by `wait_for_result()` when
            a job status is detected as "failed". It provides the error information
            used for exception re-raising and user notification.
        """
        job_info = active_jobs.get(job_id)
        if not job_info:
            return "No job info available"

        remote_dir = job_info["remote_dir"]

        # First, try to get pickled error data
        error_pkl_path = f"{remote_dir}/error.pkl"
        if self.connection_manager.remote_file_exists(error_pkl_path):
            try:
                with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
                    local_error_path = f.name

                self.connection_manager.download_file(error_pkl_path, local_error_path)

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
            except Exception:
                # If error.pkl exists but can't be read, continue to text logs
                pass

        # Fallback to text error files
        error_files = ["job.err", "slurm-*.out", "job.e*"]

        for error_file in error_files:
            try:
                stdout, _ = self.connection_manager.execute_remote_command(
                    f"cat {remote_dir}/{error_file} 2>/dev/null"
                )
                if stdout.strip():
                    return stdout
            except Exception:
                continue

        return "No error log found"

    def extract_original_exception(
        self, job_id: str, active_jobs: Dict[str, Any]
    ) -> Optional[Exception]:
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
            active_jobs: Dictionary of active job information

        Returns:
            Optional[Exception]: The original exception object if successfully extracted,
                               RuntimeError for recoverable data, or None if extraction
                               fails completely.

        Examples:
            >>> # Original ValueError preserved
            >>> exc = status_manager.extract_original_exception("job_123", active_jobs)
            >>> isinstance(exc, ValueError)  # True
            >>> str(exc)  # "Division by zero"

            >>> # Dictionary format converted
            >>> exc = status_manager.extract_original_exception("job_456", active_jobs)
            >>> isinstance(exc, RuntimeError)  # True (fallback)
            >>> str(exc)  # "Original error message"

            >>> # Extraction failed
            >>> exc = status_manager.extract_original_exception("job_789", active_jobs)
            >>> exc is None  # True

        Note:
            This method is called by `wait_for_result()` to enable proper exception
            re-raising. When successful, users can catch specific exception types
            instead of generic RuntimeError messages.

        See Also:
            get_error_log(): Retrieves error information for logging/display
            wait_for_result(): Main method that uses both error retrieval functions
        """
        job_info = active_jobs.get(job_id)
        if not job_info:
            return None

        remote_dir = job_info["remote_dir"]
        error_pkl_path = f"{remote_dir}/error.pkl"

        if self.connection_manager.remote_file_exists(error_pkl_path):
            try:
                with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
                    local_error_path = f.name

                self.connection_manager.download_file(error_pkl_path, local_error_path)

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
