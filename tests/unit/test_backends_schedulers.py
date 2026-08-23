"""Every scheduler gets the same environment (#120).

No mocks. Two real properties of the shipped code are asserted:

1. every scheduler submission delegates to the one ``_setup_job_environment``
   -- PBS had no environment setup at all, which was the original bug; and
2. the job script each backend generates activates the virtualenv that setup
   builds, and signs its result, exactly as SLURM's does.

The PBS and SGE cases this file was written for are gone: those backends were
removed because they had never been run against real hardware (issues #140 and
#141). The invariant they exposed is still worth holding for the backends that
remain, so it is asserted over SLURM and SSH here.

Unverified here: an actual ``sbatch`` against a real SLURM cluster. That is
covered by ``scripts/verify_cluster_usecases.py`` and ``docs/evidence/``.
"""

import inspect

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_schedulers import SchedulerManager
from clustrix.utils import create_job_script

SUBMIT_METHODS = [
    "submit_slurm_job",
    "submit_ssh_job",
]

JOB_CONFIG = {"cores": 4, "memory": "8GB", "time": "01:00:00"}


@pytest.mark.parametrize("method_name", SUBMIT_METHODS)
def test_every_scheduler_sets_up_its_environment(method_name):
    source = inspect.getsource(getattr(SchedulerManager, method_name))
    assert "self._setup_job_environment(" in source
    assert "self._stage_job_directory(" in source


@pytest.mark.parametrize("method_name", SUBMIT_METHODS)
def test_no_scheduler_carries_its_own_copy_of_the_venv_setup(method_name):
    """The two-venv block lived in some submit methods and was missing from others."""
    source = inspect.getsource(getattr(SchedulerManager, method_name))
    assert "enhanced_setup_two_venv_environment" not in source
    assert "setup_remote_environment(" not in source


@pytest.mark.parametrize("cluster_type", ["slurm", "ssh"])
def test_generated_script_runs_the_shared_execution_block(cluster_type):
    config = ClusterConfig(cluster_type=cluster_type, remote_work_dir="/scratch/x")
    script = create_job_script(
        cluster_type=cluster_type,
        job_config=JOB_CONFIG,
        remote_job_dir="/scratch/x/job_1",
        config=config,
    )

    # The venv the (now shared) environment setup builds.
    assert ". venv/bin/activate" in script
    # The result signing the caller verifies before unpickling.
    assert "result.pkl.hmac" in script
    assert "CLUSTRIX_RESULT_KEY" in script
    # The file PBS used to try to run and which nothing ever creates.
    assert "execute_function.py" not in script


def test_ssh_and_slurm_scripts_execute_identically():
    config = ClusterConfig(remote_work_dir="/scratch/x")
    scripts = {}
    for cluster_type in ("ssh", "slurm"):
        config.cluster_type = cluster_type
        scripts[cluster_type] = create_job_script(
            cluster_type=cluster_type,
            job_config=JOB_CONFIG,
            remote_job_dir="/scratch/x/job_1",
            config=config,
        )

    def execution_part(script):
        return script[script.index("export CLUSTRIX_RESULT_KEY") :]

    assert execution_part(scripts["ssh"]) == execution_part(scripts["slurm"])
