"""``async_submit``: a handle now, the answer later.

Every job here really runs. The local ones run on the executor's own worker
threads and are held open with ``threading.Event`` objects the test controls,
so "still running", "finished" and "queued behind another job" are states the
test puts the job in rather than states it hopes to catch with a sleep. The
remote ones run over a real SSH connection to ``tests/ssh_server.py``.

The remote test this replaces patched ``clustrix.executor.ClusterExecutor``
-- a name the async executor does not look up -- and asserted the returned
object had a ``job_id`` attribute.
"""

import os
import sys
import threading
import time

import pytest

from clustrix import cluster, configure
from clustrix.async_executor_simple import AsyncJobResult, SimpleAsyncClusterExecutor
from clustrix.config import ClusterConfig
from tests.ssh_server import LocalSSHServer

#: Long enough that a wait on it only ends this way when something is broken.
STUCK = 30


class TestAsyncExecution:
    """``@cluster(async_submit=True)`` on the local backend."""

    def test_the_call_returns_while_the_job_is_still_running(self):
        configure(cluster_type="local")
        started, release = threading.Event(), threading.Event()

        @cluster(async_submit=True)
        def held_open(x):
            started.set()
            assert release.wait(STUCK)
            return x * 2

        result = held_open(5)
        try:
            assert isinstance(result, AsyncJobResult)
            assert started.wait(STUCK), "the job never started"
            # The job is provably inside its body, and the caller has its
            # handle back: that is what asynchronous means.
            assert result.get_status() == "running"
            assert result.is_complete() is False
        finally:
            release.set()

        assert result.get_result(timeout=STUCK) == 10
        assert result.get_status() == "completed"
        assert result.is_complete() is True

    def test_the_job_runs_on_another_thread_of_this_process(self):
        configure(cluster_type="local")

        @cluster(async_submit=True)
        def who():
            import os
            import threading

            return os.getpid(), threading.get_ident()

        pid, thread = who().get_result(timeout=STUCK)

        assert pid == os.getpid()
        assert thread != threading.get_ident()

    def test_job_id_runtime_and_wait(self):
        configure(cluster_type="local")

        @cluster(async_submit=True)
        def quick_task(x):
            return x + 1

        first, second = quick_task(42), quick_task(1)

        assert first.job_id.startswith("async_")
        assert first.job_id != second.job_id
        earlier = first.get_runtime()
        time.sleep(0.05)
        assert first.get_runtime() >= earlier + 0.05
        # wait() is get_result() under another name.
        assert first.wait(timeout=STUCK) == 43
        assert second.wait(timeout=STUCK) == 2

    def test_a_timeout_does_not_lose_the_job(self):
        configure(cluster_type="local")
        release = threading.Event()

        @cluster(async_submit=True)
        def held_open():
            assert release.wait(STUCK)
            return "done"

        result = held_open()
        try:
            with pytest.raises(TimeoutError, match=result.job_id):
                result.get_result(timeout=0.05)
            assert result.get_status() == "running"
        finally:
            release.set()

        assert result.get_result(timeout=STUCK) == "done"

    def test_a_job_that_raised_reports_failed_and_carries_the_original(self):
        """``get_result`` raises RuntimeError, as documented, chained to the cause."""
        configure(cluster_type="local")

        @cluster(async_submit=True)
        def failing_task():
            raise ValueError("Test error")

        result = failing_task()

        with pytest.raises(RuntimeError, match="Test error") as raised:
            result.get_result(timeout=STUCK)

        assert result.job_id in str(raised.value)
        assert isinstance(raised.value.__cause__, ValueError)
        assert result.get_status() == "failed"

    def test_sync_and_async_give_the_same_answer(self):
        configure(cluster_type="local")

        def multiply_add(x, y):
            return x * y + 10

        sync_result = cluster(async_submit=False)(multiply_add)(3, 4)
        async_result = cluster(async_submit=True)(multiply_add)(3, 4)

        assert sync_result == 22
        assert async_result.get_result(timeout=STUCK) == 22

    def test_the_global_setting_applies_when_the_decorator_says_nothing(self):
        configure(cluster_type="local", async_submit=True)

        @cluster()
        def triple(x):
            return x * 3

        @cluster(async_submit=False)
        def triple_now(x):
            return x * 3

        handle = triple(7)

        assert isinstance(handle, AsyncJobResult)
        assert handle.get_result(timeout=STUCK) == 21
        # The decorator's own word beats the global one.
        assert triple_now(7) == 21

    def test_three_jobs_are_in_flight_at_the_same_time(self):
        """A barrier only releases when all three are inside the function."""
        configure(cluster_type="local")
        together = threading.Barrier(3)

        @cluster(async_submit=True)
        def meet(task_id):
            together.wait(timeout=STUCK)
            return f"task_{task_id}_done"

        handles = [meet(i) for i in range(3)]

        assert [h.get_result(timeout=STUCK) for h in handles] == [
            "task_0_done",
            "task_1_done",
            "task_2_done",
        ]
        assert all(h.get_status() == "completed" for h in handles)


class TestCancellation:
    """What ``cancel()`` can and cannot do, shown rather than assumed.

    The old test called ``cancel()`` on a five-second job and asserted the
    return value was a ``bool`` -- true of both outcomes.
    """

    def test_a_job_already_running_cannot_be_cancelled_and_still_finishes(self):
        started, release = threading.Event(), threading.Event()

        def held_open():
            started.set()
            assert release.wait(STUCK)
            return "ran to the end"

        with SimpleAsyncClusterExecutor(ClusterConfig(cluster_type="local")) as pool:
            handle = pool.submit_job_async(held_open, (), {}, {})
            try:
                assert started.wait(STUCK)
                assert handle.cancel() is False
                assert handle.get_status() == "running"
            finally:
                release.set()
            assert handle.get_result(timeout=STUCK) == "ran to the end"
            # Cancelling something finished is also a no.
            assert handle.cancel() is False

    def test_a_job_still_queued_is_cancelled_and_says_so(self):
        """One worker, held busy, so the second job is provably queued."""
        started, release = threading.Event(), threading.Event()
        ran = []

        def held_open():
            started.set()
            assert release.wait(STUCK)
            return "first"

        def never_runs():
            ran.append("second")
            return "second"

        config = ClusterConfig(cluster_type="local")
        with SimpleAsyncClusterExecutor(config, max_workers=1) as pool:
            first = pool.submit_job_async(held_open, (), {}, {})
            assert started.wait(STUCK)
            second = pool.submit_job_async(never_runs, (), {}, {})
            try:
                assert second.cancel() is True
                assert second.is_complete() is True
                assert second.get_status() == "cancelled"
                with pytest.raises(RuntimeError, match="was cancelled"):
                    second.get_result(timeout=STUCK)
                assert pool.get_status_summary() == {
                    "running": 1,
                    "completed": 0,
                    "failed": 0,
                    "cancelled": 1,
                }
            finally:
                release.set()
            assert first.get_result(timeout=STUCK) == "first"

        assert ran == [], "a cancelled job ran anyway"


class TestTheExecutorItself:
    """``SimpleAsyncClusterExecutor``'s own bookkeeping, on real jobs."""

    def test_wait_for_all_returns_each_result_or_the_error_that_ended_it(self):
        def double(x):
            return x * 2

        def explode():
            raise KeyError("missing")

        config = ClusterConfig(cluster_type="local", cleanup_on_success=False)
        with SimpleAsyncClusterExecutor(config) as pool:
            good = pool.submit_job_async(double, (21,), {}, {})
            bad = pool.submit_job_async(explode, (), {}, {})

            results = pool.wait_for_all(timeout=STUCK)

            assert results[good.job_id] == 42
            assert isinstance(results[bad.job_id], RuntimeError)
            assert "missing" in str(results[bad.job_id])
            assert pool.get_status_summary() == {
                "running": 0,
                "completed": 1,
                "failed": 1,
                "cancelled": 0,
            }
            assert set(pool.get_active_jobs()) == {good.job_id, bad.job_id}

    def test_finished_jobs_are_forgotten_when_cleanup_is_on(self):
        config = ClusterConfig(cluster_type="local", cleanup_on_success=True)
        with SimpleAsyncClusterExecutor(config) as pool:
            handle = pool.submit_job_async(lambda: "done", (), {}, {})
            assert handle.get_result(timeout=STUCK) == "done"

            assert pool.get_active_jobs() == {}

    def test_cancel_all_cancels_what_is_queued_and_counts_it(self):
        started, release = threading.Event(), threading.Event()

        def held_open():
            started.set()
            assert release.wait(STUCK)

        config = ClusterConfig(cluster_type="local")
        with SimpleAsyncClusterExecutor(config, max_workers=1) as pool:
            pool.submit_job_async(held_open, (), {}, {})
            assert started.wait(STUCK)
            queued = [pool.submit_job_async(held_open, (), {}, {}) for _ in range(3)]
            try:
                assert pool.cancel_all() == 3
                assert [h.get_status() for h in queued] == ["cancelled"] * 3
            finally:
                release.set()

    def test_leaving_the_with_block_stops_the_worker_threads(self):
        before = threading.active_count()
        with SimpleAsyncClusterExecutor(ClusterConfig(cluster_type="local")) as pool:
            handles = [pool.submit_job_async(lambda: 1, (), {}, {}) for _ in range(4)]
            assert [h.get_result(timeout=STUCK) for h in handles] == [1, 1, 1, 1]

        assert threading.active_count() == before
        with pytest.raises(RuntimeError):
            pool.submit_job_async(lambda: 1, (), {}, {})


class TestAsyncOverSSH:
    """The same handle, with the job on the far side of a real connection."""

    @pytest.fixture
    def ssh_cluster(self, tmp_path):
        remote = tmp_path / "remote"
        remote.mkdir()
        with LocalSSHServer(root=str(remote), password="s3cret") as server:
            configure(
                cluster_type="ssh",
                cluster_host=server.host,
                cluster_port=server.port,
                username="tester",
                password="s3cret",
                ssh_host_key_policy="auto_add",
                remote_work_dir=str(remote / "jobs"),
                replicate_local_environment=False,
                python_executable=sys.executable,
            )
            yield server

    def test_a_remote_job_resolves_to_what_the_remote_process_returned(
        self, ssh_cluster
    ):
        @cluster(cores=1, async_submit=True)
        def remote_task(data):
            import os
            import time

            time.sleep(2)
            return f"processed_{data}", os.getpid()

        submitted = time.monotonic()
        handle = remote_task("test_data")
        returned_after = time.monotonic() - submitted

        assert isinstance(handle, AsyncJobResult)
        assert returned_after < 2, "the call waited for the remote job"
        assert handle.get_status() == "running"

        value, remote_pid = handle.get_result(timeout=120)

        assert value == "processed_test_data"
        assert remote_pid != os.getpid()
        assert handle.get_status() == "completed"
        assert ("tester", "password") in ssh_cluster.authentications

    def test_a_remote_failure_reaches_the_handle(self, ssh_cluster):
        @cluster(cores=1, async_submit=True)
        def remote_failure():
            raise ValueError("raised on the far side")

        handle = remote_failure()

        with pytest.raises(RuntimeError, match="raised on the far side") as raised:
            handle.get_result(timeout=120)

        assert isinstance(raised.value.__cause__, ValueError)
        assert handle.get_status() == "failed"
