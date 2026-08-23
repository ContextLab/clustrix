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
import re
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


def _install_conda_sh(base, marker="yes", working=True):
    """Write a fixture ``etc/profile.d/conda.sh`` under ``base``.

    A real ``conda.sh`` does one thing that matters to the code under test:
    it leaves ``conda`` usable in the shell that sourced it. The fixture that
    only set a marker variable made ``test_a_conda_in_a_usual_home_location_is
    _found_and_sourced`` pass against a fragment that could not actually run
    ``conda run`` afterwards, which is the hole N5 came through.
    """
    profile = Path(base) / "etc" / "profile.d"
    profile.mkdir(parents=True, exist_ok=True)
    body = f"CLUSTRIX_FAKE_CONDA={marker}\n"
    if working:
        body += "conda() { echo 'conda 24.1.0'; return 0; }\n"
    (profile / "conda.sh").write_text(body)
    return profile / "conda.sh"


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
    "conda_setup_prefix": ". /opt/conda/etc/profile.d/conda.sh",
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
        assert ". venv/bin/activate" in text
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
        assert ". /remote/job/venv1_serialization/bin/activate" in text

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
    # `python_executable` on the replication path: the user's setting reaches
    # the single-venv launch line and must NOT reach VENV2, which clustrix
    # built at a pinned version. Nothing pinned that before.
    "slurm_python_executable": ("slurm", {"python_executable": "python3.11"}),
    "slurm_two_venv_conda_python_executable": (
        "slurm",
        {"venv_info": dict(CONDA_VENV_INFO), "python_executable": "python3.11"},
    ),
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


class TestCondaIsUsableBeforeItIsUsed:
    """`conda run` in a batch shell needs conda initialised first.

    A SLURM (or `ssh host bash script.sh`) job runs under a non-login,
    non-interactive shell, which sources no profile script, so `conda` is
    either absent from PATH or is a wrapper that refuses to work until
    conda.sh has been sourced. Emitting `conda run -n prod python` with
    nothing before it is "conda: command not found", and it was the flagship
    path of #164 -- a named environment with `use_two_venv=False`.
    """

    @staticmethod
    def _before_conda_run(text):
        """Everything the script does before it first invokes `conda run`."""
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("conda run -n "):
                return "\n".join(lines[:i])
        raise AssertionError(f"no `conda run` line in:\n{text}")

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_a_named_environment_initialises_conda_first(self, cluster_type):
        preamble = self._before_conda_run(script(cluster_type, environment="prod"))
        assert "etc/profile.d/conda.sh" in preamble, preamble

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_the_search_covers_the_usual_installation_locations(self, cluster_type):
        preamble = self._before_conda_run(script(cluster_type, environment="prod"))
        for location in (
            "$CONDA_PREFIX",
            "conda info --base",
            "$HOME/miniconda3",
            "$HOME/anaconda3",
            "$HOME/miniforge3",
            "/opt/conda",
        ):
            assert location in preamble, (location, preamble)

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_a_named_environment_stops_the_job_when_conda_cannot_be_found(
        self, cluster_type
    ):
        """Loud and diagnosable beats `conda: command not found` three lines on.

        clustrix cannot know where a given cluster keeps conda, so the honest
        outcome when the search fails is a stop with a reason, not a job that
        dies in the middle of someone else's error message.
        """
        preamble = self._before_conda_run(script(cluster_type, environment="prod"))
        assert "exit 1" in preamble
        assert "no conda installation was found on this node" in preamble
        assert "module_loads" in preamble and "pre_execution_commands" in preamble

    def test_a_plain_two_venv_named_environment_also_initialises_conda(self):
        """VENV1 is a virtualenv here, so nothing measured a conda location.

        This is the combination that is easiest to miss: replication ran, so
        `venv_info` exists, but it found no conda, so `conda_setup_prefix` is
        absent -- and the named VENV2 still needs `conda run` to work.
        """
        text = script(
            "slurm", environment="production", venv_info=dict(PLAIN_VENV_INFO)
        )
        assert "conda run -n production" in text
        assert "etc/profile.d/conda.sh" in self._before_conda_run(text)

    def test_a_conda_location_measured_on_the_cluster_is_preferred(self):
        """A probed answer beats a blind search; the search is the fallback."""
        text = script(
            "slurm", environment="production", venv_info=dict(CONDA_VENV_INFO)
        )
        preamble = self._before_conda_run(text)
        assert ". /opt/conda/etc/profile.d/conda.sh" in preamble
        # The blind in-script search is not emitted when there is nothing to
        # search for.
        assert "_clustrix_conda_sh" not in text

    def test_replication_without_a_named_environment_is_untouched(self):
        text = script("slurm", venv_info=dict(PLAIN_VENV_INFO))
        assert "_clustrix_conda_sh" not in text
        assert "etc/profile.d/conda.sh" not in text


class TestTheEmittedShellActuallyWorks:
    """The discovery block is bash, so it is checked by running bash."""

    SYSTEM_LOCATIONS = ("/opt/conda", "/usr/local/miniconda3", "/usr/local/anaconda3")

    @staticmethod
    def _run(
        tmp_path,
        home,
        extra="",
        prologue="",
        path="/usr/bin:/bin",
        env_extra=None,
        stub_broken_conda=True,
    ):
        import subprocess

        from clustrix.utils import _conda_discovery_lines

        script_path = tmp_path / "probe.sh"
        script_path.write_text(
            prologue
            + "\n".join(_conda_discovery_lines("prod"))
            + '\necho "SOURCED=${CLUSTRIX_FAKE_CONDA:-no}"\n'
            + extra
        )
        # A pristine environment: no inherited CONDA_PREFIX, no working conda
        # on PATH, and HOME pointed at the fixture. Without this the test
        # would pass or fail according to the developer's own conda. The
        # stub is what makes "no working conda" true everywhere: GitHub
        # runners ship a conda that resolves even under /usr/bin:/bin, which
        # made _clustrix_conda_works succeed and the home search never run
        # (SOURCED=no). A conda that answers --version with a failure is the
        # one input the discovery block must treat as absent. Tests that
        # supply their own (working) conda ahead of it pass
        # stub_broken_conda=False.
        env = {"HOME": str(home), "PATH": path}
        if stub_broken_conda:
            stub_bin = tmp_path / "conda-stub-bin"
            stub_bin.mkdir(exist_ok=True)
            (stub_bin / "conda").write_text("#!/bin/sh\nexit 1\n")
            (stub_bin / "conda").chmod(0o755)
            env["PATH"] = f"{stub_bin}:{path}"
        env.update(env_extra or {})
        return subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
            env=env,
        )

    @pytest.mark.parametrize("location", ["miniconda3", "anaconda3", "miniforge3"])
    def test_a_conda_in_a_usual_home_location_is_found_and_sourced(
        self, tmp_path, location
    ):
        home = tmp_path / "home"
        _install_conda_sh(home / location, marker=location)
        result = self._run(tmp_path, home)
        assert result.returncode == 0, result.stderr
        assert f"SOURCED={location}" in result.stdout

    def test_nothing_found_stops_the_job_with_a_diagnosable_message(self, tmp_path):
        import os

        if any(
            os.path.isfile(f"{p}/etc/profile.d/conda.sh") for p in self.SYSTEM_LOCATIONS
        ):
            pytest.skip("this machine has a system-wide conda; nothing to not find")
        home = tmp_path / "empty_home"
        home.mkdir()
        result = self._run(tmp_path, home)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "no conda installation was found on this node" in result.stderr
        assert "prod" in result.stderr
        # The list of places searched is printed literally, not expanded away
        # to nothing by the shell that prints it.
        assert "$CONDA_PREFIX" in result.stderr
        # The script stopped: nothing after the block ran.
        assert "SOURCED" not in result.stdout

    def test_a_conda_already_on_path_is_left_alone(self, tmp_path):
        """The claim in the name, tested against a home that *has* a conda.

        With an empty home this asserted nothing: there was no other conda to
        prefer, so a fragment that ignored the working one entirely still
        passed. The real shape is a site that puts conda on PATH via
        ``module load`` -- whose installation directory holds no
        ``etc/profile.d/conda.sh`` -- on a user who also has ``~/miniconda3``.
        Searching before asking whether conda already works sourced the
        *user's* installation over the site's, and ``-n <name>`` then resolved
        in the wrong one.
        """
        home = tmp_path / "home_with_path_conda"
        bindir = tmp_path / "bin"
        home.mkdir()
        bindir.mkdir()
        conda = bindir / "conda"
        conda.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "--version" ]; then echo "conda 24.1.0"; fi\nexit 0\n'
        )
        conda.chmod(0o755)
        # The competing installation the old ordering preferred.
        _install_conda_sh(home / "miniconda3", marker="the_users_own")
        result = self._run(
            tmp_path,
            home,
            path=f"{bindir}:/usr/bin:/bin",
            # The working conda under test lives in `bindir`; the broken
            # stub must not shadow it.
            stub_broken_conda=False,
        )
        assert result.returncode == 0, result.stderr
        assert "SOURCED=no" in result.stdout, result.stdout

    def test_a_conda_sh_that_fails_to_source_does_not_pass_for_a_working_conda(
        self, tmp_path
    ):
        """An unreadable or truncated conda.sh must not silence the diagnostic.

        Taking the "we sourced something" branch on the strength of having
        *found* a file left the job to die at ``conda: command not found``
        (rc 127) three lines later, with none of the message below.
        """
        home = tmp_path / "home_broken_conda"
        conda_sh = _install_conda_sh(home / "miniconda3")
        conda_sh.write_text("this is not shell (((\n")
        result = self._run(tmp_path, home)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "no conda installation was found on this node" in result.stderr
        assert "SOURCED" not in result.stdout

    def test_an_unreadable_conda_sh_is_the_same_story(self, tmp_path):
        home = tmp_path / "home_unreadable_conda"
        conda_sh = _install_conda_sh(home / "miniconda3")
        conda_sh.chmod(0o000)
        try:
            result = self._run(tmp_path, home)
        finally:
            conda_sh.chmod(0o644)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "no conda installation was found on this node" in result.stderr

    def test_the_search_order_is_the_order_of_the_list(self, tmp_path):
        """$CONDA_PREFIX outranks $HOME/miniconda3, and that is semantic.

        Reordering the list is not a cosmetic change: the environment the user
        is standing in has to beat a per-user install, which has to beat a
        system-wide one. Bash decides this, so bash is asked.
        """
        home = tmp_path / "home_with_both"
        _install_conda_sh(home / "miniconda3", marker="home_miniconda")
        prefix = tmp_path / "active_prefix"
        _install_conda_sh(prefix, marker="conda_prefix")
        result = self._run(tmp_path, home, env_extra={"CONDA_PREFIX": str(prefix)})
        assert result.returncode == 0, result.stderr
        assert "SOURCED=conda_prefix" in result.stdout, result.stdout

    @pytest.mark.parametrize("options", ["set -u", "set -eu", "set -e"])
    def test_the_block_survives_a_job_that_sets_shell_options(self, tmp_path, options):
        """``pre_execution_commands`` run first, and ``set -u`` is common.

        ``"$CONDA_PREFIX"`` unguarded made the block exit 1 with "unbound
        variable" before it looked anywhere -- on a node that had conda.
        """
        home = tmp_path / "home_setu"
        _install_conda_sh(home / "miniconda3", marker="found_under_" + options[-1])
        result = self._run(tmp_path, home, prologue=options + "\n")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "SOURCED=found_under_" in result.stdout

    def test_the_block_survives_shellopts_inherited_from_the_site(self, tmp_path):
        """``SHELLOPTS=nounset`` in the environment is inherited by bash."""
        home = tmp_path / "home_shellopts"
        _install_conda_sh(home / "miniconda3", marker="inherited")
        result = self._run(tmp_path, home, env_extra={"SHELLOPTS": "nounset"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "SOURCED=inherited" in result.stdout

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_the_whole_generated_script_is_valid_bash(self, tmp_path, cluster_type):
        import subprocess

        script_path = tmp_path / "job.sh"
        script_path.write_text(script(cluster_type, environment="prod"))
        result = subprocess.run(
            ["bash", "-n", str(script_path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr


class TestThePythonExecutableIsNotDiscarded:
    """`python_executable` is honoured on the replication path; honour it here.

    Accepting a setting and discarding it is the defect #164 itself was, so
    the fix must not reintroduce it one line further along.
    """

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_a_named_environment_runs_the_configured_interpreter(self, cluster_type):
        text = script(cluster_type, environment="prod", python_executable="python3.11")
        assert "conda run -n prod python3.11 -c" in text

    def test_a_named_two_venv_execution_environment_honours_it_too(self):
        text = script(
            "slurm",
            environment="prod",
            python_executable="python3.11",
            venv_info=dict(CONDA_VENV_INFO),
        )
        assert "conda run -n prod python3.11 -c" in text

    def test_the_serialization_environment_keeps_clustrix_own_interpreter(self):
        """VENV1 must stay on the version dill was pinned to, whatever the user set."""
        text = script(
            "slurm",
            environment="prod",
            python_executable="python3.11",
            venv_info=dict(CONDA_VENV_INFO),
        )
        assert "conda run -n clustrix_venv1_abc123 python -c" in text
        assert "conda run -n clustrix_venv1_abc123 python3.11" not in text

    def test_the_replicated_execution_environment_keeps_it_too(self):
        """No named environment: VENV2 is clustrix's own, pinned build."""
        text = script(
            "slurm", python_executable="python3.11", venv_info=dict(CONDA_VENV_INFO)
        )
        assert "conda run -n clustrix_venv2_abc123 python -c" in text
        assert "python3.11" not in text


class TestAnEnvironmentNameIsNotAnOptionFlag:
    """`conda run -n <name>` is an argument position, so `-` is not a name.

    `validate_shell_fragment`'s allowlist contains `-` because scheduler
    directives need it, which let `environment="--no-capture-output"` through:
    `conda run -n --no-capture-output python` parses the value as an option,
    and the job then runs somewhere other than where the user asked, quietly.
    """

    @pytest.mark.parametrize(
        "flag", ["--no-capture-output", "-n", "-p", "--name", "-name", "--live-stream"]
    )
    def test_a_name_that_is_an_option_is_refused(self, flag):
        with pytest.raises(ValueError) as excinfo:
            script("slurm", environment=flag)
        assert "conda_env_name" in str(excinfo.value)
        assert "-" in str(excinfo.value)

    @pytest.mark.parametrize("flag", ["--no-capture-output", "-n", "-p"])
    def test_the_two_venv_path_refuses_it_too(self, flag):
        with pytest.raises(ValueError):
            script("slurm", environment=flag, venv_info=dict(CONDA_VENV_INFO))

    def test_a_name_containing_a_dash_is_still_fine(self):
        assert "conda run -n py3-11-torch" in script(
            "slurm", environment="py3-11-torch"
        )

    def test_an_absurdly_long_name_is_refused(self):
        with pytest.raises(ValueError) as excinfo:
            script("slurm", environment="e" * 5000)
        assert "conda_env_name" in str(excinfo.value)
        assert "255" in str(excinfo.value)

    def test_the_longest_plausible_name_is_accepted(self):
        name = "e" * 255
        assert f"conda run -n {name} " in script("slurm", environment=name)


class TestTheSilentBehaviourChangeIsAnnounced:
    """`conda_env_name` was inert for its whole life; now it reroutes jobs.

    An old ``~/.clustrix/clustrix.yml`` can still carry a value nobody has
    thought about, and honouring it without a word is the same silence #164
    was filed about, one level up.
    """

    @staticmethod
    def _notices(caplog):
        return [
            r.getMessage()
            for r in caplog.records
            if "conda_env_name" in r.getMessage() and "now honoured" in r.getMessage()
        ]

    @pytest.fixture(autouse=True)
    def _forget_previous_announcements(self):
        from clustrix.utils import _CONDA_ENV_NAME_MIGRATION_ANNOUNCED

        _CONDA_ENV_NAME_MIGRATION_ANNOUNCED.clear()
        yield
        _CONDA_ENV_NAME_MIGRATION_ANNOUNCED.clear()

    def test_the_config_field_announces_itself(self, caplog):
        with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
            script("slurm", conda_env_name="legacy")
        notices = self._notices(caplog)
        assert notices, [r.getMessage() for r in caplog.records]
        assert "'legacy'" in notices[0]
        assert "previously accepted and never used" in notices[0]

    def test_it_is_announced_once_per_process_not_once_per_job(self, caplog):
        """A notice repeated on every submission is noise, and noise goes unread."""
        with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
            for _ in range(3):
                script("slurm", conda_env_name="legacy")
        assert len(self._notices(caplog)) == 1, self._notices(caplog)

    def test_a_per_call_environment_is_not_a_migration(self, caplog):
        """`@cluster(environment=...)` is a decision made today, not a leftover."""
        with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
            script("slurm", environment="chosen_now")
        assert not self._notices(caplog)

    def test_naming_nothing_announces_nothing(self, caplog):
        with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
            script("slurm", venv_info=dict(CONDA_VENV_INFO))
        assert not self._notices(caplog)

    def test_a_per_call_environment_that_agrees_with_the_field_is_not_a_migration(
        self, caplog
    ):
        """The two spellings can name the same environment and still differ.

        ``@cluster(environment="prod")`` under a config that also says
        ``conda_env_name="prod"`` is a decision made today which happens to
        agree with the file -- not a value nobody has read since. Announcing
        it points the user at a setting that had no part in the choice, and
        the notice fires once per process, so the one that matters is then
        suppressed for the rest of the run.
        """
        with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
            script("slurm", environment="same", conda_env_name="same")
        assert not self._notices(caplog), self._notices(caplog)

    def test_the_config_field_still_announces_after_that(self, caplog):
        """The suppression above is per call, not a way to lose the notice."""
        with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
            script("slurm", environment="same", conda_env_name="same")
            script("slurm", conda_env_name="same")
        assert len(self._notices(caplog)) == 1, self._notices(caplog)

    def test_the_decorator_passes_only_the_per_call_value(self):
        """The distinction above only survives if decorator.py keeps it.

        ``decorator.py`` used to write ``environment or config.conda_env_name``
        into ``job_config["environment"]``, which erased the difference before
        ``resolve_named_environment`` could see it. The fallback belongs in
        one place, and that place already has it.
        """
        import inspect

        from clustrix.decorator import cluster

        source = inspect.getsource(cluster)
        assert '"environment": environment,' in source
        assert "environment or config.conda_env_name" not in source


class TestSetupEnvironmentValidatesTheNameToo:
    """The second interpolation site for the same user input (utils.py:1168).

    #164 blessed ``conda_env_name`` as user input on the job-script path and
    left this one interpolating it bare. It is exported from ``__init__``.
    """

    def test_a_hostile_name_is_refused(self):
        from clustrix.utils import setup_environment

        config = make_config(conda_env_name="ev'il; touch /tmp/pwn")
        with pytest.raises(ValueError) as excinfo:
            setup_environment("/work", {}, config)
        assert "conda_env_name" in str(excinfo.value)

    def test_an_option_flag_is_refused(self):
        from clustrix.utils import setup_environment

        config = make_config(conda_env_name="--no-capture-output")
        with pytest.raises(ValueError):
            setup_environment("/work", {}, config)

    def test_an_ordinary_name_is_quoted(self):
        from clustrix.utils import setup_environment

        config = make_config(conda_env_name="prod")
        assert setup_environment("/work", {}, config) == "conda run -n prod python"

    def test_a_blank_name_is_not_an_environment_called_nothing(self):
        from clustrix.utils import setup_environment

        config = make_config(conda_env_name="   ")
        assert "conda run -n" not in setup_environment("/work", {}, config)


class TestTheSchedulerDoesNotFeedVenv1sPythonToVenv2:
    """The round-two regression: `venv2_python` got an overwritten value.

    ``executor_schedulers`` used to write ``venv_info["venv1_python"]`` over
    ``config.python_executable`` after a successful two-venv setup. Once #164
    made ``python_executable`` reach VENV2, that overwrite turned the
    *default* path into the defect the issue is about, and it is reproduced
    here through the real seam the scheduler goes through -- not by setting
    the field by hand, which is what let it past round two's tests.
    """

    @staticmethod
    def _script(venv_info, **overrides):
        from clustrix.executor_schedulers import config_for_job_script

        config = make_config(**overrides)
        job_config = dict(BASE_JOB, environment="prod")
        return (
            create_job_script(
                "slurm",
                job_config,
                "/remote/job",
                config_for_job_script(config, dict(venv_info)),
            ),
            config,
        )

    def test_a_conda_two_venv_job_runs_a_python_not_a_quoted_sentence(self):
        """`conda run -n prod 'conda run -n clustrix_venv1_x python' -c "`.

        One shell word in the executable position: the job cannot start.
        """
        text, _ = self._script(CONDA_VENV_INFO)
        assert (
            "conda run -n prod 'conda run -n clustrix_venv1_abc123 python' -c \""
            not in text
        )
        assert 'conda run -n prod python -c "' in text

    def test_a_plain_two_venv_job_does_not_execute_in_the_serialization_venv(self):
        """`conda run -n prod /job/venv1_serialization/bin/python -c "`.

        This one *runs*, which is worse: the user's function executes under
        clustrix's serialization venv rather than the environment they named,
        and nothing says so.
        """
        text, _ = self._script(PLAIN_VENV_INFO)
        assert (
            "/remote/job/venv1_serialization/bin/python -c"
            not in text.split("# Step 2")[1]
        )
        assert 'conda run -n prod python -c "' in text

    @pytest.mark.parametrize("venv_info", [CONDA_VENV_INFO, PLAIN_VENV_INFO])
    def test_the_configured_interpreter_is_the_one_that_reaches_venv2(self, venv_info):
        text, _ = self._script(venv_info, python_executable="python3.11")
        assert 'conda run -n prod python3.11 -c "' in text

    @pytest.mark.parametrize("venv_info", [CONDA_VENV_INFO, PLAIN_VENV_INFO])
    def test_the_setup_leaves_the_users_setting_where_it_found_it(self, venv_info):
        """`config` is the process-wide singleton; the overwrite outlived the job.

        The next submission's ``resolve_remote_python`` then read VENV1's
        interpreter as if the user had configured it.
        """
        _, config = self._script(venv_info, python_executable="python3.11")
        assert config.python_executable == "python3.11"

    @pytest.mark.parametrize("venv_info", [CONDA_VENV_INFO, PLAIN_VENV_INFO])
    def test_the_resulting_script_is_valid_bash(self, tmp_path, venv_info):
        import subprocess

        text, _ = self._script(venv_info)
        path = tmp_path / "job.sh"
        path.write_text(text)
        result = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_venv_info_is_the_only_field_the_setup_writes(self):
        """Anything else it writes is a user setting it has no business owning."""
        import inspect

        from clustrix.executor_schedulers import config_for_job_script

        source = inspect.getsource(config_for_job_script)
        assignments = [
            line.strip()
            for line in source.split("\n")
            if line.strip().startswith("config.") and "=" in line
        ]
        assert assignments == ["config.venv_info = venv_info"], assignments


class _NoConnection:
    """A connection manager with no SSH client.

    Not a stand-in for one. The point of the tests below is that the skip
    path must not reach for a connection at all; when it does,
    ``setup_remote_environment`` calls ``None.exec_command`` and the test
    sees a real ``AttributeError`` instead of a green assertion about a fake.
    """

    ssh_client = None


class TestANamedEnvironmentDoesNotPayForOneItWillNotUse:
    """`use_two_venv=False` + a named environment built a venv for nothing.

    The generated script runs `conda run -n <name>` and never sources
    `venv/bin/activate`, so replicating the whole local environment onto the
    cluster first was a pip install whose only effect was to make every
    submission slower. This is the flagship case of #164.
    """

    @staticmethod
    def _manager(**overrides):
        from clustrix.executor_schedulers import SchedulerManager

        return SchedulerManager(
            make_config(use_two_venv=False, **overrides), _NoConnection()
        )

    FUNC_DATA = {"requirements": {"dill": "0.3.8"}}

    def test_a_named_environment_skips_the_build_entirely(self):
        manager = self._manager()
        config = manager._setup_job_environment(
            "/remote/job", dict(self.FUNC_DATA), "prod"
        )
        assert config.venv_info is None

    def test_without_one_the_build_still_happens(self):
        """The negative above has to be a skip, not a build that does nothing.

        With no environment named, the same call reaches for the SSH
        connection there is none of, and says so about the remote host.

        "Says so about the remote host" is checked as *naming* that host, not
        as containing the word "remote". The probe used to swallow its own
        failure and return ``False``, which sent the caller into a message
        stating flatly that no matching interpreter exists on the cluster --
        a confident claim about a machine clustrix never managed to ask
        (#123). That message now names ``cluster_host`` directly and only
        falls back to the literal "the remote host" when the field is empty,
        so an assertion on the bare word passed for a reason that has since
        stopped being true. Both spellings are accepted here because both
        satisfy the requirement this test exists for: the reader is told
        which end failed.
        """
        manager = self._manager()
        with pytest.raises((AttributeError, RuntimeError)) as excinfo:
            manager._setup_job_environment("/remote/job", dict(self.FUNC_DATA), None)
        message = str(excinfo.value)
        assert (
            manager.config.cluster_host in message or "remote" in message.lower()
        ), excinfo.value

    def test_the_two_venv_branch_still_builds_and_says_why(self):
        """VENV1 is clustrix's own serialization venv and is still required.

        Only VENV2 is replaced by the named environment, so the build cannot
        simply be skipped there. `job_execution_lines` warns instead, naming
        `use_two_venv=False` as the way out -- which is the branch above.
        """
        import inspect

        from clustrix.utils import job_execution_lines

        source = inspect.getsource(job_execution_lines)
        assert "Set use_two_venv=False if you do not want it built." in source


class TestTheNameRulesAreCondasNotTheDirectiveAllowlists:
    """`conda run -n <name>` is not a scheduler directive.

    Reusing `validate_shell_fragment`'s allowlist -- which exists to keep
    shell syntax out of an *unquoted* `#SBATCH` line -- refused environments
    conda creates happily, and the two are not the same question.
    """

    from clustrix.utils import validate_environment_name as _validate

    @pytest.mark.parametrize(
        "name",
        ["análisis", "环境", "env(1)", "env[1]", "my~env", "a&b", "x!y", "µ-env"],
    )
    def test_a_name_conda_accepts_is_accepted(self, name):
        from clustrix.utils import validate_environment_name

        assert validate_environment_name("conda_env_name", name) == name

    @pytest.mark.parametrize("name", ["análisis", "env(1)", "a&b", "x!y"])
    def test_such_a_name_reaches_the_script_quoted(self, name):
        assert f"conda run -n {shlex.quote(name)} python -c" in script(
            "slurm", environment=name
        )

    @pytest.mark.parametrize("name", ["env(1)", "a&b", "x!y", "my~env"])
    def test_and_the_script_is_still_valid_bash(self, tmp_path, name):
        """Quoting is what makes these safe, so it is checked by running bash."""
        import subprocess

        path = tmp_path / "job.sh"
        path.write_text(script("slurm", environment=name))
        result = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("name", ["p:rod", "pr#od", "a b", "a\tb", "e\x00v"])
    def test_a_name_conda_itself_refuses_is_refused(self, name):
        from clustrix.utils import validate_environment_name

        with pytest.raises(ValueError):
            validate_environment_name("conda_env_name", name)

    @pytest.mark.parametrize(
        "path", ["/scratch/envs/prod", "envs/prod", "./prod", "/", "."]
    )
    def test_a_prefix_environment_is_refused_here_not_on_the_compute_node(self, path):
        """clustrix emits `-n`, never `-p`; a path was accepted and then failed.

        Accept-then-fail put the error on the cluster, after the job was
        queued, where it read as the cluster's fault.
        """
        from clustrix.utils import validate_environment_name

        with pytest.raises(ValueError) as excinfo:
            validate_environment_name("conda_env_name", path)
        message = str(excinfo.value)
        assert "conda_env_name" in message
        assert "-p" in message or "directory reference" in message

    def test_dot_dot_is_not_an_environment(self):
        from clustrix.utils import validate_environment_name

        with pytest.raises(ValueError):
            validate_environment_name("conda_env_name", "..")

    def test_an_empty_name_is_refused_by_the_validator_itself(self):
        """Callers stop first, but the validator must not bless `conda run -n ''`.

        Removing this check leaves an empty name silently valid, and the only
        thing standing between that and a job is whichever caller remembered
        to strip and test the string.
        """
        from clustrix.utils import validate_environment_name

        with pytest.raises(ValueError) as excinfo:
            validate_environment_name("conda_env_name", "")
        assert "empty" in str(excinfo.value)


def test_the_conda_search_order_is_pinned():
    """Order is semantics here; see `_CONDA_SEARCH_LOCATIONS`.

    A system-wide `/opt/conda` must not outrank the environment the user is
    standing in, and a per-user install must not outrank either. Nothing else
    in the file records that, so it is recorded here.
    """
    from clustrix.utils import _CONDA_SEARCH_LOCATIONS

    assert [human for _, human in _CONDA_SEARCH_LOCATIONS] == [
        "$CONDA_PREFIX",
        "$(conda info --base)",
        "$HOME/miniconda3",
        "$HOME/anaconda3",
        "$HOME/miniforge3",
        "/opt/conda",
        "/usr/local/miniconda3",
        "/usr/local/anaconda3",
    ]


def test_every_parameter_expansion_in_the_search_is_guarded():
    """`set -u` is one `pre_execution_commands` line away, and it was fatal."""
    from clustrix.utils import _CONDA_SEARCH_WORDS

    assert "$CONDA_PREFIX" not in _CONDA_SEARCH_WORDS.replace("${CONDA_PREFIX:-}", "")
    assert "$HOME" not in _CONDA_SEARCH_WORDS.replace("${HOME:-}", "")


def test_conda_info_base_cannot_hang_the_job_or_be_defeated_by_a_warning(tmp_path):
    """It had no timeout and took whatever conda printed, warnings included."""
    import subprocess

    from clustrix.utils import _CONDA_SHELL_HELPERS

    bindir = tmp_path / "bin"
    bindir.mkdir()
    conda = bindir / "conda"
    conda.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "info" ]; then\n'
        '  echo "==> WARNING: A newer version of conda exists. <=="\n'
        '  echo "/real/conda/base"\n'
        '  echo "/second/line"\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    conda.chmod(0o755)
    script_path = tmp_path / "probe.sh"
    script_path.write_text(
        "\n".join(_CONDA_SHELL_HELPERS) + '\necho "BASE=$(_clustrix_conda_base)"\n'
    )
    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "BASE=/real/conda/base", result.stdout


@pytest.mark.parametrize(
    "printed",
    [
        # A conda wrapper that came off a Windows checkout, or output piped
        # through a tool that keeps CRLF. `[ -f "/real/conda/base\r/etc/..." ]`
        # is false, so the entry lost to whatever came after it in the list.
        "/real/conda/base\r\n",
        "   /real/conda/base\n",
        "/real/conda/base   \n",
        "\t/real/conda/base\t\r\n",
    ],
)
def test_conda_info_base_output_is_stripped_of_cr_and_surrounding_space(
    tmp_path, printed
):
    """Whatever conda decorates the line with, the answer is the path.

    Not silent -- the entry falls through to the next candidate loudly enough
    to end in the diagnostic -- but wrong, and wrong in a way that sends the
    job to a different conda installation than the one it asked.
    """
    import subprocess

    from clustrix.utils import _CONDA_SHELL_HELPERS

    bindir = tmp_path / "bin"
    bindir.mkdir()
    conda = bindir / "conda"
    conda.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "info" ]; then\n'
        f"  printf '%s' {shlex.quote(printed)}\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    conda.chmod(0o755)
    script_path = tmp_path / "probe.sh"
    script_path.write_text(
        "\n".join(_CONDA_SHELL_HELPERS) + '\necho "BASE=[$(_clustrix_conda_base)]"\n'
    )
    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "BASE=[/real/conda/base]", repr(result.stdout)


class TestTheNamedEnvironmentVersionGuard:
    """A named environment on the wrong Python minor version must be refused.

    dill embeds CPython bytecode, and that bytecode does not load across minor
    versions. Every other path refuses this before the job runs;
    ``_select_remote_python`` at submit time, and both conda environments
    pinned to the local version by ``setup_two_venv_environment``. The named
    path had that check only as a side effect of the environment replication
    it now skips, so for a while it had none at all.

    Run as real bash against a real interpreter, because the guard is shell
    wrapping a ``python -c`` and the interesting part is whether the exit
    status reaches the job.
    """

    @staticmethod
    def _run(tmp_path, remote_version):
        """Run the guard with a fake ``conda`` that runs a chosen Python."""
        import subprocess
        import sys

        from clustrix.utils import named_environment_version_guard

        bindir = tmp_path / "bin"
        bindir.mkdir()
        # `conda run -n prod python -c "..."` -> run the body under a Python
        # that reports `remote_version`, whatever this interpreter is.
        shim = bindir / "fakepython"
        shim.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"sys.version_info = tuple({remote_version!r}) + (0, 'final', 0)\n"
            "exec(sys.argv[2])\n"
        )
        shim.chmod(0o755)
        conda = bindir / "conda"
        conda.write_text(
            "#!/bin/bash\n"
            "# conda run -n <name> <python> -c <body>: drop the first four\n"
            "shift 4\n"
            f'exec {shim} "$@"\n'
        )
        conda.chmod(0o755)
        script = tmp_path / "guard.sh"
        script.write_text(
            "\n".join(named_environment_version_guard("prod", "python"))
            + "\necho REACHED_THE_JOB\n"
        )
        return subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            env={"HOME": str(tmp_path), "PATH": f"{bindir}:/usr/bin:/bin"},
        )

    def test_a_matching_minor_version_lets_the_job_through(self, tmp_path):
        import sys

        result = self._run(tmp_path, list(sys.version_info[:2]))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "REACHED_THE_JOB" in result.stdout

    def test_a_different_minor_version_stops_the_job(self, tmp_path):
        import sys

        other = [sys.version_info.major, sys.version_info.minor + 1]
        result = self._run(tmp_path, other)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "REACHED_THE_JOB" not in result.stdout, (
            "the guard printed a diagnostic and then let the job run anyway; "
            "the failure it prevents is silent, so this has to stop the job"
        )

    def test_the_diagnostic_names_both_versions_and_the_way_out(self, tmp_path):
        import sys

        other = [sys.version_info.major, sys.version_info.minor + 1]
        result = self._run(tmp_path, other)
        message = result.stderr
        assert f"{sys.version_info.major}.{sys.version_info.minor}" in message
        assert f"{other[0]}.{other[1]}" in message
        assert "prod" in message
        assert "environment=" in message and "conda_env_name=" in message

    @pytest.mark.parametrize("cluster_type", SCHEDULERS)
    def test_the_guard_is_emitted_on_both_named_branches(self, cluster_type):
        single = script(cluster_type, environment="prod")
        two_venv = script(
            cluster_type, environment="prod", venv_info=dict(CONDA_VENV_INFO)
        )
        for text in (single, two_venv):
            assert "_got = sys.version_info[:2]" in text, text

    def test_the_guard_is_not_emitted_when_no_environment_is_named(self):
        assert "_got = sys.version_info[:2]" not in script("slurm")
        assert "_got = sys.version_info[:2]" not in script(
            "slurm", venv_info=dict(CONDA_VENV_INFO)
        )


class TestAPathIsRefusedAtConfigurationTime:
    """Where #164 said the refusal happens, and where it actually happened.

    ``conda_env_name`` was validated only by ``resolve_named_environment``, at
    submission -- by which point the job directory exists on the cluster, the
    result-signing key has been written into it and the pickled function has
    been uploaded. The commit that introduced the validation claimed it
    happened "at configuration time". Now it does.
    """

    BAD = ["/scratch/envs/prod", "p:rod", ".", "..", "--no-capture-output", "a b"]

    @pytest.mark.parametrize("bad", BAD)
    def test_configure_refuses_it(self, bad):
        from clustrix.config import configure

        with pytest.raises(ValueError, match="conda_env_name"):
            configure(conda_env_name=bad)

    @pytest.mark.parametrize("bad", BAD)
    def test_the_constructor_refuses_it(self, bad):
        with pytest.raises(ValueError, match="conda_env_name"):
            ClusterConfig(conda_env_name=bad)

    @pytest.mark.parametrize("bad", BAD)
    def test_a_configuration_file_refuses_it(self, tmp_path, bad):
        """And the refusal says which file the bad name came out of.

        ``load_config`` validates before it builds the ``ClusterConfig``, and
        the construction would raise on its own -- so the only thing the
        earlier call adds is the ``source`` it passes, which names the file.
        Unasserted, that call is indistinguishable from redundant, and the
        next reader deletes it and leaves a user with a config directory of
        several files and a message that names none of them.
        """
        import json

        from clustrix.config import load_config

        path = tmp_path / "clustrix.yml"
        path.write_text(json.dumps({"cluster_type": "slurm", "conda_env_name": bad}))
        with pytest.raises(ValueError, match="conda_env_name") as raised:
            load_config(str(path))
        assert str(path) in str(raised.value), (
            "the refusal does not name the configuration file it came from: "
            f"{raised.value}"
        )

    def test_a_real_name_is_still_accepted_everywhere(self, tmp_path):
        import json

        from clustrix.config import configure, get_config, load_config

        configure(conda_env_name="prod")
        assert get_config().conda_env_name == "prod"
        assert ClusterConfig(conda_env_name="análisis").conda_env_name == "análisis"
        path = tmp_path / "clustrix.json"
        path.write_text(json.dumps({"conda_env_name": "prod"}))
        load_config(str(path))
        assert get_config().conda_env_name == "prod"

    def test_leaving_it_unset_is_not_an_error(self):
        from clustrix.config import configure

        configure(conda_env_name=None)
        assert ClusterConfig().conda_env_name is None


#: Scenarios that exist *because* of #164: every one of them names an
#: environment, so every one of them takes a branch the replication goldens
#: above can never reach. Those goldens prove the old path is unchanged and
#: nothing whatever about the new code -- the round-two regression (VENV1's
#: interpreter fed to VENV2) sat in a line no golden contained.
#:
#: Between them these cover: the single-venv named path, the two-venv named
#: path, `venv2_python`, the in-script conda discovery block, a *measured*
#: conda prefix with a named environment, and the `conda_env_name` route that
#: raises the migration notice.
NAMED_SCENARIOS = {
    "slurm_named_single_venv": ("slurm", {"environment": "prod"}, {}),
    "ssh_named_single_venv": ("ssh", {"environment": "prod"}, {}),
    "slurm_named_python_executable": (
        "slurm",
        {"environment": "prod"},
        {"python_executable": "python3.11"},
    ),
    "slurm_named_two_venv_conda": (
        "slurm",
        {"environment": "prod"},
        {"venv_info": dict(CONDA_VENV_INFO)},
    ),
    "ssh_named_two_venv_conda": (
        "ssh",
        {"environment": "prod"},
        {"venv_info": dict(CONDA_VENV_INFO)},
    ),
    "slurm_named_two_venv_conda_python_executable": (
        "slurm",
        {"environment": "prod"},
        {"venv_info": dict(CONDA_VENV_INFO), "python_executable": "python3.11"},
    ),
    "slurm_named_two_venv_plain": (
        "slurm",
        {"environment": "prod"},
        {"venv_info": dict(PLAIN_VENV_INFO)},
    ),
    # The SSH half of the plain two-venv named path had no golden at all, and
    # it is the one shape where VENV2 being handed VENV1's interpreter reads
    # as an ordinary path rather than as a nested `conda run`.
    "ssh_named_two_venv_plain": (
        "ssh",
        {"environment": "prod"},
        {"venv_info": dict(PLAIN_VENV_INFO)},
    ),
    "slurm_named_via_config": ("slurm", {}, {"conda_env_name": "legacy"}),
    "slurm_named_with_setup_lines": (
        "slurm",
        {"environment": "prod", "partition": "gpu"},
        {
            "module_loads": ["anaconda"],
            "environment_variables": {"OMP_NUM_THREADS": "4"},
            "pre_execution_commands": ["set -u"],
        },
    ),
}


def named_script(name):
    cluster_type, job_overrides, config_overrides = NAMED_SCENARIOS[name]
    job_config = dict(BASE_JOB, **job_overrides)
    return create_job_script(
        cluster_type,
        job_config,
        "/remote/job",
        make_config(**dict(config_overrides)),
    )


#: The one line in a named job script that depends on which interpreter
#: generated it. The guard has to name the *submitting* version -- that is the
#: version the dill payload's bytecode is locked to -- so a golden committed
#: from 3.12 would fail for a contributor on 3.11 for no reason at all. Both
#: sides of the comparison are normalised through this and through nothing
#: else, so every other byte is still pinned exactly; the value itself is
#: asserted separately by
#: ``test_the_version_guard_names_the_submitting_interpreter``.
_LOCAL_PY_LITERAL = re.compile(r"^_want = \(\d+, \d+\)$", re.M)


def _normalise_local_python(text):
    return _LOCAL_PY_LITERAL.sub("_want = (LOCAL_MAJOR, LOCAL_MINOR)", text)


@pytest.mark.parametrize("name", sorted(NAMED_SCENARIOS))
def test_the_named_environment_branches_are_pinned_byte_for_byte(name):
    golden = GOLDEN_DIR / f"{name}.sh"
    assert golden.exists(), f"missing golden {golden}"
    assert _normalise_local_python(named_script(name)) == _normalise_local_python(
        golden.read_text()
    )


@pytest.mark.parametrize("name", sorted(NAMED_SCENARIOS))
def test_the_version_guard_names_the_submitting_interpreter(name):
    """What the normalisation above deliberately does not check.

    dill's payload carries the bytecode of the interpreter that wrote it, so
    the version the guard demands is this process's, not the golden's.
    """
    import sys

    want = f"_want = ({sys.version_info.major}, {sys.version_info.minor})"
    assert want in named_script(name), (
        f"the named job script does not demand this interpreter's version: "
        f"expected {want!r}"
    )


@pytest.mark.parametrize("name", sorted(NAMED_SCENARIOS))
def test_every_named_golden_is_valid_bash(tmp_path, name):
    """A byte-for-byte match with a broken script is not worth much."""
    import subprocess

    path = tmp_path / "job.sh"
    path.write_text((GOLDEN_DIR / f"{name}.sh").read_text())
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_no_named_golden_runs_venv1s_interpreter_as_venv2s():
    """The round-two regression, asserted across every named golden at once."""
    for name in NAMED_SCENARIOS:
        text = (GOLDEN_DIR / f"{name}.sh").read_text()
        for line in text.split("\n"):
            if line.startswith("conda run -n ") and line.endswith(' -c "'):
                # `conda run -n prod 'conda run -n clustrix_venv1_x python'`
                assert line.count("conda run") == 1, (name, line)
                # `conda run -n prod /job/venv1_serialization/bin/python`
                assert "venv1_serialization" not in line, (name, line)
