import os
import socket

import paramiko
import pytest

from clustrix.decorator import cluster
from clustrix.config import configure


class TestClusterDecorator:
    """Test the @cluster decorator."""

    def test_basic_decoration(self):
        """Test basic function decoration."""

        @cluster(cores=8, memory="16GB")
        def test_func(x, y):
            return x + y

        assert hasattr(test_func, "__wrapped__")
        assert hasattr(test_func, "_cluster_config")
        assert test_func._cluster_config["cores"] == 8
        assert test_func._cluster_config["memory"] == "16GB"

    def test_decorator_without_params(self):
        """Test decorator without parameters."""

        @cluster
        def test_func(x):
            return x * 2

        assert hasattr(test_func, "__wrapped__")
        assert hasattr(test_func, "_cluster_config")
        # All parameters should be None when not specified
        expected_config = {
            "cores": None,
            "memory": None,
            "time": None,
            "partition": None,
            "gpus": None,
            # No "queue": it was accepted, resolved into job_config and read by
            # nothing, so #158 removed it. @cluster(queue=...) now lands in
            # **kwargs and hits the unrecognised-option warning.
            "parallel": None,
            "auto_gpu_parallel": None,
            "environment": None,
            "async_submit": None,
        }
        assert test_func._cluster_config == expected_config

    def test_decorator_with_all_params(self):
        """Test decorator with all possible parameters."""

        @cluster(
            cores=16,
            memory="32GB",
            time="04:00:00",
            partition="gpu",
            parallel=True,
            auto_gpu_parallel=True,
            environment="test_env",
        )
        def test_func():
            return "test"

        config = test_func._cluster_config
        assert config["cores"] == 16
        assert config["memory"] == "32GB"
        assert config["time"] == "04:00:00"
        assert config["partition"] == "gpu"
        assert config["parallel"] is True
        assert config["auto_gpu_parallel"] is True
        assert config["environment"] == "test_env"

    def test_with_no_cluster_the_function_runs_here_on_the_callers_own_objects(self):
        """No host configured means a plain call: same process, same objects.

        The mocked version patched ``clustrix.executor.ClusterExecutor`` --
        not the name the decorator uses -- and asserted the patch was never
        called, which was true whatever the decorator did. What local
        execution means is observable without it: the function sees this
        process, and the very list it was handed, not a copy that crossed a
        serializer on the way to somewhere else.
        """
        configure(cluster_host=None)
        handed_in = [1, 2]

        @cluster(cores=4)
        def remembers(values, extra):
            values.append(extra)
            return os.getpid(), values

        pid, returned = remembers(handed_in, 3)

        assert pid == os.getpid()
        assert returned is handed_in
        assert handed_in == [1, 2, 3]

    def test_function_metadata_preserved(self):
        """Test that function metadata is preserved."""

        @cluster(cores=4)
        def documented_function(x, y):
            """This function adds two numbers."""
            return x + y

        assert documented_function.__name__ == "documented_function"
        assert documented_function.__doc__ == "This function adds two numbers."

    def test_a_cluster_that_cannot_be_reached_raises_and_does_not_run_locally(
        self, tmp_path
    ):
        """A failed submission is the caller's error, never a quiet local run.

        The mocked version told a ``Mock`` executor to raise ``RuntimeError``
        from ``submit_job`` and asserted a ``RuntimeError`` came out. This one
        has a real failure to propagate: the configured port is one nothing
        is listening on, so the SSH connection is really refused.
        """
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        configure(
            cluster_type="ssh",
            cluster_host="127.0.0.1",
            cluster_port=closed_port,
            username="tester",
            password="wrong_password",
            remote_work_dir=str(tmp_path),
        )
        ran_here = []

        @cluster(cores=1)
        def job():
            ran_here.append(os.getpid())
            return "an answer nobody computed remotely"

        with pytest.raises(paramiko.ssh_exception.NoValidConnectionsError) as raised:
            job()

        assert str(closed_port) in str(raised.value)
        assert ran_here == [], "the function ran on this machine instead"

    def test_parallel_flag(self):
        """Test parallel execution flag."""

        @cluster(parallel=True)
        def parallel_func(data):
            return [x * 2 for x in data]

        @cluster(parallel=False)
        def sequential_func(data):
            return [x * 2 for x in data]

        assert parallel_func._cluster_config["parallel"] is True
        assert sequential_func._cluster_config["parallel"] is False

    def test_kwargs_handling(self):
        """Keyword arguments must reach the function that runs on the cluster.

        The previous version of this test asserted that a mocked executor's
        ``submit_job`` had been called once and that the decorator returned
        whatever the mock's ``wait_for_result`` was told to return. Neither
        statement mentions the kwargs, so deleting the kwargs entirely from
        ``decorator._execute_single`` -- ``serialize_function(func, args, {})``
        -- left all 38 tests in this file green. It asserted that the call
        completed, which was never in doubt.

        This version asserts on the values the function actually received.
        Nothing is mocked: ``cluster_type="local"`` is a real backend, so the
        decorator builds a real ``ClusterExecutor``, really serialises the
        function through ``serialize_function``, and really runs it. A
        ``cluster_host`` is set because that is what makes
        ``_choose_execution_mode`` take the remote branch -- the branch where
        the kwargs pass-through lives.
        """
        configure(
            cluster_type="local",
            cluster_host="localhost",
            auto_parallel=False,
        )

        @cluster(cores=1)
        def records_what_it_received(a, b=10, c=20):
            return {"a": a, "b": b, "c": c, "total": a + b + c}

        result = records_what_it_received(5, c=30)

        assert result == {"a": 5, "b": 10, "c": 30, "total": 45}, (
            "the function ran on the cluster with the wrong arguments. "
            f"Got {result!r}. A 'c' of 20 means the keyword argument was "
            "dropped somewhere between the decorator and serialisation."
        )

    def test_decorator_stacking(self):
        """Test that decorator can be combined with other decorators."""

        def other_decorator(func):
            def wrapper(*args, **kwargs):
                return func(*args, **kwargs) * 2

            return wrapper

        @other_decorator
        @cluster(cores=4)
        def test_func(x):
            return x + 1

        # When executed locally
        configure(cluster_host=None)
        result = test_func(5)
        assert result == 12  # (5 + 1) * 2

    def test_decorator_order_matters(self):
        """Test that decorator order affects execution."""

        def multiply_decorator(factor):
            def decorator(func):
                def wrapper(*args, **kwargs):
                    return func(*args, **kwargs) * factor

                return wrapper

            return decorator

        # cluster decorator inside
        @multiply_decorator(3)
        @cluster(cores=2)
        def func1(x):
            return x + 5

        # cluster decorator outside
        @cluster(cores=2)
        @multiply_decorator(3)
        def func2(x):
            return x + 5

        configure(cluster_host=None)

        result1 = func1(10)
        result2 = func2(10)

        assert result1 == 45  # (10 + 5) * 3
        assert result2 == 45  # (10 + 5) * 3 - both should be same for local execution

    def test_resource_parameter_validation(self):
        """Test validation of resource parameters."""

        # Test valid parameters
        @cluster(cores=8, memory="16GB", time="02:00:00")
        def valid_func():
            return "test"

        config = valid_func._cluster_config
        assert config["cores"] == 8
        assert config["memory"] == "16GB"
        assert config["time"] == "02:00:00"

    def test_job_config_inheritance(self):
        """Test job config inheritance from global config."""
        configure(
            default_cores=16,
            default_memory="32GB",
            default_time="04:00:00",
            default_partition="gpu",
        )

        # Test decorator without parameters inherits defaults
        @cluster()
        def default_func():
            return "test"

        # Test decorator with partial override
        @cluster(cores=8, memory="64GB")
        def override_func():
            return "test"

        configure(cluster_host=None)  # Local execution for testing

        # The config should be stored but actual values come from get_config() during execution
        # For @cluster() without parameters, cores should be None (not specified)
        assert default_func._cluster_config["cores"] is None
        assert override_func._cluster_config["cores"] == 8
        assert override_func._cluster_config["memory"] == "64GB"

    def test_execution_mode_selection(self):
        """Test execution mode selection logic."""
        from clustrix.decorator import _choose_execution_mode
        from clustrix.config import ClusterConfig

        # Test local mode when no cluster host
        config_no_host = ClusterConfig(cluster_host=None)
        mode = _choose_execution_mode(config_no_host, lambda: None, (), {})
        assert mode == "local"

        # Test remote mode when cluster host is set
        config_with_host = ClusterConfig(cluster_host="test.cluster.com")
        mode = _choose_execution_mode(config_with_host, lambda: None, (), {})
        assert mode == "remote"

        # Test local mode when prefer_local_parallel is set
        config_prefer_local = ClusterConfig(
            cluster_host="test.cluster.com", prefer_local_parallel=True
        )
        mode = _choose_execution_mode(config_prefer_local, lambda: None, (), {})
        assert mode == "local"


class TestExecutionModeSelection:
    """Test execution mode selection edge cases."""

    def test_local_execution_no_parallelization(self):
        """Test simple local execution without parallelization."""

        @cluster()
        def simple_local_func(x):
            return x + 10

        # Configure for local execution
        configure(cluster_type="local")

        result = simple_local_func(5)
        assert result == 15

    def test_local_execution_fallback_without_parallelization(self):
        """Test local execution fallback when parallelization is disabled."""

        @cluster(parallel=False)
        def local_fallback_func(x, y):
            return x * y + 5

        # Configure for local execution
        configure(cluster_host=None, auto_parallel=False)

        result = local_fallback_func(3, 4)
        assert result == 17  # 3 * 4 + 5 = 17
