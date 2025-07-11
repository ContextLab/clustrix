"""
Comprehensive cluster job monitoring and validation framework.

This module provides tools for monitoring and validating real cluster job
submissions across different cluster types (SLURM, PBS, SGE, Kubernetes, SSH).
"""

import time
import os
import subprocess
import logging
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import json
import uuid
from datetime import datetime

from clustrix.config import ClusterConfig
from tests.real_world.credential_manager import get_credential_manager


class ClusterType(Enum):
    """Supported cluster types."""

    SLURM = "slurm"
    PBS = "pbs"
    SGE = "sge"
    KUBERNETES = "kubernetes"
    SSH = "ssh"


class JobStatus(Enum):
    """Job execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class JobMetrics:
    """Job execution metrics."""

    job_id: str
    cluster_type: ClusterType
    start_time: float
    end_time: Optional[float] = None
    duration: Optional[float] = None
    status: JobStatus = JobStatus.PENDING
    exit_code: Optional[int] = None
    memory_used_mb: Optional[float] = None
    cpu_time_seconds: Optional[float] = None
    queue_time_seconds: Optional[float] = None
    execution_time_seconds: Optional[float] = None
    error_message: Optional[str] = None
    stdout_lines: int = 0
    stderr_lines: int = 0
    function_name: Optional[str] = None
    function_args: Optional[str] = None
    cluster_node: Optional[str] = None
    cluster_queue: Optional[str] = None
    resource_requests: Dict[str, Any] = field(default_factory=dict)
    environment_info: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        """Calculate derived metrics."""
        if self.end_time and self.start_time:
            self.duration = self.end_time - self.start_time


@dataclass
class ValidationResult:
    """Result of job validation."""

    job_id: str
    success: bool
    message: str
    metrics: Optional[JobMetrics] = None
    validation_details: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class ClusterJobValidator:
    """Validator for cluster job submissions and execution."""

    def __init__(self, cluster_config: ClusterConfig):
        """Initialize validator with cluster configuration."""
        self.config = cluster_config
        self.cluster_type = ClusterType(cluster_config.cluster_type)
        self.logger = logging.getLogger(__name__)
        self.validation_results: List[ValidationResult] = []

    def validate_job_submission(
        self,
        job_id: str,
        function_name: str,
        function_args: str,
        resource_requests: Dict[str, Any],
    ) -> ValidationResult:
        """Validate that a job was submitted successfully."""
        try:
            # Check if job exists in cluster queue
            job_exists = self._check_job_exists(job_id)

            if not job_exists:
                return ValidationResult(
                    job_id=job_id,
                    success=False,
                    message=f"Job {job_id} not found in cluster queue",
                    errors=[
                        f"Job {job_id} not found in {self.cluster_type.value} queue"
                    ],
                )

            # Get job details
            job_details = self._get_job_details(job_id)

            # Validate resource requests
            resource_validation = self._validate_resource_requests(
                job_details, resource_requests
            )

            # Create metrics
            metrics = JobMetrics(
                job_id=job_id,
                cluster_type=self.cluster_type,
                start_time=time.time(),
                function_name=function_name,
                function_args=function_args,
                resource_requests=resource_requests,
                cluster_node=job_details.get("node"),
                cluster_queue=job_details.get("queue"),
                status=JobStatus.PENDING,
            )

            validation_details = {
                "job_details": job_details,
                "resource_validation": resource_validation,
                "submission_time": datetime.now().isoformat(),
            }

            result = ValidationResult(
                job_id=job_id,
                success=True,
                message=f"Job {job_id} submitted successfully",
                metrics=metrics,
                validation_details=validation_details,
            )

            if not resource_validation["all_valid"]:
                result.warnings.extend(resource_validation["warnings"])

            self.validation_results.append(result)
            return result

        except Exception as e:
            self.logger.error(f"Error validating job submission {job_id}: {e}")
            return ValidationResult(
                job_id=job_id,
                success=False,
                message=f"Error validating job submission: {e}",
                errors=[str(e)],
            )

    def monitor_job_execution(
        self, job_id: str, timeout_seconds: int = 300
    ) -> ValidationResult:
        """Monitor job execution until completion or timeout."""
        start_time = time.time()
        last_status = None
        status_changes = []

        try:
            while time.time() - start_time < timeout_seconds:
                # Get current job status
                current_status = self._get_job_status(job_id)

                if current_status != last_status:
                    status_changes.append(
                        {
                            "timestamp": time.time(),
                            "status": current_status.value,
                            "elapsed": time.time() - start_time,
                        }
                    )
                    last_status = current_status
                    self.logger.info(
                        f"Job {job_id} status changed to {current_status.value}"
                    )

                # Check if job is complete
                if current_status in [
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                ]:
                    break

                # Wait before next check
                time.sleep(5)

            # Get final job metrics
            final_metrics = self._get_job_metrics(job_id)
            final_metrics.start_time = start_time
            final_metrics.end_time = time.time()
            final_metrics.status = current_status

            # Determine if monitoring was successful
            if current_status == JobStatus.COMPLETED:
                success = True
                message = f"Job {job_id} completed successfully"
            elif current_status == JobStatus.FAILED:
                success = False
                message = f"Job {job_id} failed"
            elif time.time() - start_time >= timeout_seconds:
                success = False
                message = (
                    f"Job {job_id} monitoring timed out after {timeout_seconds} seconds"
                )
                current_status = JobStatus.TIMEOUT
            else:
                success = False
                message = f"Job {job_id} ended with status {current_status.value}"

            result = ValidationResult(
                job_id=job_id,
                success=success,
                message=message,
                metrics=final_metrics,
                validation_details={
                    "status_changes": status_changes,
                    "monitoring_duration": time.time() - start_time,
                    "timeout_seconds": timeout_seconds,
                },
            )

            self.validation_results.append(result)
            return result

        except Exception as e:
            self.logger.error(f"Error monitoring job {job_id}: {e}")
            return ValidationResult(
                job_id=job_id,
                success=False,
                message=f"Error monitoring job: {e}",
                errors=[str(e)],
            )

    def validate_job_output(
        self, job_id: str, expected_output: Any = None
    ) -> ValidationResult:
        """Validate job output and results."""
        try:
            # Get job output files
            output_files = self._get_job_output_files(job_id)

            # Read stdout and stderr
            stdout_content = ""
            stderr_content = ""

            if output_files.get("stdout"):
                stdout_content = self._read_file(output_files["stdout"])

            if output_files.get("stderr"):
                stderr_content = self._read_file(output_files["stderr"])

            # Validate output
            validation_details = {
                "stdout_lines": len(stdout_content.splitlines()),
                "stderr_lines": len(stderr_content.splitlines()),
                "stdout_length": len(stdout_content),
                "stderr_length": len(stderr_content),
                "output_files": output_files,
            }

            # Check for errors in stderr
            errors = []
            if stderr_content.strip():
                errors.append("Job produced stderr output")

            # Check expected output if provided
            if expected_output is not None:
                try:
                    # Try to parse job result
                    result_file = output_files.get("result")
                    if result_file:
                        actual_output = self._read_job_result(result_file)
                        if actual_output != expected_output:
                            errors.append(
                                f"Output mismatch: expected {expected_output}, got {actual_output}"
                            )
                        else:
                            validation_details["output_match"] = True
                    else:
                        errors.append("No result file found")
                except Exception as e:
                    errors.append(f"Error reading job result: {e}")

            success = len(errors) == 0
            message = (
                f"Job {job_id} output validation {'passed' if success else 'failed'}"
            )

            result = ValidationResult(
                job_id=job_id,
                success=success,
                message=message,
                validation_details=validation_details,
                errors=errors,
            )

            self.validation_results.append(result)
            return result

        except Exception as e:
            self.logger.error(f"Error validating job output {job_id}: {e}")
            return ValidationResult(
                job_id=job_id,
                success=False,
                message=f"Error validating job output: {e}",
                errors=[str(e)],
            )

    def _check_job_exists(self, job_id: str) -> bool:
        """Check if job exists in cluster queue."""
        try:
            if self.cluster_type == ClusterType.SLURM:
                result = subprocess.run(
                    ["squeue", "-j", job_id, "-h"], capture_output=True, text=True
                )
                return result.returncode == 0 and result.stdout.strip() != ""

            elif self.cluster_type == ClusterType.PBS:
                result = subprocess.run(
                    ["qstat", job_id], capture_output=True, text=True
                )
                return result.returncode == 0

            elif self.cluster_type == ClusterType.SGE:
                result = subprocess.run(
                    ["qstat", "-j", job_id], capture_output=True, text=True
                )
                return result.returncode == 0

            elif self.cluster_type == ClusterType.KUBERNETES:
                result = subprocess.run(
                    ["kubectl", "get", "job", job_id], capture_output=True, text=True
                )
                return result.returncode == 0

            elif self.cluster_type == ClusterType.SSH:
                # For SSH, check if process is running
                result = subprocess.run(
                    ["ps", "aux", "|", "grep", job_id],
                    shell=True,
                    capture_output=True,
                    text=True,
                )
                return job_id in result.stdout

            return False

        except Exception:
            return False

    def _get_job_details(self, job_id: str) -> Dict[str, Any]:
        """Get detailed job information."""
        details = {}

        try:
            if self.cluster_type == ClusterType.SLURM:
                result = subprocess.run(
                    ["scontrol", "show", "job", job_id], capture_output=True, text=True
                )
                if result.returncode == 0:
                    # Parse SLURM job details
                    for line in result.stdout.split("\n"):
                        if "=" in line:
                            key, value = line.split("=", 1)
                            details[key.strip()] = value.strip()

            elif self.cluster_type == ClusterType.PBS:
                result = subprocess.run(
                    ["qstat", "-f", job_id], capture_output=True, text=True
                )
                if result.returncode == 0:
                    # Parse PBS job details
                    for line in result.stdout.split("\n"):
                        if "=" in line and not line.startswith("Job Id:"):
                            key, value = line.split("=", 1)
                            details[key.strip()] = value.strip()

            elif self.cluster_type == ClusterType.SGE:
                result = subprocess.run(
                    ["qstat", "-j", job_id], capture_output=True, text=True
                )
                if result.returncode == 0:
                    # Parse SGE job details
                    for line in result.stdout.split("\n"):
                        if ":" in line:
                            key, value = line.split(":", 1)
                            details[key.strip()] = value.strip()

            elif self.cluster_type == ClusterType.KUBERNETES:
                result = subprocess.run(
                    ["kubectl", "describe", "job", job_id, "-o", "json"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    details = json.loads(result.stdout)

        except Exception as e:
            self.logger.error(f"Error getting job details for {job_id}: {e}")

        return details

    def _get_job_status(self, job_id: str) -> JobStatus:
        """Get current job status."""
        try:
            if self.cluster_type == ClusterType.SLURM:
                result = subprocess.run(
                    ["squeue", "-j", job_id, "-h", "-o", "%T"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0 and result.stdout.strip():
                    status = result.stdout.strip()
                    if status in ["PENDING", "PD"]:
                        return JobStatus.PENDING
                    elif status in ["RUNNING", "R"]:
                        return JobStatus.RUNNING
                    elif status in ["COMPLETED", "CD"]:
                        return JobStatus.COMPLETED
                    elif status in ["FAILED", "F", "TIMEOUT", "TO"]:
                        return JobStatus.FAILED
                    elif status in ["CANCELLED", "CA"]:
                        return JobStatus.CANCELLED
                else:
                    # Job not in queue, check if completed
                    result = subprocess.run(
                        ["sacct", "-j", job_id, "-n", "-o", "State"],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        status = result.stdout.strip()
                        if "COMPLETED" in status:
                            return JobStatus.COMPLETED
                        elif "FAILED" in status:
                            return JobStatus.FAILED

            elif self.cluster_type == ClusterType.PBS:
                result = subprocess.run(
                    ["qstat", job_id], capture_output=True, text=True
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split("\n")
                    if len(lines) > 1:
                        status = lines[1].split()[4]  # Status column
                        if status == "Q":
                            return JobStatus.PENDING
                        elif status == "R":
                            return JobStatus.RUNNING
                        elif status == "C":
                            return JobStatus.COMPLETED
                        elif status == "E":
                            return JobStatus.FAILED

            elif self.cluster_type == ClusterType.SGE:
                result = subprocess.run(
                    ["qstat", "-j", job_id], capture_output=True, text=True
                )
                if result.returncode == 0:
                    if "job_state" in result.stdout:
                        for line in result.stdout.split("\n"):
                            if "job_state" in line:
                                status = line.split(":")[1].strip()
                                if status == "qw":
                                    return JobStatus.PENDING
                                elif status == "r":
                                    return JobStatus.RUNNING
                                elif status == "t":
                                    return JobStatus.COMPLETED

            elif self.cluster_type == ClusterType.KUBERNETES:
                result = subprocess.run(
                    [
                        "kubectl",
                        "get",
                        "job",
                        job_id,
                        "-o",
                        "jsonpath='{.status.conditions[0].type}'",
                    ],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    status = result.stdout.strip().strip("'")
                    if status == "Complete":
                        return JobStatus.COMPLETED
                    elif status == "Failed":
                        return JobStatus.FAILED
                    else:
                        return JobStatus.RUNNING

            return JobStatus.UNKNOWN

        except Exception as e:
            self.logger.error(f"Error getting job status for {job_id}: {e}")
            return JobStatus.UNKNOWN

    def _get_job_metrics(self, job_id: str) -> JobMetrics:
        """Get comprehensive job metrics."""
        metrics = JobMetrics(
            job_id=job_id, cluster_type=self.cluster_type, start_time=time.time()
        )

        try:
            if self.cluster_type == ClusterType.SLURM:
                result = subprocess.run(
                    [
                        "sacct",
                        "-j",
                        job_id,
                        "-o",
                        "JobID,State,ExitCode,CPUTime,MaxRSS,Elapsed",
                        "-n",
                    ],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split("\n")
                    if lines:
                        fields = lines[0].split()
                        if len(fields) >= 6:
                            metrics.status = (
                                JobStatus.COMPLETED
                                if "COMPLETED" in fields[1]
                                else JobStatus.FAILED
                            )
                            metrics.exit_code = (
                                int(fields[2].split(":")[0]) if ":" in fields[2] else 0
                            )
                            metrics.cpu_time_seconds = self._parse_time(fields[3])
                            metrics.memory_used_mb = self._parse_memory(fields[4])
                            metrics.execution_time_seconds = self._parse_time(fields[5])

            # Add more cluster-specific metric parsing as needed

        except Exception as e:
            self.logger.error(f"Error getting job metrics for {job_id}: {e}")

        return metrics

    def _validate_resource_requests(
        self, job_details: Dict[str, Any], resource_requests: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate that resource requests were honored."""
        validation = {"all_valid": True, "warnings": [], "details": {}}

        # Check cores/CPUs
        if "cores" in resource_requests:
            requested_cores = resource_requests["cores"]
            allocated_cores = self._extract_allocated_cores(job_details)
            if allocated_cores and allocated_cores != requested_cores:
                validation["all_valid"] = False
                validation["warnings"].append(
                    f"CPU allocation mismatch: requested {requested_cores}, allocated {allocated_cores}"
                )
            validation["details"]["cores"] = {
                "requested": requested_cores,
                "allocated": allocated_cores,
            }

        # Check memory
        if "memory" in resource_requests:
            requested_memory = resource_requests["memory"]
            allocated_memory = self._extract_allocated_memory(job_details)
            if allocated_memory and allocated_memory != requested_memory:
                validation["warnings"].append(
                    f"Memory allocation mismatch: requested {requested_memory}, allocated {allocated_memory}"
                )
            validation["details"]["memory"] = {
                "requested": requested_memory,
                "allocated": allocated_memory,
            }

        return validation

    def _get_job_output_files(self, job_id: str) -> Dict[str, str]:
        """Get paths to job output files."""
        files = {}

        try:
            if self.cluster_type == ClusterType.SLURM:
                # SLURM typically creates slurm-<jobid>.out files
                files["stdout"] = f"slurm-{job_id}.out"
                files["stderr"] = f"slurm-{job_id}.err"

            elif self.cluster_type == ClusterType.PBS:
                # PBS creates <jobname>.o<jobid> and <jobname>.e<jobid> files
                files["stdout"] = f"{job_id}.o{job_id}"
                files["stderr"] = f"{job_id}.e{job_id}"

            elif self.cluster_type == ClusterType.SGE:
                # SGE creates <jobname>.o<jobid> and <jobname>.e<jobid> files
                files["stdout"] = f"{job_id}.o{job_id}"
                files["stderr"] = f"{job_id}.e{job_id}"

            # Add clustrix-specific result files
            files["result"] = f"result_{job_id}.pkl"
            files["error"] = f"error_{job_id}.pkl"

        except Exception as e:
            self.logger.error(f"Error getting output files for {job_id}: {e}")

        return files

    def _read_file(self, file_path: str) -> str:
        """Read file content."""
        try:
            with open(file_path, "r") as f:
                return f.read()
        except Exception:
            return ""

    def _read_job_result(self, result_file: str) -> Any:
        """Read job result from pickle file."""
        try:
            import pickle

            with open(result_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    def _parse_time(self, time_str: str) -> Optional[float]:
        """Parse time string to seconds."""
        try:
            if ":" in time_str:
                parts = time_str.split(":")
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            return float(time_str)
        except:
            return None

    def _parse_memory(self, memory_str: str) -> Optional[float]:
        """Parse memory string to MB."""
        try:
            if memory_str.endswith("K"):
                return float(memory_str[:-1]) / 1024
            elif memory_str.endswith("M"):
                return float(memory_str[:-1])
            elif memory_str.endswith("G"):
                return float(memory_str[:-1]) * 1024
            return float(memory_str)
        except:
            return None

    def _extract_allocated_cores(self, job_details: Dict[str, Any]) -> Optional[int]:
        """Extract allocated CPU cores from job details."""
        try:
            if "NCPUS" in job_details:
                return int(job_details["NCPUS"])
            elif "NumCPUs" in job_details:
                return int(job_details["NumCPUs"])
            elif "cpus" in job_details:
                return int(job_details["cpus"])
        except:
            pass
        return None

    def _extract_allocated_memory(self, job_details: Dict[str, Any]) -> Optional[str]:
        """Extract allocated memory from job details."""
        try:
            if "ReqMem" in job_details:
                return job_details["ReqMem"]
            elif "Resource_List.mem" in job_details:
                return job_details["Resource_List.mem"]
            elif "memory" in job_details:
                return job_details["memory"]
        except:
            pass
        return None

    def get_validation_summary(self) -> Dict[str, Any]:
        """Get summary of all validation results."""
        if not self.validation_results:
            return {"total_validations": 0, "summary": "No validations performed"}

        total = len(self.validation_results)
        successful = sum(1 for r in self.validation_results if r.success)
        failed = total - successful

        summary = {
            "total_validations": total,
            "successful": successful,
            "failed": failed,
            "success_rate": successful / total * 100,
            "cluster_type": self.cluster_type.value,
            "validation_details": [
                {
                    "job_id": r.job_id,
                    "success": r.success,
                    "message": r.message,
                    "errors": r.errors,
                    "warnings": r.warnings,
                }
                for r in self.validation_results
            ],
        }

        return summary

    def export_results(self, output_file: str):
        """Export validation results to JSON file."""
        summary = self.get_validation_summary()

        with open(output_file, "w") as f:
            json.dump(summary, f, indent=2, default=str)

        self.logger.info(f"Validation results exported to {output_file}")


def create_validator(cluster_type: str, **config_kwargs) -> ClusterJobValidator:
    """Create a cluster job validator for the specified cluster type."""
    config = ClusterConfig(cluster_type=cluster_type, **config_kwargs)
    return ClusterJobValidator(config)


def validate_cluster_job(
    job_id: str,
    cluster_type: str,
    function_name: str,
    expected_output: Any = None,
    timeout: int = 300,
    **config_kwargs,
) -> ValidationResult:
    """Validate a single cluster job end-to-end."""
    validator = create_validator(cluster_type, **config_kwargs)

    # Validate submission
    submission_result = validator.validate_job_submission(job_id, function_name, "", {})

    if not submission_result.success:
        return submission_result

    # Monitor execution
    execution_result = validator.monitor_job_execution(job_id, timeout)

    if not execution_result.success:
        return execution_result

    # Validate output
    output_result = validator.validate_job_output(job_id, expected_output)

    return output_result
