import logging
import textwrap

import pytest
from unittest.mock import Mock, patch
from clustrix.executor import ClusterExecutor
from clustrix.config import ClusterConfig
from clustrix.utils import create_job_script, serialize_function


def _double(x):
    """Module-level so it can really be serialized and really be run."""
    return x * 2


def _explode():
    """A real failure with a real traceback."""
    return 1 / 0


#: A kubeconfig that the real kubernetes client parses successfully. It points
#: at a port nothing is listening on, which is enough: these tests exercise
#: client setup, never an API call.
_MINIMAL_KUBECONFIG = textwrap.dedent("""\
    apiVersion: v1
    kind: Config
    clusters:
    - name: clustrix-test
      cluster:
        server: https://127.0.0.1:6443
    contexts:
    - name: clustrix-test
      context:
        cluster: clustrix-test
        user: clustrix-test
    current-context: clustrix-test
    users:
    - name: clustrix-test
      user:
        token: not-a-real-token
    """)


def _point_kubeconfig_at(monkeypatch, path):
    """Aim the real kubernetes client at `path`.

    `KUBECONFIG` alone is not enough: kubernetes.config reads it once, into a
    module constant, when it is first imported. Setting the environment
    variable afterwards leaves whichever value the first import saw, so the
    constant is redirected too. Nothing is faked -- the client still reads a
    real file off disk and either parses it or refuses it.
    """
    monkeypatch.setenv("KUBECONFIG", str(path))
    monkeypatch.setattr(
        "kubernetes.config.kube_config.KUBE_CONFIG_DEFAULT_LOCATION", str(path)
    )


#: (cluster_type, ClusterExecutor submission method, directive unique to it).
SCHEDULER_CASES = [
    ("slurm", "_submit_slurm_job", "#SBATCH --cpus-per-task=4"),
    ("pbs", "_submit_pbs_job", "#PBS -l nodes=1:ppn=4"),
    ("sge", "_submit_sge_job", "#$ -pe smp 4"),
]


class TestClusterExecutor:
    """Test ClusterExecutor class."""

    @pytest.fixture
    def executor(self, mock_config):
        """Create a ClusterExecutor instance with mock config."""
        return ClusterExecutor(mock_config)

    def test_initialization(self, executor, mock_config):
        """Test executor initialization."""
        assert executor.config == mock_config
        assert executor.ssh_client is None
        assert executor.sftp_client is None

    @patch("paramiko.SSHClient")
    def test_connect(self, mock_ssh_class, executor):
        """Test SSH connection establishment."""
        mock_ssh = Mock()
        mock_ssh_class.return_value = mock_ssh
        mock_sftp = Mock()
        mock_ssh.open_sftp.return_value = mock_sftp

        executor.connect()

        mock_ssh.set_missing_host_key_policy.assert_called_once()
        mock_ssh.connect.assert_called_once_with(
            hostname="test.cluster.com",
            port=22,
            username="testuser",
            key_filename="~/.ssh/test_key",
        )
        assert executor.ssh_client == mock_ssh
        assert executor.sftp_client == mock_sftp

    @patch("paramiko.SSHClient")
    def test_connect_with_password(self, mock_ssh_class):
        """Test SSH connection with password."""
        config = ClusterConfig(
            cluster_host="test.cluster.com", username="testuser", password="testpass"
        )
        executor = ClusterExecutor(config)

        mock_ssh = Mock()
        mock_ssh_class.return_value = mock_ssh

        executor.connect()

        mock_ssh.connect.assert_called_once_with(
            hostname="test.cluster.com",
            port=22,
            username="testuser",
            password="testpass",
        )

    def test_disconnect(self, executor):
        """Test SSH disconnection."""
        mock_ssh = Mock()
        mock_sftp = Mock()
        executor.ssh_client = mock_ssh
        executor.sftp_client = mock_sftp

        executor.disconnect()

        mock_sftp.close.assert_called_once()
        mock_ssh.close.assert_called_once()
        assert executor.ssh_client is None
        assert executor.sftp_client is None

    @patch("paramiko.SSHClient")
    def test_execute_command(self, mock_ssh_class, executor):
        """Test command execution."""
        mock_ssh = Mock()
        mock_ssh_class.return_value = mock_ssh
        executor.ssh_client = mock_ssh

        # Setup mock response
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"command output"
        mock_stdout.channel.recv_exit_status.return_value = 0

        mock_stderr = Mock()
        mock_stderr.read.return_value = b""

        mock_ssh.exec_command.return_value = (None, mock_stdout, mock_stderr)

        stdout, stderr = executor._execute_command("echo test")

        assert stdout == "command output"
        assert stderr == ""
        mock_ssh.exec_command.assert_called_once_with("echo test")

    def test_execute_command_not_connected(self, executor):
        """A command with no SSH connection must fail, and say why.

        Nothing is mocked: this is the shipped code raising its real error.

        The expected text is CHANGED from "Not connected". The refactor moved
        this into ConnectionManager, whose message is "SSH client not
        connected. Call setup_ssh_connection() first." -- the old regex never
        matched anything clustrix produces.
        """
        with pytest.raises(RuntimeError, match="SSH client not connected"):
            executor._execute_command("echo test")

    @patch("cloudpickle.dumps")
    def test_prepare_function_data(self, mock_pickle, executor):
        """Test function data preparation."""

        def test_func(x):
            return x * 2

        mock_pickle.return_value = b"pickled_data"

        result = executor._prepare_function_data(test_func, (5,), {}, {"cores": 4})

        assert result == b"pickled_data"
        mock_pickle.assert_called_once()

        # Check the structure of pickled data
        call_args = mock_pickle.call_args[0][0]
        assert call_args["func"].__name__ == "test_func"
        assert call_args["args"] == (5,)
        assert call_args["kwargs"] == {}
        assert call_args["config"] == {"cores": 4}

    # ------------------------------------------------------------------
    # Job submission.
    #
    # The four tests that lived here (slurm/pbs/sge/k8s) were mock theatre.
    # They patched `clustrix.executor.setup_remote_environment` -- a name that
    # module has not exported since the refactor, so the patch was a silent
    # no-op -- and `clustrix.executor.cloudpickle`, likewise absent. They then
    # replaced `executor._execute_remote_command`, a backward-compatibility
    # alias the scheduler path no longer calls, fed it "Submitted batch job
    # 12345", and asserted that "12345" came back. No clustrix code decided
    # anything in any of them.
    #
    # Two things about submission are real and checkable here, with no
    # scheduler and no network: the script each scheduler generates (a pure
    # function), and the fact that a submission with no connection fails
    # loudly rather than inventing a job ID.
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("cluster_type,_method,directive", SCHEDULER_CASES)
    def test_scheduler_script_carries_only_its_own_directives(
        self, cluster_type, _method, directive
    ):
        """Real generator, real output -- create_job_script is pure."""
        config = ClusterConfig(cluster_type=cluster_type, remote_work_dir="/scratch/w")

        script = create_job_script(
            cluster_type=cluster_type,
            job_config={"cores": 4, "memory": "8GB", "time": "01:00:00"},
            remote_job_dir="/scratch/w/job_1",
            config=config,
        )

        assert script.startswith("#!/bin/bash")
        assert directive in script
        # The result the caller collects has to be signed, or it is refused
        # before deserialization.
        assert "result.pkl.hmac" in script
        # A directive meant for another scheduler in this script would be
        # either ignored or fatal, depending on the site.
        for other_type, _m, other_directive in SCHEDULER_CASES:
            if other_type != cluster_type:
                assert other_directive not in script

    @pytest.mark.parametrize("cluster_type,method,_directive", SCHEDULER_CASES)
    def test_scheduler_submission_without_a_connection_records_no_job(
        self, cluster_type, method, _directive
    ):
        """A submission that cannot reach the cluster must not invent a job.

        Real call into the shipped submission path. It gets as far as creating
        the remote job directory and stops there, because there is no SSH
        connection -- which is exactly the observable behaviour worth pinning:
        a phantom entry in active_jobs would be waited on forever.
        """
        config = ClusterConfig(
            cluster_type=cluster_type,
            cluster_host="test.cluster.com",
            username="testuser",
            remote_work_dir="/tmp/test_clustrix",
        )
        executor = ClusterExecutor(config)
        func_data = serialize_function(_double, (21,), {})

        with pytest.raises(RuntimeError, match="SSH client not connected"):
            getattr(executor, method)(func_data, {"cores": 4})

        assert executor.scheduler_manager.active_jobs == {}
        assert executor.active_jobs == {}

    def test_submit_k8s_job_without_a_usable_cluster_records_no_job(
        self, monkeypatch, tmp_path
    ):
        """Same property for Kubernetes, via the real kubernetes client.

        KUBECONFIG points at a file that does not exist, so the real client
        refuses to configure itself. No API call is attempted and no cluster
        is contacted.
        """
        _point_kubeconfig_at(monkeypatch, tmp_path / "no-such-kubeconfig.yaml")
        executor = ClusterExecutor(ClusterConfig(cluster_type="kubernetes"))
        func_data = serialize_function(_double, (21,), {})

        with pytest.raises(Exception) as excinfo:
            executor._submit_k8s_job(func_data, {"cores": 4, "memory": "8Gi"})

        assert "kube-config" in str(excinfo.value)
        assert executor.k8s_manager.active_jobs == {}
        assert executor.active_jobs == {}

    def test_check_slurm_status(self, executor):
        """Test SLURM job status checking."""
        executor.ssh_client = Mock()

        # Mock squeue output
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"RUNNING"
        mock_stdout.channel.recv_exit_status.return_value = 0

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())

        status = executor._check_slurm_status("12345")

        assert status == "running"

        # Verify squeue command
        call_args = executor.ssh_client.exec_command.call_args[0][0]
        assert "squeue" in call_args
        assert "12345" in call_args

    def test_check_pbs_status(self, executor):
        """Test PBS job status checking."""
        executor.ssh_client = Mock()

        # Mock qstat output
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"12345.pbs  user  R  queue"
        mock_stdout.channel.recv_exit_status.return_value = 0

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())

        status = executor._check_pbs_status("12345")

        assert status == "running"

    def test_check_sge_status_running(self, executor):
        """Test SGE job status checking - running state."""
        executor.ssh_client = Mock()

        # Mock qstat -j output for running job
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"job_state                          r"
        mock_stderr = Mock()
        mock_stderr.read.return_value = b""

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        status = executor._check_sge_status("12345")

        assert status == "running"

        # Verify qstat command
        call_args = executor.ssh_client.exec_command.call_args[0][0]
        assert "qstat -j" in call_args
        assert "12345" in call_args

    def test_check_sge_status_queued(self, executor):
        """Test SGE job status checking - queued state."""
        executor.ssh_client = Mock()

        # Mock qstat -j output for queued job
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"job_state                          qw"
        mock_stderr = Mock()
        mock_stderr.read.return_value = b""

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        status = executor._check_sge_status("12345")

        assert status == "queued"

    def test_check_sge_status_failed(self, executor):
        """Test SGE job status checking - error state."""
        executor.ssh_client = Mock()

        # Mock qstat -j output for error job
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"job_state                          Eqw"
        mock_stderr = Mock()
        mock_stderr.read.return_value = b""

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        status = executor._check_sge_status("12345")

        assert status == "failed"

    def test_check_sge_status_completed(self, executor):
        """Test SGE job status checking - completed/not found."""
        executor.ssh_client = Mock()

        # Mock qstat -j output for job not found
        mock_stdout = Mock()
        mock_stdout.read.return_value = b""
        mock_stderr = Mock()
        mock_stderr.read.return_value = b"Following jobs do not exist: 12345"

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        status = executor._check_sge_status("12345")

        assert status == "completed"

    def test_check_sge_status_exit_status(self, executor):
        """Test SGE job status checking - exit status indicates completion."""
        executor.ssh_client = Mock()

        # Mock qstat -j output with exit status
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"exit_status                        0"
        mock_stderr = Mock()
        mock_stderr.read.return_value = b""

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        status = executor._check_sge_status("12345")

        assert status == "completed"

    # ------------------------------------------------------------------
    # Status and results.
    #
    # These three used to hand-build `active_jobs["job_12345"] =
    # {"remote_dir": ...}` and mock an SFTP `stat`. `get_job_status` now
    # dispatches on `active_jobs[job_id]["manager"]` -- there are several job
    # managers (scheduler, kubernetes, local, huggingface) -- so the
    # hand-built entry raised KeyError. The stale part was the TEST: the entry
    # clustrix writes has that key.
    #
    # `cluster_type="local"` is a real backend, so these now submit real work,
    # run it, and read the real bookkeeping back.
    # ------------------------------------------------------------------

    def test_get_job_status_completed(self):
        """A completed job's status, routed by the manager that owns it."""
        executor = ClusterExecutor(ClusterConfig(cluster_type="local"))
        job_id = executor.submit_job(
            serialize_function(_double, (21,), {}), {"cores": 1}
        )

        assert executor.active_jobs[job_id]["manager"] == "local"
        assert executor.get_job_status(job_id) == "completed"

    def test_get_job_status_failed(self):
        """A job that really raised is reported as failed, not as unknown."""
        executor = ClusterExecutor(ClusterConfig(cluster_type="local"))
        job_id = executor.submit_job(serialize_function(_explode, (), {}), {"cores": 1})

        assert executor.active_jobs[job_id]["manager"] == "local"
        assert executor.get_job_status(job_id) == "failed"
        # The backward-compatibility alias must agree with the public method.
        assert executor._check_job_status(job_id) == "failed"

    def test_get_result_success(self):
        """`get_result` returns the real value and stops tracking the job.

        The old version mocked SFTP to write a pickle of its own dict and
        asserted it got that dict back. It could not run today anyway: a
        result is HMAC-verified against the key recorded at submission before
        anything unpickles it, and a hand-written pickle carries no signature.
        """
        executor = ClusterExecutor(ClusterConfig(cluster_type="local"))
        job_id = executor.submit_job(
            serialize_function(_double, (21,), {}), {"cores": 1}
        )

        assert executor.get_result(job_id) == 42
        assert job_id not in executor.active_jobs

    def test_cancel_job_slurm(self, executor):
        """Test canceling SLURM job."""
        executor.ssh_client = Mock()
        executor.config.cluster_type = "slurm"

        mock_stdout = Mock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 0

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())

        executor.cancel_job("12345")

        call_args = executor.ssh_client.exec_command.call_args[0][0]
        assert "scancel 12345" in call_args

    def test_cancel_job_sge(self, executor):
        """A job clustrix failed to cancel must stay tracked.

        Rewritten. The old version mocked `exec_command` and asserted that
        "qdel 12345" reached its own Mock; its `active_jobs` entry also had no
        "manager" key, which is now a KeyError. Here the qdel is really
        attempted, there really is no connection, and the property that
        matters is the consequence: dropping the job from `active_jobs` after
        a failed cancellation would leave it running and invisible.
        """
        executor.config.cluster_type = "sge"
        executor.active_jobs["12345"] = {"manager": "scheduler", "job_id": "12345"}

        with pytest.raises(RuntimeError, match="SSH client not connected"):
            executor.cancel_job("12345")

        assert "12345" in executor.active_jobs

    def test_get_error_log(self):
        """The real traceback of a real failure, and the unknown-job path."""
        executor = ClusterExecutor(ClusterConfig(cluster_type="local"))
        job_id = executor.submit_job(serialize_function(_explode, (), {}), {"cores": 1})

        error_log = executor._get_error_log(job_id)
        assert "Traceback (most recent call last)" in error_log
        assert "_explode" in error_log

        # An ID nobody recorded falls through to the scheduler manager, which
        # says so rather than guessing.
        assert "No job info available" in executor._get_error_log("unknown_job")


class TestClusterExecutorEdgeCases:
    """Test edge cases and error handling in ClusterExecutor."""

    def test_setup_ssh_connection_no_host(self):
        """Test SSH setup fails when no cluster_host is specified."""
        config = ClusterConfig(cluster_host=None)
        executor = ClusterExecutor(config)

        with pytest.raises(ValueError, match="cluster_host must be specified"):
            executor._setup_ssh_connection()

    @patch("os.getenv")
    @patch("paramiko.SSHClient")
    def test_setup_ssh_connection_no_username(self, mock_ssh_class, mock_getenv):
        """Test SSH setup uses environment USER when no username specified."""

        # Return different values for different env vars
        def getenv_side_effect(key, default=None):
            if key == "USER":
                return "envuser"
            return default

        mock_getenv.side_effect = getenv_side_effect
        config = ClusterConfig(cluster_host="test.cluster.com", username=None)
        executor = ClusterExecutor(config)

        mock_ssh = Mock()
        mock_ssh_class.return_value = mock_ssh

        executor._setup_ssh_connection()

        # Should have called getenv for USER
        assert any(call[0][0] == "USER" for call in mock_getenv.call_args_list)
        connect_call = mock_ssh.connect.call_args[1]
        assert connect_call["username"] == "envuser"

    @patch("paramiko.SSHClient")
    def test_setup_ssh_connection_no_auth(self, mock_ssh_class):
        """Test SSH setup with neither key nor password (uses agent/default)."""
        config = ClusterConfig(
            cluster_host="test.cluster.com",
            username="testuser",
            key_file=None,
            password=None,
        )
        executor = ClusterExecutor(config)

        mock_ssh = Mock()
        mock_ssh_class.return_value = mock_ssh

        executor._setup_ssh_connection()

        # Should not include key_filename or password
        connect_call = mock_ssh.connect.call_args[1]
        assert "key_filename" not in connect_call
        assert "password" not in connect_call
        assert connect_call["username"] == "testuser"

    def test_setup_kubernetes_import_error(self):
        """Test Kubernetes setup when kubernetes package not available."""
        config = ClusterConfig(cluster_type="kubernetes")
        executor = ClusterExecutor(config)

        # Mock import error by patching the import at module level
        with patch.dict("sys.modules", {"kubernetes": None}):
            with pytest.raises(ImportError, match="kubernetes package required"):
                executor._setup_kubernetes()

    # ------------------------------------------------------------------
    # Cloud auto-configuration during Kubernetes setup.
    #
    # Three tests here replaced CloudProviderManager with a Mock and asserted
    # against `clustrix.executor.logger`. The refactor moved this code into
    # executor_connections, which logs to its own logger, so the assertions
    # were made against a logger the code never touched -- they could not
    # fail for the right reason and did not fail for the wrong one either.
    #
    # The real CloudProviderManager reports an incomplete provider config
    # without contacting anything, so the skip path is testable for real. The
    # kubeconfig below is a real file the real kubernetes client parses.
    #
    # Deleted rather than repaired: the third test, which asserted that a
    # Mock raising ImportError produced a warning. CloudProviderManager's
    # constructor stores two attributes and cannot raise, and auto_configure
    # catches its own exceptions, so that branch is unreachable without a
    # mock -- the test could only ever have verified the mock.
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "cloud_provider,expected_reason",
        [
            ("aws", "Missing EKS cluster name or region"),
            ("gcp", "Missing GKE cluster name, zone, or project ID"),
        ],
    )
    def test_cloud_auto_configure_skip_reason_is_reported(
        self, cloud_provider, expected_reason, monkeypatch, tmp_path, caplog
    ):
        """Real manager, real logging, no cloud account touched.

        An incomplete provider config is answered from the config itself:
        `_configure_aws` and `_configure_gcp` both return their reason before
        constructing a configurator, so nothing here makes a network call.
        """
        kubeconfig = tmp_path / "kubeconfig.yaml"
        kubeconfig.write_text(_MINIMAL_KUBECONFIG)
        _point_kubeconfig_at(monkeypatch, kubeconfig)

        config = ClusterConfig(
            cluster_type="kubernetes",
            cloud_auto_configure=True,
            cloud_provider=cloud_provider,
        )
        executor = ClusterExecutor(config)

        with caplog.at_level(logging.INFO):
            executor._setup_kubernetes()

        assert f"Cloud auto-configuration skipped: {expected_reason}" in caplog.text
        # Setup still completes: a skipped auto-configuration is not a failure.
        assert executor.k8s_client is not None

    @patch("kubernetes.client")
    @patch("kubernetes.config")
    def test_setup_kubernetes_no_cloud_auto_configure(
        self, mock_k8s_config, mock_k8s_client
    ):
        """Test Kubernetes setup without cloud auto-configuration."""
        config = ClusterConfig(cluster_type="kubernetes", cloud_auto_configure=False)
        executor = ClusterExecutor(config)

        executor._setup_kubernetes()

        # Should load kube config normally
        mock_k8s_config.load_kube_config.assert_called_once()
        mock_k8s_client.ApiClient.assert_called_once()


class TestJobSubmissionEdgeCases:
    """Test job submission edge cases and error handling."""

    @pytest.fixture
    def mock_executor(self):
        """Create a mock executor with necessary setup."""
        config = ClusterConfig(
            cluster_host="test.cluster.com", cluster_type="slurm", username="testuser"
        )
        executor = ClusterExecutor(config)

        # Mock SSH connection
        executor.ssh_client = Mock()
        executor.sftp_client = Mock()

        return executor

    def test_submit_job_unsupported_cluster_type(self, mock_executor):
        """Test job submission with unsupported cluster type."""
        mock_executor.config.cluster_type = "unsupported_type"

        func_data = {"function": b"test", "args": b"test", "kwargs": b"test"}
        job_config = {"cores": 2}

        with pytest.raises(ValueError, match="Unsupported cluster type"):
            mock_executor.submit_job(func_data, job_config)


class TestJobStatusAndResults:
    """Test job status checking and result retrieval."""

    @pytest.fixture
    def mock_executor(self):
        """Create a mock executor."""
        config = ClusterConfig(
            cluster_host="test.cluster.com", cluster_type="slurm", username="testuser"
        )
        executor = ClusterExecutor(config)
        executor.ssh_client = Mock()
        executor.sftp_client = Mock()
        return executor

    def test_get_job_status_unsupported_type(self, mock_executor):
        """Test job status check with unsupported cluster type."""
        mock_executor.config.cluster_type = "unsupported"

        status = mock_executor.get_job_status("job123")
        assert status == "unknown"
