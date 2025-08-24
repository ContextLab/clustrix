"""SLURM, PBS, and SGE scheduler job submission and monitoring.

This module handles job submission, monitoring, and status checking for traditional
HPC schedulers including SLURM, PBS/Torque, and Sun Grid Engine (SGE).
"""

import os
import time
import tempfile
import pickle
import logging
import threading
from typing import Dict, Any, Optional

from .utils import create_job_script, setup_remote_environment
from .executor_scheduler_status import SchedulerStatusManager

logger = logging.getLogger(__name__)


class SchedulerManager:
    """Manages jobs for traditional HPC schedulers (SLURM, PBS, SGE)."""

    def __init__(self, config, connection_manager):
        """Initialize scheduler manager.

        Args:
            config: ClusterConfig instance
            connection_manager: ConnectionManager instance for SSH operations
        """
        self.config = config
        self.connection_manager = connection_manager
        self.active_jobs: Dict[str, Any] = {}
        self.status_manager = SchedulerStatusManager(config, connection_manager)

    def submit_slurm_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via SLURM."""
        # Create remote working directory
        remote_job_dir = f"{self.config.remote_work_dir}/job_{int(time.time())}"
        self.connection_manager.execute_remote_command(f"mkdir -p {remote_job_dir}")

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f, protocol=4)
            local_pickle_path = f.name

        self.connection_manager.upload_file(
            local_pickle_path, f"{remote_job_dir}/function_data.pkl"
        )
        os.unlink(local_pickle_path)

        # Setup two-venv environment for cross-version compatibility (if enabled)
        updated_config = self.config
        if getattr(self.config, "use_two_venv", True):
            try:
                from .utils import enhanced_setup_two_venv_environment

                logger.info(
                    "Setting up enhanced two-venv environment with GPU detection"
                )

                # Use threading to implement timeout for venv setup
                venv_info = None
                exception_occurred = None

                def setup_venv():
                    nonlocal venv_info, exception_occurred
                    try:
                        venv_info = enhanced_setup_two_venv_environment(
                            self.connection_manager.ssh_client,
                            remote_job_dir,
                            func_data["requirements"],
                            self.config,
                        )
                    except Exception as e:
                        exception_occurred = e

                setup_thread = threading.Thread(target=setup_venv)
                setup_thread.daemon = True
                setup_thread.start()
                setup_thread.join(
                    timeout=getattr(self.config, "venv_setup_timeout", 300)
                )

                if setup_thread.is_alive():
                    logger.warning(
                        "Two-venv setup timed out, falling back to basic setup"
                    )
                    raise TimeoutError("Two-venv setup timed out")
                elif exception_occurred:
                    raise exception_occurred
                elif venv_info:
                    # Update config with venv paths for job script generation
                    updated_config.python_executable = venv_info["venv1_python"]
                    # Store venv_info for script generation
                    updated_config.venv_info = venv_info
                    logger.info(
                        f"Two-venv setup successful, using: {venv_info['venv1_python']}"
                    )
                else:
                    raise RuntimeError("Two-venv setup returned no result")

            except Exception as e:
                logger.warning(
                    f"Two-venv setup failed, falling back to basic setup: {e}"
                )
                # Fallback to basic environment setup
                setup_remote_environment(
                    self.connection_manager.ssh_client,
                    remote_job_dir,
                    func_data["requirements"],
                    self.config,
                )
                updated_config.venv_info = None
        else:
            logger.info("Two-venv setup disabled, using basic environment setup")
            # Use basic environment setup
            setup_remote_environment(
                self.connection_manager.ssh_client,
                remote_job_dir,
                func_data["requirements"],
                self.config,
            )
            updated_config = self.config
            updated_config.venv_info = None

        # Create job script
        script_content = create_job_script(
            cluster_type="slurm",
            job_config=job_config,
            remote_job_dir=remote_job_dir,
            config=updated_config,
        )

        # Upload and submit job script
        script_path = f"{remote_job_dir}/job.sh"
        self.connection_manager.create_remote_file(script_path, script_content)

        # Submit job
        cmd = f"cd {remote_job_dir} && sbatch job.sh"
        stdout, stderr = self.connection_manager.execute_remote_command(cmd)

        # Extract job ID from sbatch output
        job_id = stdout.strip().split()[-1]

        # Store job info
        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "submitted",
            "submit_time": time.time(),
        }

        return job_id

    def submit_pbs_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via PBS."""
        # Similar to SLURM but with PBS commands
        remote_job_dir = f"{self.config.remote_work_dir}/job_{int(time.time())}"
        self.connection_manager.execute_remote_command(f"mkdir -p {remote_job_dir}")

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f, protocol=4)
            local_pickle_path = f.name

        self.connection_manager.upload_file(
            local_pickle_path, f"{remote_job_dir}/function_data.pkl"
        )
        os.unlink(local_pickle_path)

        # Create PBS script
        script_content = create_job_script(
            cluster_type="pbs",
            job_config=job_config,
            remote_job_dir=remote_job_dir,
            config=self.config,
        )

        script_path = f"{remote_job_dir}/job.pbs"
        self.connection_manager.create_remote_file(script_path, script_content)

        # Submit job
        cmd = f"cd {remote_job_dir} && qsub job.pbs"
        stdout, stderr = self.connection_manager.execute_remote_command(cmd)

        job_id = stdout.strip()

        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "submitted",
            "submit_time": time.time(),
        }

        return job_id

    def submit_sge_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via SGE."""
        # Create remote working directory
        remote_job_dir = f"{self.config.remote_work_dir}/job_{int(time.time())}"
        self.connection_manager.execute_remote_command(f"mkdir -p {remote_job_dir}")

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f, protocol=4)
            local_pickle_path = f.name

        self.connection_manager.upload_file(
            local_pickle_path, f"{remote_job_dir}/function_data.pkl"
        )
        os.unlink(local_pickle_path)

        # Setup environment
        setup_remote_environment(
            self.connection_manager.ssh_client,
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
        self.connection_manager.create_remote_file(script_path, script_content)

        # Submit job
        cmd = f"cd {remote_job_dir} && qsub job.sge"
        stdout, stderr = self.connection_manager.execute_remote_command(cmd)

        # Extract job ID from qsub output (SGE format: "Your job 123456 ...")
        job_id = stdout.strip().split()[2] if "Your job" in stdout else stdout.strip()

        # Store job info
        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "submitted",
            "submit_time": time.time(),
        }

        return job_id

    def submit_ssh_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via direct SSH using two-venv approach."""
        remote_job_dir = f"{self.config.remote_work_dir}/job_{int(time.time())}"
        self.connection_manager.execute_remote_command(f"mkdir -p {remote_job_dir}")

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f, protocol=4)
            local_pickle_path = f.name

        self.connection_manager.upload_file(
            local_pickle_path, f"{remote_job_dir}/function_data.pkl"
        )
        os.unlink(local_pickle_path)

        # Setup two-venv environment for cross-version compatibility (if enabled)
        updated_config = self.config
        if getattr(self.config, "use_two_venv", True):
            try:
                from .utils import enhanced_setup_two_venv_environment

                logger.info(
                    "Setting up enhanced two-venv environment with GPU detection"
                )

                # Use threading to implement timeout for venv setup
                venv_info = None
                exception_occurred = None

                def setup_venv():
                    nonlocal venv_info, exception_occurred
                    try:
                        venv_info = enhanced_setup_two_venv_environment(
                            self.connection_manager.ssh_client,
                            remote_job_dir,
                            func_data["requirements"],
                            self.config,
                        )
                    except Exception as e:
                        exception_occurred = e

                setup_thread = threading.Thread(target=setup_venv)
                setup_thread.daemon = True
                setup_thread.start()
                setup_thread.join(
                    timeout=getattr(self.config, "venv_setup_timeout", 300)
                )

                if setup_thread.is_alive():
                    logger.warning(
                        "Two-venv setup timed out, falling back to basic setup"
                    )
                    raise TimeoutError("Two-venv setup timed out")
                elif exception_occurred:
                    raise exception_occurred
                elif venv_info:
                    # Update config with venv paths
                    updated_config.python_executable = venv_info["venv1_python"]
                    # Store venv_info for script generation
                    updated_config.venv_info = venv_info
                    logger.info(
                        f"Two-venv setup successful, using: {venv_info['venv1_python']}"
                    )
                else:
                    raise RuntimeError("Two-venv setup returned no result")

            except Exception as e:
                logger.warning(f"Failed to setup two-venv environment: {e}")
                # Fall back to original approach
                updated_config.venv_info = None
        else:
            logger.info("Two-venv setup disabled, using basic environment setup")
            updated_config.venv_info = None

        # Create execution script
        script_content = create_job_script(
            cluster_type="ssh",
            job_config=job_config,
            remote_job_dir=remote_job_dir,
            config=updated_config,
        )

        script_path = f"{remote_job_dir}/job.sh"
        self.connection_manager.create_remote_file(script_path, script_content)

        # Execute in background
        cmd = f"cd {remote_job_dir} && nohup bash job.sh > job.out 2> job.err < /dev/null &"
        stdout, stderr = self.connection_manager.execute_remote_command(cmd)

        # Use timestamp as job ID for SSH
        job_id = f"ssh_{int(time.time())}"

        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "running",
            "submit_time": time.time(),
        }

        return job_id

    def check_job_status(self, job_id: str) -> str:
        """Check the current status of a job across multiple cluster schedulers."""
        return self.status_manager.check_job_status(job_id, self.active_jobs)

    def get_error_log(self, job_id: str) -> str:
        """Retrieve comprehensive error information from a failed job."""
        return self.status_manager.get_error_log(job_id, self.active_jobs)

    def extract_original_exception(self, job_id: str) -> Optional[Exception]:
        """Extract and reconstruct the original exception from a failed remote job."""
        return self.status_manager.extract_original_exception(job_id, self.active_jobs)

    def cancel_job(self, job_id: str):
        """Cancel a running job."""
        if self.config.cluster_type == "slurm":
            self.connection_manager.execute_remote_command(f"scancel {job_id}")
        elif self.config.cluster_type == "pbs":
            self.connection_manager.execute_remote_command(f"qdel {job_id}")
        elif self.config.cluster_type == "sge":
            self.connection_manager.execute_remote_command(f"qdel {job_id}")

        if job_id in self.active_jobs:
            del self.active_jobs[job_id]
