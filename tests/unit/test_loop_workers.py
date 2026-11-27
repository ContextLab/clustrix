"""The local worker pool: real subprocesses, real pipes, real crashes.

Nothing here is simulated. Workers are started as separate interpreters and
tasks cross the process boundary pickled, exactly as they do when
``@cluster`` divides a loop among them.
"""

import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
import warnings

import pytest

from clustrix import loop_workers
from clustrix.loop_workers import (
    THREAD_COUNT_VARIABLES,
    NotPicklable,
    RemoteTraceback,
    WorkerDied,
    WorkerPool,
    available_cpus,
    in_worker,
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

    # ``emit`` is handed a TaskOutput per task now, not a string: standard
    # error and warnings travel with it (W4, W5).
    assert "".join(output.stdout for output in heard).splitlines() == [
        f"working on {i}" for i in range(6)
    ]


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
    """It grows *in place*. This used to assert that a larger request got a
    new pool object, which is the behaviour that killed another thread's
    loop: the old pool was closed under it. See the next test."""
    small = shared_pool(2)
    size = small.size
    assert shared_pool(1) is small and small.size == size
    assert shared_pool(size) is small and small.size == size
    assert shared_pool(size + 1) is small
    assert small.size == size + 1


def test_growing_the_shared_pool_does_not_kill_a_loop_already_running_on_it():
    """One thread is half-way through its tasks when another asks for a
    wider pool. Replacing the pool closed the first thread's workers, and
    its tasks came back as ``WorkerDied``."""
    running = shared_pool(2)
    outcome = {}

    def long_loop():
        try:
            outcome["pids"] = running.map(napping, [(0.6,), (0.6,)], limit=2)
        except BaseException as error:  # noqa: BLE001 - reported by the assert
            outcome["error"] = error

    thread = threading.Thread(target=long_loop)
    thread.start()
    time.sleep(0.2)  # its tasks are in the workers now
    wider = shared_pool(running.size + 1)
    thread.join(timeout=60)

    assert "error" not in outcome, f"the running loop was broken: {outcome}"
    assert len(outcome["pids"]) == 2 and os.getpid() not in outcome["pids"]
    assert wider is running
    assert wider.map(where_am_i, [(3,)])[0][1] == 6


def test_threads_sharing_a_pool_never_start_more_workers_than_its_size():
    """A task that finds every worker busy waits for one. It used to start
    another: four threads on a pool of two left eight processes running, and
    they were kept, so the "pool of two" stayed eight wide."""
    results = {}

    with WorkerPool(2) as pool:

        def use_the_pool(index):
            results[index] = pool.map(napping, [(0.15,)] * 4)

        threads = [
            threading.Thread(target=use_the_pool, args=(index,)) for index in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert sorted(results) == [0, 1, 2, 3]
        pids = {pid for answers in results.values() for pid in answers}
        assert len(pids) <= 2, f"a pool of 2 ran its tasks in {len(pids)} processes"
        assert os.getpid() not in pids
        assert all(len(answers) == 4 for answers in results.values())


def test_a_worker_knows_it_is_one_and_this_process_knows_it_is_not(pool):
    """What stops a decorated function called inside a divided loop from
    starting a pool of its own in every worker."""
    assert not in_worker()
    assert pool.map(in_worker, [(), ()]) == [True, True]


def thread_settings():
    return {name: os.environ.get(name) for name in THREAD_COUNT_VARIABLES}


@pytest.fixture
def no_shared_pool(monkeypatch):
    """The shared pool is one per process: start each of these tests without
    one, and without thread counts inherited from whoever runs the suite."""
    loop_workers._close_shared_pool()
    for name in THREAD_COUNT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    yield
    loop_workers._close_shared_pool()


def test_shared_workers_are_each_given_their_share_of_the_cpus(no_shared_pool):
    """Numerical libraries size their thread pools to the whole machine in
    every process that imports them; N workers would run N times too many
    threads. The variables have to be in place when the worker starts."""
    share = str(max(1, available_cpus() // 2))

    seen = shared_pool(2).map(thread_settings, [(), (), (), ()])

    assert seen == [dict.fromkeys(THREAD_COUNT_VARIABLES, share)] * 4
    assert set(THREAD_COUNT_VARIABLES) >= {"OMP_NUM_THREADS", "MKL_NUM_THREADS"}


def test_a_thread_count_the_user_set_is_left_alone(no_shared_pool, monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    share = str(max(1, available_cpus() // 2))

    seen = shared_pool(2).map(thread_settings, [(), ()])

    for settings in seen:
        assert settings["OMP_NUM_THREADS"] == "3"
        assert settings["OPENBLAS_NUM_THREADS"] == share


def test_a_pool_built_directly_sets_no_thread_counts(no_shared_pool):
    with WorkerPool(2) as neutral:
        seen = neutral.map(thread_settings, [(), ()])

    assert seen == [dict.fromkeys(THREAD_COUNT_VARIABLES)] * 2


def test_growing_the_shared_pool_shrinks_every_worker_s_share(no_shared_pool):
    """A worker's environment is fixed when it starts, so one started for a
    narrower pool is retired rather than left with a share that is too big."""
    cpus = available_cpus()
    narrow = shared_pool(1)
    (before,) = narrow.map(lambda: (os.getpid(), thread_settings()), [()])
    assert before[1]["OMP_NUM_THREADS"] == str(cpus)

    wide = shared_pool(2)
    after = wide.map(lambda: (napping(0.2), thread_settings()), [(), ()], limit=2)

    assert wide is narrow
    assert [settings["OMP_NUM_THREADS"] for _, settings in after] == [
        str(max(1, cpus // 2))
    ] * 2
    assert len({pid for pid, _ in after}) == 2
    assert before[0] not in {pid for pid, _ in after}, "the old worker was kept"


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


# -- what an adversarial review found ------------------------------------------


PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(loop_workers.__file__)))


def reads_relative(name):
    with open(name) as handle:
        return os.getcwd(), handle.read()


def sets_a_variable(name):
    seen = os.environ.get(name)
    os.environ[name] = "left behind by a task"
    return seen


class Odd(Exception):
    """An exception that pickles and then cannot be rebuilt from its args."""

    def __init__(self, a, b):
        super().__init__(a)
        self.b = b


def raises_odd(value):
    raise Odd(f"odd failure {value}", "second argument")


def warns(value):
    for _ in range(3):
        warnings.warn(f"careful with {value}", DeprecationWarning)
    print(f"out {value}")
    print(f"err {value}", file=sys.stderr)
    return value


def test_a_task_runs_in_the_directory_the_caller_is_in_now(pool, tmp_path, monkeypatch):
    """W1. Workers outlive an ``os.chdir``; they read the old directory's
    file, or none."""
    for name in ("first", "second"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "input.txt").write_text(f"from {name}")

    monkeypatch.chdir(tmp_path / "first")
    before = pool.map(reads_relative, [("input.txt",)] * 4)
    monkeypatch.chdir(tmp_path / "second")
    after = pool.map(reads_relative, [("input.txt",)] * 4)

    assert set(before) == {(str(tmp_path / "first"), "from first")}
    assert set(after) == {(str(tmp_path / "second"), "from second")}


def test_a_task_sees_the_environment_the_caller_has_now(pool, monkeypatch):
    """W1. Set, changed and removed after the workers started."""
    name = "CLUSTRIX_TEST_MODE"
    monkeypatch.delenv(name, raising=False)
    pool.map(reads_environment, [(name,)] * 6)  # every worker started

    monkeypatch.setenv(name, "fast")
    assert set(pool.map(reads_environment, [(name,)] * 6)) == {"fast"}
    monkeypatch.setenv(name, "slow")
    assert set(pool.map(reads_environment, [(name,)] * 6)) == {"slow"}
    monkeypatch.delenv(name)
    assert set(pool.map(reads_environment, [(name,)] * 6)) == {None}


def test_a_variable_one_task_set_is_not_there_for_the_next(monkeypatch):
    name = "CLUSTRIX_TEST_LEFTOVER"
    monkeypatch.delenv(name, raising=False)
    with WorkerPool(1) as single:
        assert single.map(sets_a_variable, [(name,)] * 3) == [None, None, None]


def test_what_the_pool_pinned_is_not_overridden_by_the_caller(monkeypatch):
    """One GPU per worker must survive the caller having its own setting,
    and a worker must go on knowing it is one."""
    monkeypatch.setenv("CLUSTRIX_TEST_DEVICE", "caller")
    monkeypatch.delenv(loop_workers.IN_WORKER_VARIABLE, raising=False)
    with WorkerPool(1, [{"CLUSTRIX_TEST_DEVICE": "3"}]) as pinned:
        first = pinned.map(reads_environment, [("CLUSTRIX_TEST_DEVICE",)])
        monkeypatch.setenv("CLUSTRIX_TEST_DEVICE", "changed")
        second = pinned.map(reads_environment, [("CLUSTRIX_TEST_DEVICE",)])
        marker = pinned.map(reads_environment, [(loop_workers.IN_WORKER_VARIABLE,)])

    assert (first, second, marker) == (["3"], ["3"], ["1"])


def test_a_thread_count_is_the_pool_s_until_the_caller_sets_one(monkeypatch):
    name = THREAD_COUNT_VARIABLES[0]
    monkeypatch.delenv(name, raising=False)
    with WorkerPool(1, [{name: "3"}]) as limited:
        assert limited.map(reads_environment, [(name,)]) == ["3"]
        monkeypatch.setenv(name, "7")
        assert limited.map(reads_environment, [(name,)]) == ["7"]
        monkeypatch.delenv(name)
        assert limited.map(reads_environment, [(name,)]) == ["3"]


def test_an_exception_pickle_cannot_rebuild_still_arrives_as_itself(pool):
    """W2. It used to arrive as ``TypeError: Odd.__init__() missing 1
    required positional argument``, raised by unpickling in this process."""
    with pytest.raises(Odd) as raised:
        pool.map(raises_odd, [(1,)])

    print(repr(raised.value), raised.value.__cause__)
    assert raised.value.args == ("odd failure 1",)
    assert raised.value.b == "second argument"
    assert isinstance(raised.value.__cause__, RemoteTraceback)
    assert "in raises_odd" in str(raised.value.__cause__)
    assert [value for _, value in pool.map(where_am_i, [(1,)])] == [2]


def test_an_exception_that_cannot_travel_at_all_arrives_described(pool):
    def raises_held():
        import threading

        class Held(Exception):
            def __init__(self, message, lock):
                super().__init__(message)
                self.lock = lock

        raise Held("holding a lock", threading.Lock())

    with pytest.raises(RuntimeError) as raised:
        pool.map(raises_held, [()])

    assert str(raised.value) == "Held: holding a lock"
    assert "in raises_held" in str(raised.value.__cause__)


def test_warnings_from_a_task_reach_the_caller_s_filters(pool):
    """W4. They were printed as text by the worker, so ``catch_warnings``
    here recorded nothing and ``-W error`` did not raise."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pool.map(warns, [(1,), (2,)], emit=lambda output: output.replay())

    messages = [(str(w.message), w.category) for w in caught]
    print(messages)
    assert messages == [
        ("careful with 1", DeprecationWarning),
        ("careful with 2", DeprecationWarning),
    ], "in task order, and once per task for one line"
    assert all(w.filename == __file__ for w in caught)
    assert caught[0].lineno == warns.__code__.co_firstlineno + 2

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(DeprecationWarning, match="careful with 5"):
            pool.map(warns, [(5,)])


def test_a_warning_category_that_cannot_travel_becomes_a_user_warning(pool):
    def warns_oddly():
        import threading

        class Held(UserWarning):
            lock = threading.Lock()

        warnings.warn("held", Held)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pool.map(warns_oddly, [()])

    assert [(str(w.message), w.category) for w in caught] == [("held", UserWarning)]


def test_standard_error_is_replayed_on_standard_error(pool, capsys):
    """W5. It used to come out on the caller's standard output."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pool.map(warns, [(1,), (2,)])

    shown = capsys.readouterr()
    assert shown.out == "out 1\nout 2\n"
    assert shown.err == "err 1\nerr 2\n"


def test_a_task_that_cannot_be_pickled_says_which_part(pool):
    """D2. ``NotPicklable`` is what lets ``@cluster`` run the loop here."""
    lock = threading.Lock()

    with pytest.raises(NotPicklable) as refused:
        pool.map(timed_call, [(len, {"total": 0, "self": lock}, [1, 2])])
    with pytest.raises(NotPicklable) as positional:
        pool.map(len, [(lock,)])

    print(refused.value, "|", positional.value)
    assert isinstance(refused.value, TypeError)
    assert "'self' (a lock)" in str(refused.value)
    assert "argument 0 (a lock)" in str(positional.value)
    assert [value for _, value in pool.map(where_am_i, [(1,)])] == [2]


def test_an_interrupt_does_not_wait_for_the_tasks_or_leave_workers(tmp_path):
    """W3. Ctrl-C during a divided loop used to return only when every
    chunk had finished. The workers busy with the abandoned tasks are killed
    and the interrupt is raised at once."""
    script = tmp_path / "driver.py"
    script.write_text(textwrap.dedent("""
            import signal, time
            from clustrix.loop_workers import WorkerPool

            signal.signal(signal.SIGINT, signal.default_int_handler)

            def nap(seconds):
                time.sleep(seconds)

            pool = WorkerPool(3)
            pool.map(nap, [(0,)] * 3)
            print("READY", flush=True)
            pool.map(nap, [(6,)] * 5)
            print("FINISHED", flush=True)
            """))
    driver = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        # The package under test, wherever this test was collected from.
        env={**os.environ, "PYTHONPATH": PACKAGE_ROOT},
    )
    try:
        assert driver.stdout.readline().strip() == "READY"
        time.sleep(1.0)
        workers = subprocess.run(
            ["pgrep", "-g", str(driver.pid), "-f", "clustrix.loop_workers"],
            capture_output=True,
            text=True,
        ).stdout.split()
        assert len(workers) == 3
        interrupted = time.perf_counter()
        os.kill(driver.pid, signal.SIGINT)
        out, err = driver.communicate(timeout=30)
        elapsed = time.perf_counter() - interrupted
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(driver.pid, signal.SIGKILL)

    print(f"exited {elapsed:.2f}s after SIGINT, status {driver.returncode}\n{err}")
    assert "KeyboardInterrupt" in err and "FINISHED" not in out
    assert elapsed < 3, f"waited {elapsed:.1f}s for 6-second tasks nobody wants"
    left = subprocess.run(
        ["pgrep", "-g", str(driver.pid), "-f", "clustrix.loop_workers"],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert left == [], f"worker processes left behind: {left}"


@pytest.mark.parametrize("how", [signal.SIGKILL, signal.SIGTERM])
def test_workers_do_not_outlive_a_parent_that_was_killed(tmp_path, how):
    """#176 W1. A real ``@cluster`` loop in a real process that is then
    killed. Its workers used to finish their chunks -- every remaining
    iteration ran, for nobody. Each iteration leaves a marker file when it
    starts, so "kept computing" is something that can be counted."""
    markers = tmp_path / "markers"
    markers.mkdir()
    script = tmp_path / "driver.py"
    script.write_text(textwrap.dedent("""
            import os, sys, time
            from clustrix import cluster, configure

            configure(cluster_type="local", cluster_host=None)
            MARKERS = sys.argv[1]

            @cluster(cores=3)
            def work(n):
                out = []
                for i in range(n):
                    open(f"{MARKERS}/iter_{i}_{os.getpid()}", "w").close()
                    time.sleep(2)
                    out.append(i)
                return out

            work(12)
            print("FINISHED", flush=True)
            """))
    driver = subprocess.Popen(
        [sys.executable, str(script), str(markers)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
        env={**os.environ, "PYTHONPATH": PACKAGE_ROOT, "HOME": str(tmp_path)},
    )

    def workers():
        return subprocess.run(
            ["pgrep", "-g", str(driver.pid), "-f", "clustrix.loop_workers"],
            capture_output=True,
            text=True,
        ).stdout.split()

    try:
        deadline = time.monotonic() + 60
        while len(os.listdir(markers)) < 3 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert len(os.listdir(markers)) >= 3, "the loop never started in 3 workers"
        assert len(workers()) == 3
        os.kill(driver.pid, how)
        driver.wait(timeout=30)
        killed = time.monotonic()
        while workers() and time.monotonic() - killed < 5:
            time.sleep(0.1)
        gone_after = time.monotonic() - killed
        left = workers()
        started = sorted(os.listdir(markers))
        time.sleep(5)  # two more iterations' worth, had anything survived
        later = sorted(os.listdir(markers))
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(driver.pid, signal.SIGKILL)

    print(
        f"{how.name}: workers gone {gone_after:.2f}s after the parent; "
        f"{len(started)} iteration(s) had started, {len(later)} five seconds on"
    )
    assert left == [], f"workers outlived their parent: {left}"
    assert gone_after < 3
    assert later == started, "iterations kept starting after the parent died"
    assert len(later) < 12 and "FINISHED" not in driver.stdout.read()


def pid_after_a_pause(seconds):
    time.sleep(seconds)
    return os.getpid()


def test_a_worker_whose_parent_is_alive_is_left_alone_between_calls():
    """The watch must not fire on its own: the thread that started a worker
    ends with the call that needed it, and the worker is kept."""
    with WorkerPool(2) as pool:
        starter = threading.Thread(target=pool.prestart)
        starter.start()
        starter.join()
        first = set(pool.map(pid_after_a_pause, [(0.5,), (0.5,)]))
        time.sleep(2.5)  # more than two of the watch's checks
        second = set(pool.map(pid_after_a_pause, [(0.5,), (0.5,)]))

    assert first == second and len(first) == 2
