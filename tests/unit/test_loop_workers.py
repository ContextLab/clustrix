"""The local worker pool: real subprocesses, real pipes, real crashes.

Nothing here is simulated. Workers are started as separate interpreters and
tasks cross the process boundary pickled, exactly as they do when
``@cluster`` divides a loop among them.
"""

import os
import subprocess
import sys
import textwrap
import time

import pytest

from clustrix.loop_workers import (
    RemoteTraceback,
    WorkerDied,
    WorkerPool,
    available_cpus,
    shared_pool,
    timed_call,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the tests signal processes the POSIX way"
)


def where_am_i(value):
    return os.getpid(), value * 2


def chatty(value):
    print(f"working on {value}")
    return value


def napping(seconds):
    time.sleep(seconds)
    return os.getpid()


def failing(value):
    if value == 2:
        raise KeyError(f"no such thing: {value}")
    return value


def dying(value):
    os.kill(os.getpid(), 9)


def exiting(code):
    raise SystemExit(code)


def reads_environment(name):
    return os.environ.get(name)


@pytest.fixture
def pool():
    with WorkerPool(3) as running:
        yield running


def test_tasks_run_in_other_processes_and_come_back_in_order(pool):
    results = pool.map(where_am_i, [(i,) for i in range(9)])

    assert [value for _, value in results] == [i * 2 for i in range(9)]
    pids = {pid for pid, _ in results}
    assert os.getpid() not in pids
    assert 1 <= len(pids) <= 3


def test_work_really_overlaps(pool):
    """Three half-second naps on three workers take about half a second."""
    pool.map(napping, [(0.0,)] * 3)  # workers started and warm
    started = time.perf_counter()
    pids = pool.map(napping, [(0.5,)] * 3)
    elapsed = time.perf_counter() - started

    assert len(set(pids)) == 3
    assert elapsed < 1.2, f"three 0.5s tasks took {elapsed:.2f}s: they ran in turn"


def test_limit_caps_how_many_run_at_once(pool):
    pool.map(napping, [(0.0,)] * 3)
    started = time.perf_counter()
    pool.map(napping, [(0.3,)] * 3, limit=1)

    assert time.perf_counter() - started >= 0.85


def test_what_a_task_prints_is_replayed_in_task_order(pool):
    heard = []

    pool.map(chatty, [(i,) for i in range(6)], emit=heard.append)

    assert "".join(heard).splitlines() == [f"working on {i}" for i in range(6)]


def test_printing_reaches_the_caller_s_stdout_by_default(pool, capsys):
    pool.map(chatty, [(1,), (2,)])

    assert capsys.readouterr().out == "working on 1\nworking on 2\n"


def test_an_exception_arrives_as_itself_with_the_worker_s_traceback(pool):
    with pytest.raises(KeyError) as raised:
        pool.map(failing, [(1,), (2,), (3,)])

    assert "no such thing: 2" in str(raised.value)
    cause = raised.value.__cause__
    assert isinstance(cause, RemoteTraceback)
    assert "in failing" in str(cause) and "raise KeyError" in str(cause)


def test_the_earliest_failure_is_the_one_reported(pool):
    def fails_with_its_index(index):
        raise ValueError(index)

    with pytest.raises(ValueError) as raised:
        pool.map(fails_with_its_index, [(i,) for i in range(5)])

    assert raised.value.args == (0,)


def test_a_killed_worker_is_reported_and_replaced(pool):
    with pytest.raises(WorkerDied) as died:
        pool.map(dying, [(1,)])
    assert "exit status -9" in str(died.value)

    # The pool is still usable: the dead worker is not handed out again.
    assert [value for _, value in pool.map(where_am_i, [(4,), (5,)])] == [8, 10]


def test_sys_exit_in_a_task_is_raised_here_not_obeyed_by_the_worker(pool):
    with pytest.raises(SystemExit) as raised:
        pool.map(exiting, [(3,)])

    assert raised.value.code == 3
    assert [value for _, value in pool.map(where_am_i, [(1,)])] == [2]


def test_workers_get_their_own_environment_by_position():
    """This is how one GPU is pinned per worker."""
    environments = [{"CLUSTRIX_TEST_DEVICE": "0"}, {"CLUSTRIX_TEST_DEVICE": "1"}]
    with WorkerPool(2, environments) as pinned:
        pinned.prestart()
        seen = pinned.map(
            lambda: (os.getpid(), os.environ["CLUSTRIX_TEST_DEVICE"]), [()] * 8
        )

    by_pid = {}
    for pid, device in seen:
        by_pid.setdefault(pid, set()).add(device)
    assert all(len(devices) == 1 for devices in by_pid.values())
    assert {d for devices in by_pid.values() for d in devices} <= {"0", "1"}


def test_timed_call_measures_the_work_not_the_trip(pool):
    ((seconds, pid),) = pool.map(timed_call, [(napping, 0.2)])

    assert 0.2 <= seconds < 0.6
    assert pid != os.getpid()


def test_a_closed_pool_refuses_work():
    pool = WorkerPool(1)
    pool.map(where_am_i, [(1,)])
    pool.close()

    with pytest.raises(RuntimeError, match="closed"):
        pool.map(where_am_i, [(1,)])


def test_a_pool_needs_at_least_one_worker():
    with pytest.raises(ValueError):
        WorkerPool(0)


def test_the_shared_pool_is_reused_and_grows_only_when_asked_for_more():
    small = shared_pool(2)
    assert shared_pool(1) is small
    assert shared_pool(2) is small
    bigger = shared_pool(small.size + 1)
    assert bigger is not small and bigger.size == small.size + 1


def test_a_scheduler_s_allocation_wins_over_the_affinity_mask(monkeypatch):
    """On a SLURM node without a task plugin, the affinity mask shows every
    core to a job that was granted one."""
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "3")
    assert available_cpus() == 3

    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "not-a-number")
    assert available_cpus() >= 1

    monkeypatch.delenv("SLURM_CPUS_PER_TASK")
    assert available_cpus() == len(os.sched_getaffinity(0))


def test_a_script_with_no_main_guard_is_not_re_run_inside_the_workers(tmp_path):
    """The reason this pool exists. ``multiprocessing``'s spawn start method
    re-imports the parent's ``__main__`` in every worker; for a plain script
    that means running the script again, including the call that asked for
    workers. Here the script's top level must run exactly once, and a module
    sitting beside the script must still be importable in the workers."""
    (tmp_path / "beside_the_script.py").write_text("def triple(x):\n    return 3 * x\n")
    script = tmp_path / "analysis.py"
    script.write_text(textwrap.dedent("""
            import os
            from clustrix.loop_workers import WorkerPool
            import beside_the_script

            print("TOP LEVEL", flush=True)

            def work(x):
                return os.getpid(), beside_the_script.triple(x)

            with WorkerPool(2) as pool:
                results = pool.map(work, [(i,) for i in range(4)])
            print("VALUES", [v for _, v in results])
            print("IN PARENT", os.getpid() in {p for p, _ in results})
            """))

    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.count("TOP LEVEL") == 1
    assert "VALUES [0, 3, 6, 9]" in completed.stdout
    assert "IN PARENT False" in completed.stdout
