"""SLURM scheduler and plain-SSH job submission and monitoring.

This module handles job submission, monitoring, and status checking for SLURM
and for direct execution over SSH.

Clustrix ships no PBS/Torque or SGE submission. Neither has been verified
against a real scheduler of that kind, so neither is offered. Support for
them is tracked in issues #140 and #141.
"""

import os
import secrets
import shlex
import time
import tempfile
import pickle
import logging
import threading
from typing import Dict, Any, Optional

from .utils import (
    create_job_script,
    resolve_named_environment,
    setup_remote_environment,
)
from .executor_scheduler_status import SchedulerStatusManager

logger = logging.getLogger(__name__)


def config_for_job_script(config, venv_info: Optional[Dict[str, Any]]):
    """Record the environment layout the job script is generated from.

    The one place a completed (or absent) venv setup is written back onto the
    config, and deliberately the *only* field it writes.

    What it must not write is ``config.python_executable``. That used to be
    overwritten here with ``venv_info["venv1_python"]`` -- clustrix's own
    *serialization* interpreter, an internal detail of the two-venv layout
    that ``venv_info`` already carries. Nothing ever read it back for VENV1,
    and #164 then made ``python_executable`` reach VENV2, which turned the
    overwrite into a defect on the default path: on a conda cluster the job
    script became ``conda run -n prod 'conda run -n clustrix_venv1_x python'
    -c "``, one quoted word in the executable position and unrunnable, and on
    a cluster without conda it became ``conda run -n prod
    /job/venv1_serialization/bin/python -c "``, which runs the user's
    function under the serialization venv instead of the environment they
    named -- silently, which is the exact defect #164 exists to fix.

    ``config`` is the process-wide singleton, so the overwrite also outlived
    the submission: the next job's ``resolve_remote_python`` read the
    leftover value as if the user had configured it.

    Args:
        config: The cluster configuration, mutated in place and returned.
        venv_info: The two-venv layout, or ``None`` when only the single venv
            was built.

    Returns:
        ``config``, for the caller to generate the job script from.
    """
    config.venv_info = venv_info
    return config


class SchedulerManager:
    """Submits jobs to SLURM and plain SSH hosts."""

    def _prepare_job_dir(self, remote_job_dir: str) -> str:
        """Create the job directory and give the job a result-signing key.

        The key lets the caller verify result.pkl before unpickling it, which
        matters because unpickling executes code (#121). It is written inside
        the job directory with 0600, and the directory itself is 0700, so on a
        shared filesystem another user can neither read the key nor forge a
        result that verifies against it.

        This bounds the trust to whoever can already read the job directory.
        It is not a defence against a wholly compromised remote host -- that
        host runs the function anyway -- but it does stop an unrelated user,
        a stale file, or a truncated transfer from being loaded as code.

        The key is written over SFTP rather than by a shell command. Writing it
        with `printf '%s' <key> > file` would put the key in the remote command
        line, and on a default Linux `/proc` any user on that login node can
        read another user's command line out of `ps` -- which would hand the
        secret to exactly the people the 0700 directory is meant to exclude.
        """
        # `mkdir -p` succeeds on a directory that already exists and is owned
        # by somebody else, and an unchecked `chmod` then fails silently. Job
        # directory names were fully predictable, so on a world-writable
        # remote_work_dir an attacker could pre-create the directory, receive
        # the signing key into it, and forge a result -- turning the defence
        # into a code-execution path on the *submitting* machine. Create it
        # exclusively, and refuse to continue if that fails.
        self.connection_manager.execute_remote_command(
            f"mkdir -p {shlex.quote(os.path.dirname(remote_job_dir))}", check=True
        )
        self.connection_manager.execute_remote_command(
            f"mkdir -m 700 {shlex.quote(remote_job_dir)}", check=True
        )
        key = secrets.token_hex(32)
        self.connection_manager.create_remote_file(
            f"{remote_job_dir}/.clustrix_result_key", key, mode=0o600
        )
        return key

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

    def _stage_job_directory(self, func_data: Dict[str, Any]) -> tuple:
        """Create the remote job directory and upload the function data.

        Every scheduler needs exactly this, and each carried its own copy.

        Returns:
            (remote_job_dir, result_key)
        """
        work_dir = self.connection_manager.resolve_remote_path(
            self.config.remote_work_dir
        )
        remote_job_dir = f"{work_dir}/job_{int(time.time())}_{secrets.token_hex(4)}"
        result_key = self._prepare_job_dir(remote_job_dir)

        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f, protocol=4)
            local_pickle_path = f.name
        try:
            self.connection_manager.upload_file(
                local_pickle_path, f"{remote_job_dir}/function_data.pkl"
            )
        finally:
            os.unlink(local_pickle_path)

        return remote_job_dir, result_key

    def _setup_job_environment(
        self,
        remote_job_dir: str,
        func_data: Dict[str, Any],
        named_env: Optional[str] = None,
    ):
        """Build the Python environment the generated job script will activate.

        Shared by SLURM and SSH, which each used to carry their own copy of
        it (#120).

        Args:
            remote_job_dir: The job directory already staged on the cluster.
            func_data: The serialized function and its requirements.
            named_env: The existing cluster environment this job was told to
                run in, from ``resolve_named_environment``. When there is one,
                the single-venv build below is not merely redundant -- the
                generated script never activates it -- so it is skipped, which
                takes a pip install of the whole local environment off the
                front of every such submission.

        Returns the config to generate the job script from: `venv_info` set for
        the two-venv layout, or cleared when only the single venv was built.
        """

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
                    raise TimeoutError("Two-venv setup timed out")
                elif exception_occurred:
                    raise exception_occurred
                elif venv_info:
                    # Update config with venv paths for job script generation
                    logger.info(
                        f"Two-venv setup successful, using: "
                        f"{venv_info['venv1_python']}"
                    )
                    return config_for_job_script(self.config, venv_info)
                else:
                    raise RuntimeError("Two-venv setup returned no result")

            except Exception as e:
                logger.warning(
                    f"Two-venv setup failed, falling back to basic setup: {e}"
                )
        else:
            logger.info("Two-venv setup disabled, using basic environment setup")

        # Fall back to the single-venv layout -- which means actually building
        # that venv. Setting venv_info = None without this leaves the generated
        # script activating a virtualenv nobody created.
        #
        # Unless the user named an environment. Then the generated script runs
        # `conda run -n <name>` and never sources `venv/bin/activate`, so
        # building the venv is a pip install of the entire local environment
        # whose only effect is to make every submission slower. This is the
        # `use_two_venv=False` case, which is what naming an environment is
        # for in the first place.
        if named_env:
            logger.info(
                "Skipping environment replication: this job runs in the "
                "existing cluster environment %r, so the environment "
                "clustrix would otherwise build would never be activated.",
                named_env,
            )
        else:
            setup_remote_environment(
                self.connection_manager.ssh_client,
                remote_job_dir,
                func_data["requirements"],
                self.config,
            )
        return config_for_job_script(self.config, None)

    def submit_slurm_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via SLURM."""
        remote_job_dir, result_key = self._stage_job_directory(func_data)
        updated_config = self._setup_job_environment(
            remote_job_dir,
            func_data,
            resolve_named_environment(job_config, self.config),
        )

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
            "result_key": result_key,
            "status": "submitted",
            "submit_time": time.time(),
        }

        return job_id

    def submit_ssh_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via direct SSH using two-venv approach."""
        remote_job_dir, result_key = self._stage_job_directory(func_data)
        updated_config = self._setup_job_environment(
            remote_job_dir,
            func_data,
            resolve_named_environment(job_config, self.config),
        )

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
        cmd = (
            f"cd {remote_job_dir} && "
            "nohup bash job.sh > job.out 2> job.err < /dev/null &"
        )
        stdout, stderr = self.connection_manager.execute_remote_command(cmd)

        # Use timestamp as job ID for SSH
        job_id = f"ssh_{int(time.time())}"

        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "result_key": result_key,
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

        if job_id in self.active_jobs:
            del self.active_jobs[job_id]
