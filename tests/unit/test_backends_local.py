"""Real tests for cluster_type "local" (#120).

No mocks and nothing to skip: these run the real executor on this machine.
Before the fix, every one of them ended in
``ValueError: Unsupported cluster type: local``.
"""

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_core import ClusterExecutor
from clustrix.utils import serialize_function


def add(a, b):
    return a + b


def sum_squares(n):
    return sum(i * i for i in range(n))


def divide(a, b):
    return a / b


def _executor():
    return ClusterExecutor(ClusterConfig(cluster_type="local"))


def test_local_cluster_type_executes_and_returns_the_right_answer():
    executor = _executor()
    job_id = executor.submit_job(serialize_function(add, (2, 40), {}), {"cores": 2})

    assert job_id.startswith("local_")
    assert executor.get_job_status(job_id) == "completed"
    assert executor.wait_for_result(job_id) == 42


def test_local_cluster_type_handles_kwargs_and_real_computation():
    executor = _executor()
    job_id = executor.submit_job(
        serialize_function(sum_squares, (), {"n": 10}), {"cores": 1}
    )

    assert executor.wait_for_result(job_id) == 285


def test_local_failure_raises_the_original_exception():
    executor = _executor()
    job_id = executor.submit_job(serialize_function(divide, (1, 0), {}), {"cores": 1})

    assert executor.get_job_status(job_id) == "failed"
    assert "ZeroDivisionError" in executor._get_error_log(job_id)

    with pytest.raises(ZeroDivisionError):
        executor.wait_for_result(job_id)


def test_local_result_is_only_delivered_once():
    executor = _executor()
    job_id = executor.submit_job(serialize_function(add, (1, 1), {}), {"cores": 1})

    assert executor.wait_for_result(job_id) == 2
    with pytest.raises(ValueError, match="Unknown local job ID"):
        executor.local_manager.wait_for_result(job_id)


def test_local_job_cancellation_does_not_claim_a_lie():
    """The work has already run; reporting a cancellation would be false."""
    executor = _executor()
    job_id = executor.submit_job(serialize_function(add, (1, 2), {}), {"cores": 1})

    with pytest.raises(RuntimeError, match="cannot be cancelled"):
        executor.cancel_job(job_id)


def test_unknown_cluster_type_still_fails_loudly():
    executor = ClusterExecutor(ClusterConfig(cluster_type="not-a-cluster"))
    with pytest.raises(Exception):
        executor.submit_job(serialize_function(add, (1, 2), {}), {"cores": 1})
