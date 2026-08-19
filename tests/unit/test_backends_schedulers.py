"""PBS gets the same environment every other scheduler gets (#120).

No mocks. Two real properties of the shipped code are asserted:

1. every scheduler submission delegates to the one ``_setup_job_environment``
   -- PBS had no environment setup at all, which is the whole bug; and
2. the job script PBS generates activates the virtualenv that setup builds,
   and signs its result, exactly as SLURM's does.

Unverified here: an actual ``qsub`` against a real PBS cluster. That needs a
PBS scheduler; nothing in this repository can stand in for one.
"""

import inspect

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_schedulers import SchedulerManager
from clustrix.utils import create_job_script

SUBMIT_METHODS = [
    "submit_slurm_job",
    "submit_pbs_job",
    "submit_sge_job",
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
    """The two-venv block lived in two submit methods and was missing from two."""
    source = inspect.getsource(getattr(SchedulerManager, method_name))
    assert "enhanced_setup_two_venv_environment" not in source
    assert "setup_remote_environment(" not in source


@pytest.mark.parametrize("cluster_type", ["slurm", "pbs", "sge", "ssh"])
def test_generated_script_runs_the_shared_execution_block(cluster_type):
    config = ClusterConfig(cluster_type=cluster_type, remote_work_dir="/scratch/x")
    script = create_job_script(
        cluster_type=cluster_type,
        job_config=JOB_CONFIG,
        remote_job_dir="/scratch/x/job_1",
        config=config,
    )

    # The venv the (now shared) environment setup builds.
    assert "source venv/bin/activate" in script
    # The result signing the caller verifies before unpickling.
    assert "result.pkl.hmac" in script
    assert "CLUSTRIX_RESULT_KEY" in script
    # The file PBS used to try to run and which nothing ever creates.
    assert "execute_function.py" not in script


def test_pbs_and_slurm_scripts_execute_identically():
    config = ClusterConfig(remote_work_dir="/scratch/x")
    scripts = {}
    for cluster_type in ("pbs", "slurm"):
        config.cluster_type = cluster_type
        scripts[cluster_type] = create_job_script(
            cluster_type=cluster_type,
            job_config=JOB_CONFIG,
            remote_job_dir="/scratch/x/job_1",
            config=config,
        )

    def execution_part(script):
        return script[script.index("export CLUSTRIX_RESULT_KEY") :]

    assert execution_part(scripts["pbs"]) == execution_part(scripts["slurm"])
