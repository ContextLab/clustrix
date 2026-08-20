"""What a *submission* emits, pinned at the seam that actually ships (#164).

Everything else about the named-environment work is tested by calling
``create_job_script`` with a config arranged by hand. That leaves the three
functions a real job actually goes through -- ``SchedulerManager.
_setup_job_environment``, ``submit_slurm_job`` and ``submit_ssh_job`` -- with
no test at all, and round three found three surviving mutants living in
exactly that gap:

* **M13.** Re-introducing ``config.python_executable = venv_info["venv1_python"]``
  four lines further up, inside ``_setup_job_environment``, restores the
  round-two regression verbatim -- ``conda run -n prod 'conda run -n
  clustrix_venv1_x python' -c "``, one quoted word in the executable position
  and unrunnable -- and leaves the whole suite green. It also outlives the
  submission, because ``config`` is a process-wide singleton.
* **M3.** ``return config_for_job_script(self.config, None)`` on the two-venv
  *success* branch throws the measured layout away, and the script silently
  drops to the single-venv shape.
* **M7.** ``submit_ssh_job`` dropping the ``named_env`` argument puts a full
  environment replication back on the front of every named-environment SSH
  job.

None of the three is a fact about ``create_job_script``, so no test of
``create_job_script`` can see any of them. These tests drive a real
submission instead, and assert the property that matters rather than the seam
that happens to carry it: **whatever a job script ends up containing, VENV2's
interpreter is never VENV1's.**

Substrate, stated plainly, so that a pass here is not read as more than it is.
The SSH server is real (``tests/ssh_server.py``): a real socket, a real
handshake, a real SFTP subsystem, and a real shell running real commands
against real files. The shipped ``ConnectionManager`` and ``SchedulerManager``
run unmodified, and the job script that is asserted on is the one really
uploaded to the "cluster". Two commands on that host are fixtures, because
this machine has neither a SLURM controller nor a conda installation it may
write to:

* ``conda`` -- a shell function defined by a fixture ``etc/profile.d/conda.sh``
  in the account's home, which is the shape ``test_named_environment.py``
  already uses and, at many real sites, the only shape conda has.
* ``sbatch`` -- a script on ``PATH`` that prints what sbatch prints.

So these prove what clustrix emits and executes, not that conda or SLURM
behave as the fixtures do. Nothing here is a mock object, no mocking library
is imported (deliberately not naming the module, so that the repo's own count
of modules that do is not inflated by this sentence), and the code under test
cannot tell it is being tested.

``PATH`` is curated rather than inherited, and that is load-bearing rather
than tidy. With the developer's own ``PATH``, the conda search finds the
developer's own conda -- and the first draft of this file really did create
two ``clustrix_venv*`` environments inside a real ``~/opt/anaconda3``. The
fixture asserts that no conda is reachable before it puts its own there.
"""

import os
import pathlib
import re
import stat

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_core import ClusterExecutor
from clustrix.utils import serialize_function
from tests.ssh_server import LocalSSHServer

PASSWORD = "submission-invariants"

#: PATH for the "cluster" account. Deliberately not ``os.environ["PATH"]``:
#: on a developer machine that leads clustrix straight to the developer's own
#: conda installation, where it will happily run ``conda create``.
SAFE_PATH_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

#: Stands in for a conda installation. Defines ``conda`` as a shell function,
#: which is what a real ``conda.sh`` does and why clustrix has to source it at
#: all. ``env list`` reports no environments, so every run builds rather than
#: taking the reuse shortcut.
CONDA_SH = """\
conda() {
    printf '%s\\n' "$*" >> "$HOME/conda-calls.log"
    case "$1 $2" in
        "--version "*) echo "conda 24.1.0"; return 0 ;;
        "info --base") echo "$HOME/miniconda3"; return 0 ;;
        "env list") echo "# conda environments:"; return 0 ;;
    esac
    return 0
}
"""

SBATCH = """\
#!/bin/sh
echo "Submitted batch job 4242"
"""


def _account(tmp_path, with_conda=True):
    """A home directory shaped like a login account on a cluster."""
    root = tmp_path / "cluster"
    (root / "bin").mkdir(parents=True)
    sbatch = root / "bin" / "sbatch"
    sbatch.write_text(SBATCH)
    sbatch.chmod(sbatch.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if with_conda:
        profile = root / "miniconda3" / "etc" / "profile.d"
        profile.mkdir(parents=True)
        (profile / "conda.sh").write_text(CONDA_SH)
    return root


def _assert_no_other_conda(server):
    """Fail loudly if anything but the fixture's conda is reachable.

    Not a nicety. ``setup_two_venv_environment`` runs ``conda create`` in
    whatever conda it finds, and a leaked ``PATH`` makes that the machine's
    real one.
    """
    _, stdout, _ = server_exec(server, "command -v conda || true")
    assert not stdout.strip(), (
        "a conda outside this fixture is reachable from the test account "
        f"({stdout.strip()!r}); clustrix would run `conda create` in it"
    )


def server_exec(server, command):
    """Run a command on the server through the shipped connection code.

    Deliberately not a raw ``paramiko.SSHClient``: installing a host key
    policy anywhere but ``clustrix/ssh_security.py`` is forbidden repo-wide
    (``tests/unit/test_no_autoadd_policy.py``), and going through
    ``ConnectionManager`` means this helper reaches the "cluster" exactly the
    way a submission does.
    """
    from clustrix.executor_connections import ConnectionManager

    manager = ConnectionManager(_config(server, "ssh"))
    try:
        manager.connect()
        stdout, stderr = manager.execute_remote_command(command)
        return None, stdout, stderr
    finally:
        manager.disconnect()


@pytest.fixture
def cluster(tmp_path):
    """A running SSH server whose account has the fixture conda and sbatch."""
    root = _account(tmp_path)
    path = os.pathsep.join((str(root / "bin"),) + SAFE_PATH_DIRS)
    with LocalSSHServer(
        root=str(root), password=PASSWORD, env={"PATH": path}
    ) as server:
        _assert_no_other_conda(server)
        yield server


def _config(server, cluster_type, **overrides):
    config = ClusterConfig(
        cluster_type=cluster_type,
        cluster_host=server.host,
        cluster_port=server.port,
        username="tester",
        password=PASSWORD,
        ssh_host_key_policy="auto_add",
        remote_work_dir=server.root,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _payload():
    """A real serialized function, exactly as ``submit_job`` would carry it."""

    def add(left, right):
        return left + right

    data = serialize_function(add, (2, 3), {})
    # The full local environment would have every submission writing a few
    # hundred pip specs over SFTP; the two packages the two-venv layout
    # genuinely requires are what the code under test branches on.
    data["requirements"] = {"dill": "0.3.8", "cloudpickle": "3.0.0"}
    return data


JOB = {"cores": 2, "memory": "4GB"}


def submit_all(server, cluster_type, count=1, **overrides):
    """Really submit ``count`` jobs; return the scripts and the config."""
    config = _config(server, cluster_type, **overrides)
    executor = ClusterExecutor(config)
    scripts = []
    try:
        executor.connect()
        submitter = (
            executor.scheduler_manager.submit_slurm_job
            if cluster_type == "slurm"
            else executor.scheduler_manager.submit_ssh_job
        )
        for _ in range(count):
            job_id = submitter(_payload(), dict(JOB))
            job_dir = executor.scheduler_manager.active_jobs[job_id]["remote_dir"]
            scripts.append((pathlib.Path(job_dir) / "job.sh").read_text())
    finally:
        executor.disconnect()
    return scripts, config


def submit(server, cluster_type, **overrides):
    scripts, config = submit_all(server, cluster_type, **overrides)
    return scripts[0], config


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------

#: A line that opens an interpreter for one of the two-venv stages, or for the
#: single-venv layout's only stage.
_STAGE_LAUNCH = re.compile(r'^(?!#).*-c "$')


def venv2_launch_line(script):
    """The line that starts the interpreter the user's function runs in.

    Located structurally -- by the comment the generator writes above the
    block -- rather than by pattern-matching for what a mutant produces, so
    the assertions below are about the interpreter VENV2 gets, whatever that
    turns out to be.
    """
    lines = script.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("# Step 2: Use VENV2 to execute the function"):
            for candidate in lines[index + 1 :]:
                if _STAGE_LAUNCH.match(candidate):
                    return candidate
            raise AssertionError("the VENV2 block opens no interpreter")
    # Single-venv layout: there is one launch line and it is VENV2's.
    launches = [line for line in lines if _STAGE_LAUNCH.match(line)]
    assert len(launches) == 1, f"expected exactly one launch line, got {launches}"
    return launches[0]


def assert_venv2_is_not_venv1(script):
    """The property the whole of #164 turns on.

    VENV1 is clustrix's own serialization machinery. Handing its interpreter
    to VENV2 either produces an unrunnable command -- a quoted ``conda run
    ...`` in the executable position -- or, worse because it is silent, runs
    the user's function in the serialization environment instead of the one
    they named.
    """
    launch = venv2_launch_line(script)
    assert "venv1_serialization" not in launch, (
        "VENV2 is started with VENV1's interpreter, so the user's function "
        f"runs in clustrix's serialization venv:\n  {launch}"
    )
    assert (
        "clustrix_venv1_" not in launch
    ), f"VENV2 is started with VENV1's conda environment:\n  {launch}"
    for line in script.splitlines():
        assert line.count("conda run") <= 1, (
            "a `conda run` is nested inside another `conda run`; the inner "
            f"one is a single quoted word in the executable position:\n  {line}"
        )


BACKENDS = ["slurm", "ssh"]


@pytest.mark.parametrize("cluster_type", BACKENDS)
def test_a_named_environment_never_runs_in_venv1(cluster, cluster_type):
    """M13, on the default path: two-venv setup plus a named environment."""
    script, _ = submit(cluster, cluster_type, conda_env_name="prod")
    assert 'conda run -n prod python -c "' in script, script
    assert_venv2_is_not_venv1(script)


@pytest.mark.parametrize("cluster_type", BACKENDS)
def test_the_users_python_executable_reaches_venv2_and_venv1_is_untouched(
    cluster, cluster_type
):
    """The configured interpreter is what VENV2 runs -- and VENV1 keeps its."""
    script, _ = submit(
        cluster, cluster_type, conda_env_name="prod", python_executable="python3.11"
    )
    assert venv2_launch_line(script) == 'conda run -n prod python3.11 -c "'
    assert_venv2_is_not_venv1(script)
    stage1 = [
        line
        for line in script.splitlines()
        if line.startswith("conda run -n clustrix_venv1_")
    ]
    assert stage1 and all(line.endswith(' python -c "') for line in stage1), (
        "VENV1 must stay on the interpreter dill was pinned to, whatever the "
        f"user configured for their own environment: {stage1}"
    )


@pytest.mark.parametrize("cluster_type", BACKENDS)
def test_a_submission_does_not_change_python_executable(cluster, cluster_type):
    """``config`` is process-wide, so a write here outlives the job.

    The round-two regression did not only mis-generate the script it was in:
    the next submission read the leftover ``conda run -n clustrix_venv1_x
    python`` back as if the user had configured it.
    """
    scripts, config = submit_all(cluster, cluster_type, count=2, conda_env_name="prod")
    assert config.python_executable == ClusterConfig().python_executable, (
        "the submission wrote over python_executable; the next job reads "
        f"{config.python_executable!r} as if the user had set it"
    )
    for script in scripts:
        assert_venv2_is_not_venv1(script)
    first, second = (venv2_launch_line(script) for script in scripts)
    assert (
        first == second
    ), f"submission 2 emits a different VENV2 launch line:\n  {first}\n  {second}"


# ---------------------------------------------------------------------------
# The SSH probe: it has to find the conda the cluster has, and leave alone the
# conda the cluster already runs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cluster_type", BACKENDS)
def test_the_probe_finds_the_conda_this_cluster_has(cluster, cluster_type):
    """``conda_setup_prefix`` is measured, and every later conda depends on it.

    It was always the empty string. ``setup_two_venv_environment`` pasted the
    two shell helper definitions together with a space -- ``... }
    _clustrix_conda_works() { ...`` -- which is a bash syntax error, so the
    probe died before it looked anywhere, on every cluster. Nothing noticed,
    because the code falls through to ``bash -lc 'conda --version'`` and sets
    ``conda_available = True`` with no prefix: the two-venv setup then ran
    every ``conda create`` in a shell where conda had never been initialised,
    which is the exact failure ``conda_setup_prefix`` exists to prevent, and
    ``conda_activation_lines`` had nothing measured to emit.
    """
    _, config = submit(cluster, cluster_type, conda_env_name="prod")
    expected = f"source {cluster.root}/miniconda3/etc/profile.d/conda.sh"
    assert config.venv_info.get("conda_setup_prefix") == expected, (
        "the SSH probe did not find the conda.sh this account has; "
        f"got {config.venv_info.get('conda_setup_prefix')!r}"
    )


def test_a_working_conda_on_path_is_left_alone_by_the_probe(tmp_path):
    """The ordering fix, applied to the probe and not only to the script.

    A site that puts conda on ``PATH`` -- via ``module load``, or in
    ``/usr/local/bin`` -- has an installation whose directory holds no
    ``etc/profile.d/conda.sh``. Searching before asking whether conda already
    works made the user's own ``~/miniconda3`` win, and the resulting
    ``conda_setup_prefix`` is then sourced unconditionally over the top of the
    site's conda, so ``conda run -n <name>`` resolves in the wrong
    installation entirely. The generated script was fixed for this in round
    three; the probe was not.
    """
    root = _account(tmp_path)
    bindir = root / "bin"
    conda = bindir / "conda"
    conda.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "--version" ]; then echo "conda 24.1.0"; fi\nexit 0\n'
    )
    conda.chmod(0o755)
    path = os.pathsep.join((str(bindir),) + SAFE_PATH_DIRS)
    with LocalSSHServer(
        root=str(root), password=PASSWORD, env={"PATH": path}
    ) as server:
        _, config = submit(server, "slurm", conda_env_name="prod")
    assert config.venv_info.get("conda_setup_prefix") == "", (
        "the probe sourced a competing conda over one that already works; "
        f"got {config.venv_info.get('conda_setup_prefix')!r}"
    )


def test_the_probe_program_is_valid_shell():
    """It was not, and that is not something to leave to an integration test."""
    import subprocess

    from clustrix.utils import _CONDA_SHELL_HELPERS, _conda_search_lines

    program = "\n".join(list(_CONDA_SHELL_HELPERS) + _conda_search_lines())
    result = subprocess.run(
        ["bash", "-n", "-c", program], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    # No fragment may contain a single quote: the probe wraps the whole
    # program in `bash -lc '...'`.
    assert not any(
        "'" in line for line in list(_CONDA_SHELL_HELPERS) + _conda_search_lines()
    )


def test_the_probe_and_the_job_script_search_the_same_way():
    """One implementation, not two that drift. The drift was the defect."""
    from clustrix.utils import _conda_discovery_lines, _conda_search_lines

    emitted = _conda_discovery_lines("prod")
    search = _conda_search_lines()
    assert any(
        emitted[index : index + len(search)] == search
        for index in range(len(emitted) - len(search) + 1)
    ), "the job script no longer uses the shared search block"


# ---------------------------------------------------------------------------
# M3: a measured two-venv layout must reach the script
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cluster_type", BACKENDS)
def test_a_successful_two_venv_setup_produces_a_two_venv_script(cluster, cluster_type):
    """Dropping the measured layout silently collapses to one interpreter.

    The two-venv split exists because dill's payload is version-locked to the
    interpreter that wrote it; deserializing and executing in one environment
    is exactly what it is there to prevent.
    """
    script, config = submit(cluster, cluster_type, conda_env_name="prod")
    assert config.venv_info, "the measured two-venv layout was thrown away"
    for marker in (
        "# Step 1: Use VENV1 to deserialize function data",
        "# Step 2: Use VENV2 to execute the function",
        "# Step 3: Use VENV1 to serialize the result",
        "function_deserialized.pkl",
    ):
        assert marker in script, (
            f"the job script has no {marker!r}: a successful two-venv setup "
            "did not reach the generator, so the function is deserialized and "
            "executed by the same interpreter"
        )


# ---------------------------------------------------------------------------
# M7: naming an environment must skip replication on every backend
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cluster_type", BACKENDS)
def test_a_named_environment_skips_replication(cluster, cluster_type):
    """No venv is built for a job that will never activate one.

    ``use_two_venv=False`` is the case naming an environment is for. With the
    name lost on the way into ``_setup_job_environment``, clustrix pip-installs
    the whole local environment into a virtualenv the generated script never
    sources -- on every single submission.
    """
    script, _ = submit(cluster, cluster_type, conda_env_name="prod", use_two_venv=False)
    built = [command for command in cluster.commands if "-m venv" in command]
    assert not built, (
        "a virtualenv was built for a job that runs in an existing "
        f"environment, and the generated script never sources it: {built}"
    )
    assert not (pathlib.Path(cluster.root) / "venv").exists()
    assert 'conda run -n prod python -c "' in script, script
