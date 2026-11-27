"""Uploading a call's input files to a cluster that cannot see this disk.

``tests/unit/test_auto_stage.py`` covers finding files and deciding which
may leave the machine. What it cannot cover is the upload itself: its SSH
server shares the test's filesystem, so every local file is "already
there". These tests need a remote account that genuinely cannot read the
test's files -- the local test cluster's ``clx`` user, looking at a
directory only the test's own user may enter -- and skip, saying so, when
the two turn out to share a view.
"""

import os
import subprocess
from pathlib import Path

import pytest

from clustrix import auto_stage, cluster, configure, get_config
from clustrix.executor import ClusterExecutor
from clustrix.staging import StagingError

from .test_local_sshd_slurm import (
    HOST,
    KEY,
    PORT,
    USER,
    _remote,
    pytestmark,
)  # noqa: F401

WORK_DIR = "~/clustrix-autostage-tests"


def reads(path):
    return Path(path).read_text()


def reads_the_table():
    return open("data/table.csv").read()


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "data" / "subjects").mkdir(parents=True)
    (root / "data" / "table.csv").write_text("a,b\n1,2\n")
    for index in (1, 2, 3):
        (root / "data" / "subjects" / f"s{index}.txt").write_text(f"subject {index}\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    if _remote(f"test -e {root / 'data' / 'table.csv'}").returncode == 0:
        pytest.skip(
            f"{USER}@{HOST} can read this test's files, so nothing would need "
            "uploading; these tests need a remote that does not share the disk"
        )
    return root


@pytest.fixture
def cluster_connection(project):
    configure(
        cluster_type="ssh",
        cluster_host=HOST,
        cluster_port=PORT,
        username=USER,
        key_file=KEY,
        ssh_host_key_policy="auto_add",
        remote_work_dir=WORK_DIR,
    )
    config = get_config()
    _remote(f"rm -rf {WORK_DIR}/data")
    with ClusterExecutor(config) as executor:
        executor.connect()
        yield executor.connection_manager, config


def _remote_text(path: str) -> str:
    return _remote(f"cat {path}").stdout


def test_a_file_the_cluster_cannot_see_is_uploaded_and_linked(
    project, cluster_connection
):
    connection, config = cluster_connection

    args, kwargs, plan = auto_stage.prepare(connection, config, reads_the_table, (), {})

    (staged,) = plan.inputs
    assert staged.how == "uploaded" and staged.link == "data/table.csv"
    assert _remote_text(staged.remote) == "a,b\n1,2\n"

    job_dir = connection.resolve_remote_path(f"{WORK_DIR}/job-for-link-test")
    _remote(f"rm -rf {job_dir} && mkdir -p {job_dir}")
    auto_stage.link_into_job_directory(connection, job_dir, plan.links)
    assert _remote_text(f"{job_dir}/data/table.csv") == "a,b\n1,2\n"


def test_the_same_content_is_uploaded_once(project, cluster_connection):
    connection, config = cluster_connection
    first = auto_stage.prepare(connection, config, reads_the_table, (), {})[2]
    stamp = _remote(f"stat -c %Y.%i {first.inputs[0].remote}").stdout

    second = auto_stage.prepare(connection, config, reads_the_table, (), {})[2]

    assert second.inputs[0].how == "already uploaded"
    assert second.inputs[0].remote == first.inputs[0].remote
    assert _remote(f"stat -c %Y.%i {first.inputs[0].remote}").stdout == stamp


def test_changed_content_gets_its_own_copy_and_the_old_one_stays(
    project, cluster_connection
):
    """A job still running against the first version must keep reading it."""
    connection, config = cluster_connection
    before = auto_stage.prepare(connection, config, reads_the_table, (), {})[2]

    (project / "data" / "table.csv").write_text("a,b\n9,9\n")
    after = auto_stage.prepare(connection, config, reads_the_table, (), {})[2]

    assert after.inputs[0].remote != before.inputs[0].remote
    assert _remote_text(before.inputs[0].remote) == "a,b\n1,2\n"
    assert _remote_text(after.inputs[0].remote) == "a,b\n9,9\n"


def test_an_absolute_path_argument_is_pointed_at_the_copy(project, cluster_connection):
    connection, config = cluster_connection
    table = project / "data" / "table.csv"

    args, kwargs, plan = auto_stage.prepare(
        connection, config, reads, (table,), {"unrelated": str(table)}
    )

    (staged,) = plan.inputs
    assert args == (Path(staged.remote),) and isinstance(args[0], Path)
    assert kwargs == {"unrelated": staged.remote}
    assert staged.link is None and plan.links == []


def test_a_directory_arrives_whole(project, cluster_connection):
    connection, config = cluster_connection

    args, _, plan = auto_stage.prepare(
        connection, config, reads, ("data/subjects",), {}
    )

    (staged,) = plan.inputs
    assert args == ("data/subjects",) and staged.link == "data/subjects"
    listing = _remote(f"ls {staged.remote}").stdout.split()
    assert listing == ["s1.txt", "s2.txt", "s3.txt"]
    assert _remote_text(f"{staged.remote}/s2.txt") == "subject 2\n"


def test_more_than_the_limit_is_refused_with_the_sizes(project, cluster_connection):
    connection, config = cluster_connection
    (project / "data" / "big.bin").write_bytes(b"\x01" * 5000)
    configure(auto_stage_max_bytes=1000)

    with pytest.raises(StagingError) as refused:
        auto_stage.prepare(connection, get_config(), reads, ("data/big.bin",), {})

    message = str(refused.value)
    assert "big.bin" in message and "auto_stage_max_bytes" in message
    assert "no_data" in message
    assert _remote(f"ls {WORK_DIR}/data").stdout.strip() == ""


def test_an_interrupted_upload_is_not_mistaken_for_a_finished_one(
    project, cluster_connection
):
    """A partial file from a killed upload has the wrong size, so the next
    call uploads again instead of handing a job half a file."""
    connection, config = cluster_connection
    plan = auto_stage.prepare(connection, config, reads_the_table, (), {})[2]
    remote = plan.inputs[0].remote
    _remote(f"printf 'a,b' > {remote}")

    again = auto_stage.prepare(connection, config, reads_the_table, (), {})[2]

    assert again.inputs[0].how == "uploaded"
    assert _remote_text(remote) == "a,b\n1,2\n"
    assert _remote(f"ls {os.path.dirname(remote)}").stdout.split() == ["table.csv"]


# -- through @cluster ---------------------------------------------------------

RESOURCES = dict(cores=1, memory="200MB", time="00:05:00")


@pytest.fixture(params=["ssh", "slurm"])
def backend(request, project):
    if request.param == "slurm" and _remote("command -v sbatch").returncode != 0:
        pytest.skip(f"{HOST} has no sbatch, so SLURM cannot be tested")
    configure(
        cluster_type=request.param,
        cluster_host=HOST,
        cluster_port=PORT,
        username=USER,
        key_file=KEY,
        ssh_host_key_policy="auto_add",
        remote_work_dir=WORK_DIR,
        job_poll_interval=1,
        job_wait_timeout=300,
        replicate_local_environment=False,
        max_parallel_jobs=3,
    )
    return request.param


def test_a_job_reads_a_file_it_only_named_in_its_code(backend):
    """The whole point: the function is written as it would be for the
    laptop, and it runs on a machine that has never seen the file."""

    @cluster(**RESOURCES)
    def summarise():
        import os

        with open("data/table.csv") as handle:
            return handle.read(), os.getcwd()

    text, where = summarise()

    assert text == "a,b\n1,2\n"
    assert "clustrix-autostage-tests/job_" in where


def test_a_loop_over_the_files_in_a_directory_is_divided_among_jobs(backend, project):
    """File-per-subject processing: the directory is an argument, the list
    of files is only known on the cluster, and each chunk of the loop is its
    own job with the data linked into its working directory."""

    @cluster(**RESOURCES)
    def lengths(folder):
        import os

        found = {}
        for name in sorted(os.listdir(folder)):
            with open(os.path.join(folder, name)) as handle:
                found[name] = (handle.read().strip(), os.getpid())
        return found

    result = lengths("data/subjects")

    assert {name: text for name, (text, _) in result.items()} == {
        "s1.txt": "subject 1",
        "s2.txt": "subject 2",
        "s3.txt": "subject 3",
    }
    assert len({pid for _, pid in result.values()}) == 3, "one job per file"


def test_listed_and_excluded_paths_are_honoured(backend, project):
    (project / "results").mkdir()
    (project / "results" / "old.txt").write_text("from an earlier run\n")
    (project / "extra.txt").write_text("listed\n")

    @cluster(data=["extra.txt"], no_data=["results"], **RESOURCES)
    def look():
        import os

        open("results/old.txt") if os.path.exists("results/old.txt") else None
        return os.path.exists("results/old.txt"), open("extra.txt").read()

    assert look() == (False, "listed\n")


def test_turning_it_off_leaves_the_job_without_the_file(backend):
    configure(auto_stage=False)

    @cluster(**RESOURCES)
    def summarise():
        return open("data/table.csv").read()

    with pytest.raises(FileNotFoundError):
        summarise()
