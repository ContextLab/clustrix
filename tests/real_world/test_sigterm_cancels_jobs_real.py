"""SIGTERM to the client cancels the jobs its call had started (#176 W2).

A real client process submits real jobs to a real ``sshd`` or SLURM, is
sent a real SIGTERM while it waits for them, and the host is then asked
what is still running. Before the fix: all of it -- only Ctrl-C was
cleaned up, and a batch system, ``kill`` and a stopped notebook server all
send SIGTERM.

Each test works in a remote directory of its own and stops only what it
finds there: other people's jobs on the host are none of its business.

Run as ``tests/real_world/test_local_sshd_slurm.py`` describes.
"""

import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import time
import uuid

import pytest

from tests.real_world.test_local_sshd_slurm import (  # noqa: F401
    HOST,
    KEY,
    PORT,
    USER,
    _remote,
    host_facts,
    pytestmark,
)

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

DRIVER = """
import signal, sys, time
from clustrix import cluster, configure

kind, work, shape, handler = sys.argv[1:5]
configure(cluster_type=kind, cluster_host={host!r}, cluster_port={port!r},
          username={user!r}, key_file={key!r}, ssh_host_key_policy="auto_add",
          remote_work_dir=work, replicate_local_environment=False,
          job_poll_interval=1, max_parallel_jobs=3)

if handler == "own":
    def mine(signum, frame):
        print("OWN HANDLER", flush=True)
        sys.exit(7)
    signal.signal(signal.SIGTERM, mine)

@cluster(cores=1, memory="200MB", time="00:10:00", parallel=(shape == "loop"))
def sleepy(n):
    import time
    out = []
    for i in range(n):
        open("iteration-started", "w").close()  # in the job's own directory
        time.sleep(200)
        out.append(i)
    return out

print("STARTING", flush=True)
sleepy(3)
print("FINISHED", flush=True)
"""


def _running(work: str, slurm: bool) -> dict:
    """What of this test's is alive on the host: function pids, SLURM jobs."""
    pids = _remote(
        "for p in $(pgrep -u $(id -u) -f venv2_execution/bin/python); do "
        f'case "$(readlink /proc/$p/cwd)" in $HOME/{work}/*) echo $p;; esac; '
        "done; true"
    ).stdout.split()
    jobs = []
    if slurm:
        listed = _remote("squeue -h -u $(id -un) -o '%i %Z'").stdout
        jobs = [
            line.split()[0]
            for line in listed.splitlines()
            if f"/{work}/" in line.split(maxsplit=1)[-1]
        ]
    return {"pids": pids, "jobs": jobs}


def _started(work: str) -> int:
    """How many of this test's jobs have reached the function's loop."""
    listed = _remote(f"ls ~/{work}/job_*/iteration-started 2>/dev/null; true")
    return len(listed.stdout.split())


def _stop_what_is_left(work: str, slurm: bool) -> None:
    """Only this test's own: found by its own remote directory."""
    left = _running(work, slurm)
    if left["jobs"]:
        _remote("scancel " + " ".join(left["jobs"]))
    if left["pids"]:
        _remote("kill -9 " + " ".join(left["pids"]))
    _remote(f"rm -rf ~/{work}")


@pytest.mark.parametrize("kind", ["ssh", "slurm"])
@pytest.mark.parametrize("shape, expected", [("loop", 3), ("single", 1)])
@pytest.mark.parametrize("handler", ["default", "own"])
def test_sigterm_leaves_nothing_running_on_the_cluster(
    kind, shape, expected, handler, host_facts, tmp_path
):
    if kind == "slurm" and not host_facts["slurm"]:
        pytest.skip(f"The test host {HOST} has no sbatch, so SLURM cannot be tested.")
    work = f"clustrix-sigterm-{uuid.uuid4().hex[:10]}"
    script = tmp_path / "driver.py"
    script.write_text(
        textwrap.dedent(DRIVER).format(host=HOST, port=PORT, user=USER, key=KEY)
    )
    driver = subprocess.Popen(
        [sys.executable, str(script), kind, f"~/{work}", shape, handler],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": PACKAGE_ROOT, "HOME": str(tmp_path)},
    )
    try:
        deadline = time.monotonic() + 180
        while (
            _started(work) < expected
            and time.monotonic() < deadline
            and driver.poll() is None
        ):
            time.sleep(1)
        before = _running(work, kind == "slurm")
        assert len(before["pids"]) == expected, (before, driver.poll())
        if kind == "slurm":
            assert len(before["jobs"]) == expected

        driver.send_signal(signal.SIGTERM)
        out, err = driver.communicate(timeout=90)

        deadline = time.monotonic() + 20
        after = _running(work, kind == "slurm")
        while (after["pids"] or after["jobs"]) and time.monotonic() < deadline:
            time.sleep(1)
            after = _running(work, kind == "slurm")
    finally:
        with contextlib.suppress(ProcessLookupError):
            driver.kill()
        _stop_what_is_left(work, kind == "slurm")

    print(
        f"{kind}/{shape}/{handler}: {before} before SIGTERM, {after} after; "
        f"client exit status {driver.returncode}\n{err[-600:]}"
    )
    assert after == {"pids": [], "jobs": []}
    assert "FINISHED" not in out
    if handler == "own":
        # The program's own handler stays in charge, and its sys.exit is
        # what unwinds the call.
        assert "OWN HANDLER" in out and driver.returncode == 7
    else:
        assert driver.returncode == -signal.SIGTERM, "it ends by the signal"
        assert "cancelled" in err
