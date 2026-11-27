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
    _empty_the_store()
    with ClusterExecutor(config) as executor:
        executor.connect()
        yield executor.connection_manager, config


def _empty_the_store() -> None:
    """Staged copies are read-only, so ``rm -rf`` alone removes nothing."""
    _remote(f"chmod -R u+w {WORK_DIR}/data 2>/dev/null; rm -rf {WORK_DIR}/data")


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
    """A copy counts as there only if it carries the completion mark.

    This used to tamper with a *finished* copy and expect its size to give
    it away. A finished copy is read-only now, and what marks one as
    finished is ``.complete``, written last -- so the interrupted upload is
    made the way one would really look: a directory under the identity
    with part of the file in it and no mark.
    """
    connection, config = cluster_connection
    plan = auto_stage.prepare(connection, config, reads_the_table, (), {})[2]
    remote = plan.inputs[0].remote
    home = os.path.dirname(remote)
    assert _remote(f"test -e {home}/.complete").returncode == 0
    _remote(f"chmod -R u+w {home} && rm -rf {home} && mkdir {home}")
    _remote(f"printf 'a,b' > {remote}")

    again = auto_stage.prepare(connection, config, reads_the_table, (), {})[2]

    assert again.inputs[0].how == "uploaded" and again.inputs[0].remote == remote
    assert _remote_text(remote) == "a,b\n1,2\n"
    assert sorted(_remote(f"ls -A {home}").stdout.split()) == [
        ".complete",
        "table.csv",
    ]
    assert _remote(f"ls -A {os.path.dirname(home)}").stdout.split() == [
        os.path.basename(home)
    ], "nothing is left beside the finished copy"


# -- what an adversarial review found -----------------------------------------


def _store_listing() -> str:
    return _remote(f"find {WORK_DIR}/data -mindepth 1 | sort").stdout


def test_credentials_inside_a_directory_never_reach_the_cluster(
    project, cluster_connection, caplog
):
    """B1. ``.env`` alone was refused; ``.env`` inside ``data`` was sent."""
    connection, config = cluster_connection
    data = project / "data"
    (data / ".aws").mkdir()
    (data / ".aws" / "credentials").write_text("[default]\naws_secret_access_key=x\n")
    (data / ".env").write_text("API_KEY=hunter2\n")
    (data / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    os.symlink("/etc/hostname", data / "link_out.txt")

    with caplog.at_level("INFO", logger="clustrix.auto_stage"):
        _, _, plan = auto_stage.prepare(connection, config, reads, ("data",), {})

    listing = _store_listing()
    print(listing, plan.explain(), sep="\n")
    for secret in (".env", "id_rsa", "credentials", ".aws", "hunter2", "link_out"):
        assert secret not in listing
    assert "data/table.csv" in listing and "data/subjects/s3.txt" in listing
    assert (
        _remote(f"grep -rl 'hunter2\\|PRIVATE KEY\\|secret_access' {WORK_DIR}").stdout
        == ""
    )
    warned = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    informed = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    for member in (data / ".env", data / "id_rsa", data / ".aws" / "credentials"):
        assert any(str(member) in message for message in warned), warned
        assert any(str(member) in line for line in plan.skipped)
    assert any(str(data / "link_out.txt") in message for message in informed)
    assert any("link_out.txt is a symbolic link" in line for line in plan.skipped)


def test_an_excluded_path_inside_a_staged_directory_stays_here(
    project, cluster_connection
):
    """B2. ``no_data=["data/private"]`` removed nothing from ``data``."""
    connection, config = cluster_connection
    (project / "data" / "private").mkdir()
    (project / "data" / "private" / "patients.csv").write_text("name,dx\n")

    with_it = auto_stage.prepare(connection, config, reads, ("data",), {})[2]
    assert "private/patients.csv" in _store_listing(), "the control: it is staged"

    without = auto_stage.prepare(
        connection, config, reads, ("data",), {}, exclude=["data/private"]
    )[2]

    assert without.inputs[0].remote != with_it.inputs[0].remote
    under = _remote(f"find {without.inputs[0].remote} | sort").stdout
    assert "private" not in under and "table.csv" in under


def test_a_staged_copy_cannot_be_written_to(project, cluster_connection):
    """B3, at the store: read-only files in read-only directories."""
    connection, config = cluster_connection
    plan = auto_stage.prepare(connection, config, reads, ("data",), {})[2]
    remote = plan.inputs[0].remote

    attempts = [
        f"printf x >> {remote}/table.csv",
        f"touch {remote}/new.txt",
        f"rm {remote}/table.csv",
        f"mv {remote} {remote}.moved",
        f"rm {os.path.dirname(remote)}/.complete",
    ]

    assert [_remote(attempt).returncode != 0 for attempt in attempts] == [True] * 5
    assert _remote_text(f"{remote}/table.csv") == "a,b\n1,2\n"


def test_content_changed_behind_the_same_size_and_time_is_a_new_copy(
    project, cluster_connection
):
    """B4. A directory was named by its listing, sizes and times."""
    connection, config = cluster_connection
    table = project / "data" / "table.csv"
    before = auto_stage.prepare(connection, config, reads, ("data",), {})[2]
    stat = table.stat()
    table.write_text("a,b\n9,9\n")
    os.utime(table, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    after = auto_stage.prepare(connection, config, reads, ("data",), {})[2]

    assert after.inputs[0].how == "uploaded"
    assert after.inputs[0].remote != before.inputs[0].remote
    assert _remote_text(f"{after.inputs[0].remote}/table.csv") == "a,b\n9,9\n"
    assert _remote_text(f"{before.inputs[0].remote}/table.csv") == "a,b\n1,2\n"


def test_too_big_to_read_is_named_by_size_and_time_and_the_plan_says_so(
    project, cluster_connection, monkeypatch
):
    connection, config = cluster_connection
    monkeypatch.setattr(auto_stage, "_HASH_LIMIT_BYTES", 4)

    plan = auto_stage.prepare(connection, config, reads, ("data",), {})[2]

    assert plan.inputs[0].identified_by_content is False
    assert "identified by size and time, not content" in plan.explain()
    assert _remote_text(f"{plan.inputs[0].remote}/table.csv") == "a,b\n1,2\n"


def takes_many(path, point, counts, order, extra):
    return path, point, counts, order, extra


def test_only_the_arguments_that_named_a_path_are_changed(project, cluster_connection):
    """H2. The red team's call: every container came back as a builtin, and
    the path inside the set was uploaded but not rewritten."""
    import collections

    connection, config = cluster_connection
    Point = collections.namedtuple("Point", "path n")
    absolute = str(project / "data" / "table.csv")
    point = Point("x", 1)
    counts = collections.defaultdict(int)
    order = collections.OrderedDict(a=1)

    args, _, plan = auto_stage.prepare(
        connection, config, takes_many, (absolute, point, counts, order, {absolute}), {}
    )

    remote = plan.inputs[0].remote
    assert args[0] == remote
    assert args[1] is point and args[2] is counts and args[3] is order
    assert args[4] == {remote}


def test_sizes_stay_with_their_paths_when_one_cannot_be_read(cluster_connection):
    """A file the account cannot read prints an error and no size. Read by
    position, every later answer moved up one path."""
    connection, _ = cluster_connection
    base = connection.resolve_remote_path(f"{WORK_DIR}/sizes")
    _remote(
        f"rm -rf {base}; mkdir -p {base}/closed && cd {base} && printf 12345 > open "
        "&& printf 1234567 > locked && chmod 000 locked closed"
    )
    try:
        sizes = auto_stage._remote_sizes(
            connection,
            [f"{base}/{name}" for name in ("locked", "open", "closed", "absent")],
        )
    finally:
        _remote(f"chmod -R u+rwx {base}; rm -rf {base}")

    assert sizes == {
        f"{base}/locked": None,
        f"{base}/open": 5,
        f"{base}/closed": None,
        f"{base}/absent": None,
    }


DRIVER = """
import os, signal, sys
signal.signal(signal.SIGINT, signal.default_int_handler)
from clustrix import auto_stage, configure, get_config
from clustrix.executor import ClusterExecutor

def reads(path):
    return path

configure(cluster_type="ssh", cluster_host={host!r}, cluster_port={port!r},
          username={user!r}, key_file={key!r}, ssh_host_key_policy="auto_add",
          remote_work_dir={work!r})
with ClusterExecutor(get_config()) as executor:
    executor.connect()
    print("CONNECTED", flush=True)
    plan = auto_stage.prepare(
        executor.connection_manager, get_config(), reads, (sys.argv[1],), {{}}
    )[2]
    print("STAGED", plan.inputs[0].how, plan.inputs[0].remote, flush=True)
"""


def _stage_in_another_process(project, tmp_path, name):
    import sys

    import clustrix

    script = tmp_path / "stage_driver.py"
    script.write_text(
        DRIVER.format(host=HOST, port=PORT, user=USER, key=str(KEY), work=WORK_DIR)
    )
    return subprocess.Popen(
        [sys.executable, str(script), name],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": os.path.dirname(os.path.dirname(clustrix.__file__)),
        },
    )


@pytest.mark.parametrize(
    "when",
    ["ls -A {store}", "find {store} -name big.bin -size +1M"],
    ids=["as-the-directory-appears", "mid-transfer"],
)
def test_an_upload_interrupted_by_ctrl_c_leaves_nothing_behind(
    project, cluster_connection, tmp_path, when
):
    """The half-sent copy under its private name is removed on the way out,
    and nothing ever appeared under the identity."""
    import signal
    import time

    big = project / "data" / "big.bin"
    with open(big, "wb") as handle:
        handle.truncate(400 * 1024 * 1024)
    driver = _stage_in_another_process(project, tmp_path, "data/big.bin")
    try:
        assert driver.stdout.readline().strip() == "CONNECTED"
        deadline = time.monotonic() + 60
        started = when.format(store=f"{WORK_DIR}/data")
        while time.monotonic() < deadline:
            if ".incoming-" in _remote(started).stdout:
                break
        else:
            pytest.fail("the upload never started")
        driver.send_signal(signal.SIGINT)
        out, err = driver.communicate(timeout=120)
    finally:
        driver.kill()

    print(out, err[-2000:], sep="\n")
    assert "KeyboardInterrupt" in err and "STAGED" not in out
    assert _remote(f"ls -A {WORK_DIR}/data").stdout.split() == []


def test_two_processes_staging_the_same_file_never_show_half_of_it(
    project, cluster_connection, tmp_path
):
    """Both upload under private names; whichever finishes first is renamed
    into place and the other is discarded. A job that looks at the final
    name sees nothing, or all of it."""
    import threading

    connection, _ = cluster_connection
    shared = project / "data" / "shared.bin"
    shared.write_bytes(os.urandom(1024 * 1024) * 96)
    size = shared.stat().st_size
    identity, _ = auto_stage._identity(shared, [("", shared)])
    final = (
        connection.resolve_remote_path(WORK_DIR)
        + f"/{auto_stage.STORE_DIRECTORY}/{identity}/shared.bin"
    )
    seen, done = [], threading.Event()

    def watch():
        while not done.is_set():
            looked = _remote(f"stat -c %s {final} 2>/dev/null").stdout.strip()
            seen.append(looked)

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        drivers = [
            _stage_in_another_process(project, tmp_path, "data/shared.bin")
            for _ in range(2)
        ]
        outputs = [driver.communicate(timeout=300) for driver in drivers]
    finally:
        done.set()
        watcher.join()

    print(outputs, sorted(set(seen)))
    for driver, (out, err) in zip(drivers, outputs):
        assert driver.returncode == 0, err
        assert f"{final}" in out and "STAGED" in out
    assert set(seen) <= {"", str(size)}, "a partial file was visible"
    local_digest = subprocess.run(
        ["sha256sum", str(shared)], capture_output=True, text=True
    ).stdout.split()[0]
    assert _remote(f"sha256sum {final}").stdout.split()[0] == local_digest
    assert _remote(
        f"ls -A {os.path.dirname(os.path.dirname(final))}"
    ).stdout.split() == [identity]


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


# -- what an adversarial review found, through @cluster ------------------------


def test_a_job_cannot_change_what_the_next_call_reads(backend, project):
    """B3. A job opened its staged input ``r+b`` and wrote to it; the next
    call was handed the modified bytes though the local file never changed."""
    (project / "data" / "state.bin").write_bytes(b"AAAA")

    @cluster(parallel=False, **RESOURCES)
    def read_then_update():
        with open("data/state.bin", "r+b") as handle:
            seen = handle.read()
            handle.seek(0)
            handle.write(b"BBBB")
        return seen

    @cluster(parallel=False, **RESOURCES)
    def read_only():
        import os

        with open("data/state.bin", "rb") as handle:
            return handle.read(), os.path.realpath("data/state.bin")

    with pytest.raises(PermissionError):
        read_then_update()
    seen, stored = read_only()

    assert seen == b"AAAA"
    assert f"/{auto_stage.STORE_DIRECTORY}/" in stored
    assert _remote(f"cat {stored}").stdout == "AAAA"
    assert (project / "data" / "state.bin").read_bytes() == b"AAAA"


def test_an_async_call_says_its_inputs_are_not_staged(backend, caplog):
    """L1. It failed remotely with a bare FileNotFoundError and no hint."""

    @cluster(async_submit=True, **RESOURCES)
    def read_async():
        return open("data/table.csv").read()

    with caplog.at_level("WARNING", logger="clustrix.decorator"):
        handle = read_async()
        with pytest.raises(RuntimeError) as failed:
            handle.get_result(timeout=300)

    assert isinstance(failed.value.__cause__, FileNotFoundError)

    (said,) = [r.getMessage() for r in caplog.records if "async_submit" in r.message]
    assert "data/table.csv" in said and "not staged" in said


@pytest.fixture(params=["ssh", "slurm"])
def shared_project(request, monkeypatch):
    """A project the cluster account can read where it is: a shared disk."""
    import shutil
    import tempfile

    if request.param == "slurm" and _remote("command -v sbatch").returncode != 0:
        pytest.skip(f"{HOST} has no sbatch, so SLURM cannot be tested")
    root = Path(tempfile.mkdtemp(prefix="clustrix-shared-", dir="/tmp"))
    try:
        (root / "data").mkdir()
        (root / "data" / "table.csv").write_text("a,b\n1,2\n")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["chmod", "-R", "a+rX", str(root)], check=True)
        if _remote(f"cat {root / 'data' / 'table.csv'}").stdout != "a,b\n1,2\n":
            pytest.skip(f"{USER}@{HOST} cannot read {root}: no shared filesystem")
        monkeypatch.chdir(root)
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
        )
        _empty_the_store()
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_relative_path_works_when_the_cluster_shares_the_disk(shared_project):
    """H1. Used "in place", nothing was linked, and the job -- which runs
    in its own directory -- raised FileNotFoundError for a file the cluster
    could see perfectly well."""

    @cluster(parallel=False, **RESOURCES)
    def in_code():
        import os

        return open("data/table.csv").read(), os.path.realpath("data/table.csv")

    @cluster(parallel=False, **RESOURCES)
    def as_argument(path):
        return open(path).read()

    text, real = in_code()

    assert text == "a,b\n1,2\n"
    assert real == str(shared_project / "data" / "table.csv"), "used where it is"
    assert as_argument("data/table.csv") == "a,b\n1,2\n"
    assert _remote(f"ls -A {WORK_DIR}/data 2>/dev/null").stdout.split() == []


# -- #176 ---------------------------------------------------------------------


def _mode(path: str) -> str:
    return _remote(f"stat -c %a {path}").stdout.strip()


def test_the_store_is_the_owners_alone(project, cluster_connection):
    """S3. A local 0600 file landed 0444 inside 0755 directories: readable
    by every account on the cluster."""
    connection, config = cluster_connection
    table = project / "data" / "table.csv"
    table.chmod(0o600)

    _, _, plan = auto_stage.prepare(
        connection, config, reads, (str(table), "data/subjects"), {}
    )

    store = connection.resolve_remote_path(WORK_DIR) + "/data"
    assert _mode(store) == "700"
    modes = _remote(f"find {store} -mindepth 1 -printf '%y %m\\n' | sort | uniq -c")
    assert sorted(line.split()[1:] for line in modes.stdout.splitlines()) == [
        ["d", "500"],
        ["f", "400"],
    ], modes.stdout
    assert _remote_text(plan.inputs[0].remote) == "a,b\n1,2\n"


def test_a_store_left_open_by_an_earlier_version_is_closed(project, cluster_connection):
    connection, config = cluster_connection
    store = connection.resolve_remote_path(WORK_DIR) + "/data"
    _remote(
        f"mkdir -p {store}/0123456789abcdef0123/old && "
        f"echo old > {store}/0123456789abcdef0123/old/f.txt && "
        f"chmod 444 {store}/0123456789abcdef0123/old/f.txt && "
        f"chmod 555 {store}/0123456789abcdef0123/old {store}/0123456789abcdef0123 && "
        f"chmod 755 {store}"
    )
    assert _mode(f"{store}/0123456789abcdef0123/old/f.txt") == "444"

    auto_stage.prepare(connection, config, reads_the_table, (), {})

    assert _mode(store) == "700"
    assert _mode(f"{store}/0123456789abcdef0123") == "500"
    assert _mode(f"{store}/0123456789abcdef0123/old/f.txt") == "400"


def test_a_private_key_behind_a_linked_directory_never_reaches_the_cluster(
    project, cluster_connection, tmp_path, caplog
):
    """S2, end to end: ``keys -> .../.ssh``."""
    connection, config = cluster_connection
    ssh = tmp_path / "home" / ".ssh"
    ssh.mkdir(parents=True)
    (ssh / "aws-prod").write_text("PRIVATE KEY MATERIAL aws-prod\n")
    (project / "keys").symlink_to(ssh)

    with caplog.at_level("WARNING", logger="clustrix.auto_stage"):
        _, _, plan = auto_stage.prepare(
            connection, config, reads, ("keys/aws-prod",), {}
        )
        _, _, folder = auto_stage.prepare(connection, config, reads, ("keys",), {})

    assert plan.inputs == [] and folder.inputs == []
    assert caplog.text.count("looks like a credential") == 2
    found = _remote(f"grep -rl 'PRIVATE KEY MATERIAL' {WORK_DIR}/ 2>/dev/null")
    assert found.stdout.strip() == ""


def test_everything_in_a_finished_job_directory_has_a_reserved_name(backend):
    """S5's list is only as good as it is complete. This is a real job
    directory, listed after the job ran."""
    from clustrix.utils import is_reserved_job_directory_name

    configure(cleanup_on_success=False)

    @cluster(parallel=False, **RESOURCES)
    def where():
        import os

        return os.getcwd()

    job_dir = where()

    names = _remote(f"ls -A {job_dir}").stdout.split()
    assert {"job.sh", "function_data.pkl", "result.pkl"} <= set(names)
    assert [n for n in names if not is_reserved_job_directory_name(n)] == []
    _remote(f"rm -rf {job_dir}")


def test_an_input_named_like_a_job_file_is_refused_before_anything_is_sent(
    backend, project
):
    (project / "result.pkl").write_bytes(b"an old result")

    @cluster(parallel=False, **RESOURCES)
    def load(path):
        return open(path, "rb").read()

    before = _remote(f"ls -A {WORK_DIR}").stdout
    with pytest.raises(StagingError) as refused:
        load("result.pkl")

    assert "'result.pkl'" in str(refused.value) and "collides" in str(refused.value)
    assert _remote(f"ls -A {WORK_DIR}").stdout == before, "no job was started"


def test_a_job_that_writes_to_a_staged_input_is_told_why_it_could_not(backend, project):
    """S6. An output file left by an earlier run is an existing path among
    the arguments: staged, read-only."""
    (project / "data" / "out.txt").write_text("old run")

    @cluster(parallel=False, **RESOURCES)
    def write(out_path):
        with open(out_path, "w") as handle:
            handle.write("new")
        return "wrote"

    with pytest.raises(PermissionError) as failed:
        write("data/out.txt")

    message = str(failed.value)
    assert failed.value.errno == 13 and failed.value.filename == "data/out.txt"
    assert "staged" in message and "read-only" in message
    assert "@cluster(no_data=['data/out.txt'])" in message

    @cluster(parallel=False, no_data=["data/out.txt"], **RESOURCES)
    def write_unstaged(out_path):
        import os

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as handle:
            handle.write("new")
        return "wrote"

    assert write_unstaged("data/out.txt") == "wrote"
