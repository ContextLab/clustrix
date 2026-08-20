"""Scheduler job status monitoring and error handling.

This module handles status checking and error retrieval for SLURM jobs and
for jobs run directly over SSH.
"""

import os
import shlex
import time
import tempfile
import dill

import logging
from typing import Dict, Any, Optional

from .utils import verify_signed_payload

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

            >>> # SSH job failed
            >>> status = scheduler.check_job_status("ssh_1234567890", active_jobs)
            >>> print(status)  # "failed"

        Note:
            This method is called repeatedly by `wait_for_result()` during job polling.
            The implementation handles scheduler-specific quirks and provides robust
            status detection even when jobs are removed from scheduler queues.
        """

        if self.config.cluster_type == "slurm":
            if self.connection_manager.ssh_client is None:
                # No live SSH connection: we cannot query the scheduler, so
                # we genuinely do not know the job's status. (Querying
                # anyway would just raise "SSH client not connected" inside
                # execute_remote_command and get swallowed a few frames
                # down -- reporting "unknown" here directly is the honest,
                # immediate version of that same outcome.)
                return "unknown"
            return self._check_slurm_job_status_robust(job_id, active_jobs)

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
                    except Exception as exc:
                        # This used to answer "running". It is not: the job
                        # wrote an error file, and the only thing that failed
                        # is our attempt to measure it. Reporting "running"
                        # sent wait_for_result back around the poll loop until
                        # job_wait_timeout expired, and the TimeoutError then
                        # blamed a job that had already stopped. "unknown" is
                        # the honest answer and is already a documented member
                        # of this function's return set.
                        logger.warning(
                            "Job %s: could not measure %s/job.err (%s). Job "
                            "status is unknown -- it is NOT known to be "
                            "running.",
                            job_id,
                            job_info["remote_dir"],
                            exc,
                        )
                        return "unknown"
                    if line_count > 0:
                        return "failed"
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

        # Job not in the queue. That does NOT mean it finished: squeue does not
        # list a job for a short window right after sbatch accepts it, and it
        # stops listing a job the moment it finishes. Asking the accounting
        # database first distinguishes "not started yet" and "still running"
        # from "gone", which the file probe below cannot do -- it would poll
        # for about fifteen seconds, find no result.pkl, and report the job
        # unknown while it was in fact still queued.
        sacct_info = self._query_sacct(job_id)
        if sacct_info:
            state = sacct_info["state"]
            if state.startswith(("PENDING", "REQUEUED", "RESIZING", "SUSPENDED")):
                return "queued"
            if state.startswith(("RUNNING", "CONFIGURING", "COMPLETING")):
                return "running"
            if any(state.startswith(f) for f in self._TERMINAL_FAILURE_STATES):
                reason = self._get_scheduler_failure_reason(job_id)
                logger.error(f"Job {job_id} {reason or 'failed'}")
                return "failed"
            # COMPLETED, or a state we do not recognise: fall through to the
            # file probe, which distinguishes a real result from a job that
            # exited zero without producing one.

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
                        except Exception as exc:
                            # Log and continue: the remaining files, and the
                            # accounting query below, can still produce a
                            # correct verdict, so one unreadable file is not
                            # fatal. But it is not nothing either -- the file
                            # that was skipped may have been the one holding
                            # the traceback, so name it rather than letting
                            # the scan look exhaustive when it was not.
                            logger.warning(
                                "Job %s: could not scan %s for a traceback "
                                "(%s); skipping that file.",
                                job_id,
                                filename,
                                exc,
                            )
            else:
                logger.warning(f"Job {job_id} directory is empty or doesn't exist")
        except Exception as e:
            logger.error(f"Could not list job {job_id} directory: {e}")

        # Last resort: ask the scheduler itself what happened. A job that leaves
        # no result.pkl, no error.pkl and no output files never got far enough to
        # write them -- typically the working directory was not visible from the
        # compute node, so the job script died before its first redirect. The
        # accounting database is the only place that still knows, and reporting
        # "unknown" instead of its verdict is what makes this failure so
        # expensive to diagnose.
        accounting = self._get_scheduler_failure_reason(job_id)
        if accounting:
            logger.error(f"Job {job_id} {accounting}")
            return "failed"

        return "unknown"

    # Scheduler states that mean the job is over and did not succeed.
    _TERMINAL_FAILURE_STATES = (
        "FAILED",
        "CANCELLED",
        "TIMEOUT",
        "OUT_OF_MEMORY",
        "NODE_FAIL",
        "PREEMPTED",
        "BOOT_FAIL",
        "DEADLINE",
    )

    def _query_sacct(self, job_id: str) -> Optional[Dict[str, str]]:
        """Ask SLURM's accounting database about a job.

        Returns a dict with ``state``, ``exit_code``, ``nodelist`` and
        ``workdir``, or None when the cluster is not SLURM, sacct is
        unavailable, or it has no record of the job.
        """
        if self.config.cluster_type != "slurm":
            return None

        # WorkDir is only useful for the exit-127 diagnostic and is not a
        # supported field on older SLURM, where asking for it makes the whole
        # query fail. Fall back to the fields every version has rather than
        # losing the state as well.
        field_sets = ("State,ExitCode,NodeList,WorkDir", "State,ExitCode,NodeList")
        line = ""
        for fields in field_sets:
            cmd = (
                f"sacct -j {job_id} --format={fields} "
                f"--parsable2 --noheader 2>/dev/null | head -1"
            )
            try:
                stdout, _ = self.connection_manager.execute_remote_command(cmd)
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Could not query sacct for job {job_id}: {e}")
                return None
            line = (stdout or "").strip()
            if line:
                break

        if not line:
            return None

        parts = line.split("|")
        return {
            "state": parts[0].strip() if parts else "",
            "exit_code": parts[1].strip() if len(parts) > 1 else "?",
            "nodelist": parts[2].strip() if len(parts) > 2 else "?",
            "workdir": parts[3].strip() if len(parts) > 3 else "?",
        }

    def _get_scheduler_failure_reason(self, job_id: str) -> Optional[str]:
        """Ask the scheduler's accounting database why a job produced nothing.

        Returns a human-readable explanation, or None if the scheduler has no
        record of a failure (or cannot be reached).
        """
        info = self._query_sacct(job_id)
        if not info:
            return None

        state = info["state"]
        if not any(state.startswith(s) for s in self._TERMINAL_FAILURE_STATES):
            return None

        detail = (
            f"failed according to sacct: state={state} "
            f"exit_code={info['exit_code']} node={info['nodelist']} "
            f"workdir={info['workdir']}"
        )
        if info["exit_code"].startswith("127"):
            detail += (
                ". Exit code 127 means the job script could not find a command it "
                "tried to run. The usual cause is that remote_work_dir points at "
                "storage the compute node cannot see -- /tmp is node-local on most "
                "clusters, so an environment built on the login node is absent at "
                "run time. Set remote_work_dir to a shared filesystem path."
            )
        return detail

    def _authenticated_error_payload(
        self, job_id: str, job_info: Dict[str, Any]
    ) -> Optional[bytes]:
        """Return the bytes of ``error.pkl``, or None if the job wrote none.

        ``result.pkl`` was verified before ``dill.loads`` and ``error.pkl``
        was not, which made failing the job a complete bypass of the check: a
        hostile or compromised cluster only had to exit non-zero and leave a
        pickle whose ``__reduce__`` calls ``os.system``, and it ran on the
        submitting machine. Both files are deserialized here, so both clear
        the same bar and are signed with the same per-job key.

        Raises:
            PayloadAuthenticationError: the payload exists but is unsigned,
                badly signed, or has no key to check against. Callers must
                let this out rather than falling back to text logs -- the
                whole point is that the user is told.
        """
        remote_dir = job_info["remote_dir"]
        error_pkl_path = f"{remote_dir}/error.pkl"
        if not self.connection_manager.remote_file_exists(error_pkl_path):
            return None

        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as handle:
            local_error_path = handle.name
        try:
            self.connection_manager.download_file(error_pkl_path, local_error_path)
            with open(local_error_path, "rb") as handle:
                payload = handle.read()
        finally:
            if os.path.exists(local_error_path):
                os.unlink(local_error_path)

        key = job_info.get("result_key")
        tag = ""
        if key:
            quoted = shlex.quote(f"{remote_dir}/error.pkl.hmac")
            stdout, _ = self.connection_manager.execute_remote_command(
                f"cat {quoted} 2>/dev/null"
            )
            tag = stdout or ""

        verify_signed_payload(payload, tag, key, f"Job {job_id} error report")
        return payload

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

        # First, try to get pickled error data. Authentication happens outside
        # the try: a failed check is not "error.pkl could not be read", it is
        # a refusal, and swallowing it into the text-log fallback would hide
        # exactly the forgery the check exists to catch.
        payload = self._authenticated_error_payload(job_id, job_info)
        if payload is not None:
            try:
                # dill: matches how the stages write it, and keeps
                # a custom exception class bound to the caller's own. Safe to
                # deserialize only because the bytes just verified against the
                # per-job key.
                error_data = dill.loads(payload)

                # Handle different error data formats
                if isinstance(error_data, dict):
                    error_msg = error_data.get("error", str(error_data))
                    traceback_info = error_data.get("traceback", "")
                    return f"{error_msg}\n\nTraceback:\n{traceback_info}"
                else:
                    return str(error_data)
            except Exception:
                # Authenticated but unreadable -- a truncated transfer, or a
                # class this interpreter lacks. Fall through to the text logs.
                logger.warning(
                    "Job %s error.pkl verified but could not be deserialized; "
                    "falling back to text logs.",
                    job_id,
                )

        # Fallback to text error files
        error_files = ["job.err", "slurm-*.out"]

        unreadable = []
        for error_file in error_files:
            try:
                # remote_dir is quoted (it comes from config.remote_work_dir);
                # error_file stays outside the quotes because it is a glob.
                stdout, _ = self.connection_manager.execute_remote_command(
                    f"cat {shlex.quote(remote_dir)}/{error_file} 2>/dev/null"
                )
                if stdout.strip():
                    return stdout
            except Exception as exc:
                logger.warning(
                    "Job %s: could not read %s/%s (%s).",
                    job_id,
                    remote_dir,
                    error_file,
                    exc,
                )
                unreadable.append(f"{error_file} ({exc})")

        if unreadable:
            # "No error log found" is a statement about the cluster: there was
            # nothing to read. Saying it when the read itself failed reports
            # "I could not tell" as "no", and this string is what the user is
            # shown as the reason their job died.
            return (
                "Could not read the error log for job "
                f"{job_id}: {'; '.join(unreadable)}. The job may well have "
                "written one -- this is a failure to retrieve it, not "
                "evidence that it is absent."
            )
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

        # Authentication is outside the try for the same reason as in
        # get_error_log: a refusal must reach the user, not be turned into
        # "no exception could be extracted".
        payload = self._authenticated_error_payload(job_id, job_info)
        if payload is not None:
            try:
                # dill: matches how the stages write it, and keeps
                # a custom exception class bound to the caller's own. Safe to
                # deserialize only because the bytes just verified.
                error_data = dill.loads(payload)

                # Return the exception object if it is one
                if isinstance(error_data, Exception):
                    return error_data
                elif isinstance(error_data, dict):
                    # The remote stages ship the exception object itself under
                    # 'exception'. Prefer it: rebuilding from the message alone
                    # loses the type, so `except ValueError` would not fire.
                    original = error_data.get("exception")
                    if isinstance(original, Exception):
                        return original
                    if "error" in error_data:
                        return RuntimeError(error_data["error"])

            except Exception:
                # Authenticated but unreadable; the caller falls back to the
                # text error log.
                logger.warning(
                    "Job %s error.pkl verified but could not be deserialized.",
                    job_id,
                )

        return None
