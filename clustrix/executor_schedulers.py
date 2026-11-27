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
import json
import logging
import threading
from typing import Dict, Any, Optional

from .utils import (
    DEFAULT_JOB_NAME,
    create_job_script,
    resolve_named_environment,
    setup_remote_environment,
)
from .auto_stage import link_into_job_directory
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
        # The environment each set of requirements resolved to, for the life
        # of this manager. A loop divided into N jobs submits N payloads with
        # the same requirements; without this each submission re-ran GPU
        # detection and re-verified the same environment over SSH, which
        # took longer than the jobs.
        self._environments: Dict[str, Dict[str, Any]] = {}

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

    def _job_requirements(self, func_data: Dict[str, Any]) -> Dict[str, str]:
        """The packages this job's execution environment is built from.

        By default the whole local environment, which cannot miss anything
        the function needs. With ``replicate_local_environment=False`` it is
        the set the function, its arguments and the project-local code
        shipped with it were found to import, closed over their dependencies
        -- a small environment that builds in seconds. That setting used to
        install nothing at all, so a function importing numpy could not run.
        """
        if getattr(self.config, "replicate_local_environment", True):
            return func_data["requirements"]
        return func_data.get("required_packages") or {}

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
            from .utils import enhanced_setup_two_venv_environment

            requirements = self._job_requirements(func_data)
            environment_key = json.dumps(sorted(requirements.items()))
            known = self._environments.get(environment_key)
            if known is not None:
                return config_for_job_script(self.config, known)

            logger.info("Setting up enhanced two-venv environment with GPU detection")

            # Use threading to implement timeout for venv setup
            venv_info = None
            exception_occurred = None

            def setup_venv():
                nonlocal venv_info, exception_occurred
                try:
                    venv_info = enhanced_setup_two_venv_environment(
                        self.connection_manager.ssh_client,
                        remote_job_dir,
                        requirements,
                        self.config,
                    )
                except Exception as e:
                    exception_occurred = e

            timeout = getattr(self.config, "venv_setup_timeout", 300)
            setup_thread = threading.Thread(target=setup_venv)
            setup_thread.daemon = True
            setup_thread.start()
            setup_thread.join(timeout=timeout)

            # A failure here used to be logged and followed by the
            # single-venv build below. That environment holds the serializers
            # and nothing else, so the job then ran without the packages the
            # caller has -- or, when that build failed too, outside any
            # environment at all -- and whatever happened next was reported
            # instead of the real cause. `use_two_venv=False` is how to ask
            # for the bare environment on purpose.
            if setup_thread.is_alive():
                raise TimeoutError(
                    f"Building the remote Python environment took longer than "
                    f"{timeout}s (config.venv_setup_timeout). It may still be "
                    f"running on the cluster; the build is locked, so a second "
                    f"submission will wait for it rather than start again. "
                    f"Raise venv_setup_timeout for a large environment."
                )
            if exception_occurred:
                raise exception_occurred
            if not venv_info:
                raise RuntimeError("Two-venv setup returned no result")
            logger.info(
                f"Two-venv setup successful, using: {venv_info['venv1_python']}"
            )
            self._environments[environment_key] = venv_info
            return config_for_job_script(self.config, venv_info)

        logger.info("Two-venv setup disabled, using basic environment setup")

        # The single-venv layout -- which means actually building that venv.
        # Setting venv_info = None without this leaves the generated script
        # activating a virtualenv nobody created.
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
                self._job_requirements(func_data),
                self.config,
            )
        return config_for_job_script(self.config, None)

    def submit_slurm_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via SLURM."""
        remote_job_dir, result_key = self._stage_job_directory(func_data)
        # Input files found for this call (clustrix.auto_stage) are linked
        # in at the relative paths the code names them by.
        link_into_job_directory(
            self.connection_manager, remote_job_dir, job_config.get("data_links", ())
        )
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
            job_name=func_data.get("func_info", {}).get("job_name") or DEFAULT_JOB_NAME,
        )

        # Upload and submit job script
        script_path = f"{remote_job_dir}/job.sh"
        self.connection_manager.create_remote_file(script_path, script_content)

        # Submit job
        # --parsable prints "<jobid>" or "<jobid>;<cluster>" and nothing else.
        # Taking the last word of the human-readable line meant that when
        # sbatch refused the job -- unknown partition, memory above the node
        # limit -- stdout was empty and this died on an IndexError that named
        # neither sbatch nor its reason.
        cmd = f"cd {shlex.quote(remote_job_dir)} && sbatch --parsable job.sh"
        stdout, stderr = self.connection_manager.execute_remote_command(cmd)
        job_id = stdout.strip().split(";")[0].strip()
        if not job_id.isdigit():
            raise RuntimeError(
                "sbatch did not accept the job. It said: "
                f"{(stderr.strip() or stdout.strip() or 'nothing')!r}. The job "
                f"script is at {remote_job_dir}/job.sh on the cluster."
            )

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
        # Input files found for this call (clustrix.auto_stage) are linked
        # in at the relative paths the code names them by.
        link_into_job_directory(
            self.connection_manager, remote_job_dir, job_config.get("data_links", ())
        )
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

        # Run in the background, detached from this channel, and have the
        # wrapper record how the script ended. Two things depend on that
        # record. Completion: ``job.exit`` is written after everything else,
        # so its presence means result.pkl and its signature are whole --
        # polling for result.pkl alone raced the signature, which is written
        # a moment later. Failure: a job that is killed, or calls
        # ``sys.exit``, leaves no error report and may leave nothing on
        # stderr; without an exit status the only verdict available was
        # "still running", until job_wait_timeout a day later.
        quoted_dir = shlex.quote(remote_job_dir)
        #
        # The braces matter. `cd X && nohup ... &` backgrounds the whole
        # and-list in a subshell that still holds this channel's stdout and
        # stderr, so a real sshd kept the channel open -- and this call
        # blocked -- until the job had finished.
        cmd = (
            f"cd {quoted_dir} && {{ "
            "nohup bash -c 'bash job.sh > job.out 2> job.err; "
            "echo $? > job.exit.tmp; mv job.exit.tmp job.exit' "
            "> /dev/null 2>&1 < /dev/null & echo $!; }"
        )
        stdout, stderr = self.connection_manager.execute_remote_command(cmd, check=True)
        pid = stdout.strip().splitlines()[-1] if stdout.strip() else ""
        if not pid.isdigit():
            raise RuntimeError(
                f"Could not start the job in {remote_job_dir}: the remote shell "
                f"did not report a process id (got {stdout!r}; stderr: "
                f"{stderr.strip()!r})."
            )

        # Named after the job directory, which is unique. A timestamp alone
        # gave two submissions in the same second the same id, and the second
        # silently replaced the first in active_jobs.
        job_id = "ssh_" + os.path.basename(remote_job_dir)[len("job_") :]

        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "result_key": result_key,
            "status": "running",
            "submit_time": time.time(),
            "pid": int(pid),
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
            self.connection_manager.execute_remote_command(
                f"scancel {shlex.quote(str(job_id))}", check=True
            )
        elif self.config.cluster_type == "ssh":
            pid = (self.active_jobs.get(job_id) or {}).get("pid")
            if pid is None:
                raise ValueError(
                    f"Cannot cancel {job_id}: this executor did not start it, "
                    "so it does not know which remote process it is."
                )
            # The whole tree, listed before anything is signalled: the
            # recorded pid is the wrapper shell, and killing only that leaves
            # job.sh and the Python running the function alive. Killing the
            # leaves first is no better -- job.sh would move on to its next
            # command before its own turn came.
            self.connection_manager.execute_remote_command(
                '_clustrix_tree() { echo "$1"; for _c in $(pgrep -P "$1"); do '
                '_clustrix_tree "$_c"; done; }; '
                f"kill -TERM $(_clustrix_tree {int(pid)}) 2>/dev/null; true"
            )

        if job_id in self.active_jobs:
            del self.active_jobs[job_id]
