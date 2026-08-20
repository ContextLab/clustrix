"""A named, pre-existing cluster environment must reach the job script (#164).

``@cluster(environment="myenv")`` and ``configure(conda_env_name="myenv")``
were both accepted and discarded: nothing read ``job_config["environment"]``,
and ``config.conda_env_name`` was read only by ``setup_environment()``, whose
sole caller sits on an orphaned module. A user asking for a curated HPC
environment got a replicated one instead, silently.

These tests pin the routing, the precedence against environment replication,
the warning that fires when both are in play, and -- through committed golden
scripts -- that the replication path is byte-identical when no environment is
named.
"""

import logging
import shlex
from pathlib import Path

import pytest

from clustrix.config import ClusterConfig
from clustrix.utils import (
    create_job_script,
    job_execution_lines,
    resolve_named_environment,
)

GOLDEN_DIR = Path(__file__).parent / "data" / "job_scripts"

BASE_JOB = {"cores": 2, "memory": "4GB", "time": "01:00:00"}

#: The two-venv layout ``setup_two_venv_environment`` returns for a conda
#: cluster, including the synthetic ``conda_env_name`` it sets "for backward
#: compatibility with job script generation". That key must never be mistaken
#: for the user's own setting.
CONDA_VENV_INFO = {
    "venv1_python": "conda run -n clustrix_venv1_abc123 python",
    "venv1_path": "conda:clustrix_venv1_abc123",
    "venv2_python": "conda run -n clustrix_venv2_abc123 python",
    "venv2_path": "conda:clustrix_venv2_abc123",
    "conda_env1_name": "clustrix_venv1_abc123",
    "conda_env2_name": "clustrix_venv2_abc123",
    "conda_env_name": "clustrix_venv2_abc123",
    "conda_setup_prefix": "source /opt/conda/etc/profile.d/conda.sh",
    "uses_conda": True,
}

#: The same layout when the cluster has no conda: plain virtualenvs, and no
#: conda environment names at all.
PLAIN_VENV_INFO = {
    "venv1_python": "/remote/job/venv1_serialization/bin/python",
    "venv1_path": "/remote/job/venv1_serialization",
    "venv2_python": "/remote/job/venv2_execution/bin/python",
    "venv2_path": "/remote/job/venv2_execution",
    "conda_env1_name": None,
    "conda_env2_name": None,
    "uses_conda": False,
}


def make_config(**overrides):
    """A cluster config with the fields the script generators need."""
    config = ClusterConfig(
        cluster_type=overrides.pop("cluster_type", "slurm"),
        cluster_host="cluster.example.edu",
        username="researcher",
        remote_work_dir="/scratch/project",
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def script(cluster_type, *, environment=None, **config_overrides):
    """Generate a job script, optionally with a decorator-supplied env name."""
    job_config = dict(BASE_JOB)
    if environment is not None:
        job_config["environment"] = environment
    return create_job_script(
        cluster_type, job_config, "/remote/job", make_config(**config_overrides)
    )


SCHEDULERS = ["slurm", "ssh"]


class TestBothRoutesReachTheScript:
    """The four-line reproduction from the issue, as assertions."""

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_decorator_route_reaches_the_script(self, cluster_type):
        text = script(cluster_type, environment="from_decorator")
        assert "conda run -n from_decorator python" in text

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_config_route_reaches_the_script(self, cluster_type):
        text = script(cluster_type, conda_env_name="from_config")
        assert "conda run -n from_config python" in text

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_both_routes_together_prefer_the_decorator(self, cluster_type):
        """The per-call instruction beats the standing configuration.

        This is the same precedence ``decorator.py`` already applies when it
        writes ``environment or config.conda_env_name`` into job_config; a
        script generated without going through the decorator must agree.
        """
        text = script(
            cluster_type, environment="from_decorator", conda_env_name="from_config"
        )
        assert "conda run -n from_decorator python" in text
        assert "from_config" not in text

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_neither_route_leaves_the_built_venv_activated(self, cluster_type):
        text = script(cluster_type)
        assert "source venv/bin/activate" in text
        assert "conda run -n" not in text

    def test_an_empty_name_is_not_a_request(self):
        """A blank widget field is not an instruction to run in ''."""
        assert resolve_named_environment({"environment": "   "}, make_config()) is None
        assert "conda run -n" not in script("slurm", environment="")


class TestPrecedenceAgainstReplication:
    """A named environment beats the replicated one, loudly."""

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_named_environment_replaces_the_replicated_execution_env(
        self, cluster_type
    ):
        text = script(
            cluster_type, environment="production", venv_info=dict(CONDA_VENV_INFO)
        )
        assert "conda run -n production python" in text
        # VENV2 -- the execution environment -- is the one that is replaced.
        assert "clustrix_venv2_abc123" not in text

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_the_serialization_env_is_never_replaced(self, cluster_type):
        """VENV1 is clustrix's own machinery, not the user's environment.

        It must keep the Python version and the dill install that the result
        round-trip depends on, whatever the user's environment contains.
        """
        text = script(
            cluster_type, environment="production", venv_info=dict(CONDA_VENV_INFO)
        )
        assert "conda run -n clustrix_venv1_abc123 python" in text

    def test_named_environment_also_replaces_a_plain_virtualenv_venv2(self):
        text = script(
            "slurm", environment="production", venv_info=dict(PLAIN_VENV_INFO)
        )
        assert "conda run -n production python" in text
        assert "/remote/job/venv2_execution/bin/python" not in text
        # VENV1 still activates its own virtualenv.
        assert "source /remote/job/venv1_serialization/bin/activate" in text

    def test_the_conflict_is_reported(self, caplog):
        with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
            script("slurm", environment="production", venv_info=dict(CONDA_VENV_INFO))
        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "Both an existing environment and environment replication" in m
            and "'production'" in m
            and "clustrix_venv2_abc123" in m
            and "The named environment wins" in m
            for m in messages
        ), messages

    def test_no_conflict_is_reported_when_no_environment_is_named(self, caplog):
        with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
            script("slurm", venv_info=dict(CONDA_VENV_INFO))
        assert not [
            r
            for r in caplog.records
            if "existing environment and environment replication" in r.getMessage()
        ]


class TestTheSyntheticNameIsNotTheUsersName:
    """``venv_info["conda_env_name"]`` is clustrix's, not the user's.

    ``setup_two_venv_environment`` writes ``clustrix_venv2_<key>`` under that
    key. Reading it as if the user had asked for it would make every
    replicated job look like a named-environment job -- and would fire the
    conflict warning at users who never named anything.
    """

    def test_resolution_ignores_the_synthetic_name(self):
        config = make_config(venv_info=dict(CONDA_VENV_INFO))
        assert config.conda_env_name is None
        assert resolve_named_environment(dict(BASE_JOB), config) is None

    def test_replication_alone_does_not_look_like_a_named_environment(self, caplog):
        with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
            text = script("slurm", venv_info=dict(CONDA_VENV_INFO))
        # The replicated environment is still what runs the function.
        assert "conda run -n clustrix_venv2_abc123 python" in text
        assert not [
            r for r in caplog.records if "The named environment wins" in r.getMessage()
        ]

    def test_replication_does_not_overwrite_the_users_setting(self):
        """The synthetic name lives in venv_info; conda_env_name is untouched."""
        config = make_config(
            conda_env_name="production", venv_info=dict(CONDA_VENV_INFO)
        )
        assert config.conda_env_name == "production"
        assert resolve_named_environment(dict(BASE_JOB), config) == "production"


class TestUnsafeNamesAreRefused:
    """The name reaches a shell command and an unquoted script comment."""

    @pytest.mark.parametrize(
        "bad", ["ev'il; touch /tmp/pwn", "ok\nrm -rf /", "$(whoami)", "a b"]
    )
    def test_a_name_carrying_shell_syntax_is_refused(self, bad):
        with pytest.raises(ValueError) as excinfo:
            script("slurm", environment=bad)
        assert "conda_env_name" in str(excinfo.value)

    @pytest.mark.parametrize(
        "bad", ["ev'il; touch /tmp/pwn", "ok\nrm -rf /", "$(whoami)"]
    )
    def test_the_two_venv_path_refuses_it_too(self, bad):
        with pytest.raises(ValueError):
            script("slurm", environment=bad, venv_info=dict(CONDA_VENV_INFO))

    @pytest.mark.parametrize("good", ["py3.11-torch", "team_env-2", "proj.v1"])
    def test_ordinary_conda_names_are_accepted_and_quoted(self, good):
        text = script("slurm", environment=good)
        assert "conda run -n " + shlex.quote(good) + " python" in text


class TestTwoVenvContractSurvives:
    """The three-stage handoff stays symmetric with a named environment."""

    @staticmethod
    def _stages(text):
        blocks, current, inside = [], [], False
        for line in text.split("\n"):
            if line.endswith('python -c "'):
                inside, current = True, []
                continue
            if inside and line == '"':
                blocks.append("\n".join(current))
                inside = False
                continue
            if inside:
                current.append(line)
        return blocks

    def test_there_are_still_three_stages(self):
        text = script(
            "slurm", environment="production", venv_info=dict(CONDA_VENV_INFO)
        )
        assert len(self._stages(text)) == 3

    def test_every_stage_still_compiles(self):
        text = script(
            "slurm", environment="production", venv_info=dict(CONDA_VENV_INFO)
        )
        for i, block in enumerate(self._stages(text), 1):
            compile(block, f"<stage-{i}>", "exec")

    def test_every_stage_still_binds_a_rich_serializer(self):
        text = script(
            "slurm", environment="production", venv_info=dict(CONDA_VENV_INFO)
        )
        for block in self._stages(text):
            assert "import dill as _ser" in block
            assert "import cloudpickle as _ser" in block

    def test_the_handoff_files_are_never_written_with_stdlib_pickle(self):
        text = script(
            "slurm", environment="production", venv_info=dict(CONDA_VENV_INFO)
        )
        for block in self._stages(text):
            for line in block.split("\n"):
                if "pickle.dump" in line and "_ser" not in line:
                    assert "_payload" in line, line


#: Scenarios that must be untouched by #164: nothing names an environment, so
#: the generated script has to be exactly what it was before the change. The
#: goldens beside this file were produced by the pre-change generator.
REPLICATION_SCENARIOS = {
    "slurm_single_venv": ("slurm", {}),
    "ssh_single_venv": ("ssh", {}),
    "slurm_two_venv_conda": ("slurm", {"venv_info": dict(CONDA_VENV_INFO)}),
    "ssh_two_venv_conda": ("ssh", {"venv_info": dict(CONDA_VENV_INFO)}),
    "slurm_two_venv_plain": ("slurm", {"venv_info": dict(PLAIN_VENV_INFO)}),
    "ssh_two_venv_plain": ("ssh", {"venv_info": dict(PLAIN_VENV_INFO)}),
    "slurm_with_setup_lines": (
        "slurm",
        {
            "module_loads": ["python/3.11", "cuda/12.1"],
            "environment_variables": {"OMP_NUM_THREADS": "4"},
            "pre_execution_commands": ["echo hello"],
            "venv_info": dict(CONDA_VENV_INFO),
            "partition": "gpu",
        },
    ),
}


def replication_script(name):
    cluster_type, overrides = REPLICATION_SCENARIOS[name]
    overrides = dict(overrides)
    job_config = dict(BASE_JOB)
    partition = overrides.pop("partition", None)
    if partition:
        job_config["partition"] = partition
    return create_job_script(
        cluster_type, job_config, "/remote/job", make_config(**overrides)
    )


@pytest.mark.parametrize("name", sorted(REPLICATION_SCENARIOS))
def test_replication_path_is_byte_identical_without_a_named_environment(name):
    """Environment replication is verified against real hardware; do not move it.

    The golden files were generated by the generator as it stood before #164.
    A diff here means the named-environment routing leaked into the path taken
    by users who never named one.
    """
    golden = GOLDEN_DIR / f"{name}.sh"
    assert golden.exists(), f"missing golden {golden}"
    assert replication_script(name) == golden.read_text()


def test_job_execution_lines_default_is_the_replication_path():
    """Callers that pass no named environment get the old behaviour verbatim."""
    config = make_config(venv_info=dict(CONDA_VENV_INFO))
    assert job_execution_lines("/remote/job", config) == job_execution_lines(
        "/remote/job", config, None
    )
