import os

import cloudpickle
import paramiko
import pytest

from clustrix.executor import ClusterExecutor
from clustrix.config import ClusterConfig
from clustrix.utils import create_job_script, serialize_function
from tests.ssh_server import LocalSSHServer, generate_keypair

#: Password the in-process SSH server accepts. It is a real credential for a
#: real server that exists only for the duration of one test.
SSH_PASSWORD = "clustrix-test-password"


@pytest.fixture
def ssh_server(tmp_path):
    """A real SSH server on a loopback port.

    Not a mock, and not a stand-in for one: paramiko's server side, a real
    socket, a real handshake, real key and password authentication, and
    commands run by a real shell against real files. `clustrix` runs against
    it completely unmodified.
    """
    root = tmp_path / "remote"
    root.mkdir()
    private_key = generate_keypair(tmp_path, "id_ed25519")
    with LocalSSHServer(
        root=root, password=SSH_PASSWORD, authorized_keys=[f"{private_key}.pub"]
    ) as server:
        server.root_path = root
        server.private_key_path = private_key
        yield server


def _real_config(server, **overrides):
    """A ClusterConfig pointed at the real test server."""
    kwargs = dict(
        cluster_type="slurm",
        cluster_host=server.host,
        cluster_port=server.port,
        username="testuser",
        password=SSH_PASSWORD,
        remote_work_dir=str(server.root_path),
        # The server generates its host key per run, so it can never appear in
        # a known_hosts file. Verification of unknown host keys is a separate
        # subject with its own tests (tests/unit/test_host_key_policy.py).
        ssh_host_key_policy="auto_add",
    )
    kwargs.update(overrides)
    return ClusterConfig(**kwargs)


def _double(x):
    """Module-level so it can really be serialized and really be run."""
    return x * 2


def _explode():
    """A real failure with a real traceback."""
    return 1 / 0


#: (cluster_type, ClusterExecutor submission method, directive unique to it).
#: PBS and SGE used to be listed here too. Both backends were removed --
#: neither had ever been run against a real scheduler (issues #140, #141) --
#: so SLURM is the only scheduler clustrix still submits to.
SCHEDULER_CASES = [
    ("slurm", "_submit_slurm_job", "#SBATCH --cpus-per-task=4"),
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

    def test_connect(self, ssh_server):
        """A real SSH connection, authenticated with a real key.

        The old version patched ``paramiko.SSHClient``, then asserted that
        ``executor.ssh_client`` was the Mock it had just installed and that
        ``connect`` had been called with the arguments the config held. No
        socket was opened and no key was used. Here the key really is on
        disk, the server really verifies possession of it, and the SFTP
        channel really lists a file that really exists.
        """
        executor = ClusterExecutor(
            _real_config(ssh_server, key_file=str(ssh_server.private_key_path))
        )

        executor.connect()
        try:
            transport = executor.ssh_client.get_transport()
            assert transport is not None and transport.is_active()
            # The far end saw the username, and saw it prove the key.
            assert ("testuser", "publickey") in ssh_server.authentications

            (ssh_server.root_path / "marker.txt").write_text("hello")
            assert "marker.txt" in executor.sftp_client.listdir(".")
        finally:
            executor.disconnect()

    def test_connect_with_password(self, ssh_server):
        """Password authentication, really performed by a real server."""
        executor = ClusterExecutor(_real_config(ssh_server, key_file=None))

        executor.connect()
        try:
            assert ("testuser", "password") in ssh_server.authentications
            stdout, _ = executor._execute_command("echo connected")
            assert stdout.strip() == "connected"
        finally:
            executor.disconnect()

    def test_connect_with_the_wrong_password_fails(self, ssh_server):
        """Authentication has to be capable of failing.

        A mocked ``SSHClient`` accepts every credential, so the mocked tests
        above it could never have caught an executor that authenticated
        against nothing.
        """
        executor = ClusterExecutor(_real_config(ssh_server, password="wrong"))

        with pytest.raises(paramiko.AuthenticationException):
            executor.connect()

    def test_disconnect(self, ssh_server):
        """Disconnect really closes a really open connection."""
        executor = ClusterExecutor(_real_config(ssh_server, key_file=None))
        executor.connect()
        transport = executor.ssh_client.get_transport()
        assert transport.is_active()

        executor.disconnect()

        assert executor.ssh_client is None
        assert executor.sftp_client is None
        assert not transport.is_active()

    def test_execute_command(self, ssh_server):
        """Named in issue #117: the old test asserted Python assignment works.

        It set ``mock_stdout.read.return_value = b"command output"`` and then
        asserted ``stdout == "command output"``. Every byte in that assertion
        was supplied by the test itself.

        This runs a real command in a real shell at the far end of a real SSH
        connection, and asserts on output clustrix has no other way of
        knowing: the contents of a file on the server's disk, the server's
        real stderr, and a real non-zero exit status.
        """
        (ssh_server.root_path / "greeting.txt").write_text("hello from a real shell\n")
        executor = ClusterExecutor(_real_config(ssh_server, key_file=None))
        executor.connect()
        try:
            stdout, stderr = executor._execute_command("cat greeting.txt")
            assert stdout == "hello from a real shell\n"
            assert stderr == ""

            # Real stderr, kept separate from stdout.
            out, err = executor.connection_manager.execute_remote_command(
                "echo oops >&2; exit 3"
            )
            assert out == ""
            assert err.strip() == "oops"

            # And `check=True` really reads the real exit status.
            with pytest.raises(RuntimeError, match=r"exit 3"):
                executor.connection_manager.execute_remote_command(
                    "echo oops >&2; exit 3", check=True
                )

            assert ssh_server.commands.count("cat greeting.txt") == 1
        finally:
            executor.disconnect()

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

    def test_prepare_function_data(self, executor):
        """Real serialization, really round-tripped.

        The old version patched ``cloudpickle.dumps`` to return
        ``b"pickled_data"`` and asserted it got ``b"pickled_data"`` back, so it
        would have passed against a serializer that could not serialize
        anything. This asserts the bytes deserialize into a working function.
        """

        def test_func(x):
            return x * 2

        result = executor._prepare_function_data(test_func, (5,), {}, {"cores": 4})

        assert isinstance(result, bytes)
        restored = cloudpickle.loads(result)
        assert restored["args"] == (5,)
        assert restored["kwargs"] == {}
        assert restored["config"] == {"cores": 4}
        # The point of serializing it at all: it still runs on the far side.
        assert restored["func"](5) == 10

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
        # This loop used to compare SLURM's script against PBS's and SGE's.
        # With one scheduler left it would assert nothing, so the property is
        # stated against the backend that is not a scheduler instead: an SSH
        # script must carry no scheduler directives at all, since there is
        # nothing on the far end to read them.
        ssh_script = create_job_script(
            cluster_type="ssh",
            job_config={"cores": 4, "memory": "8GB", "time": "01:00:00"},
            remote_job_dir="/scratch/w/job_1",
            config=ClusterConfig(cluster_type="ssh", remote_work_dir="/scratch/w"),
        )
        assert directive not in ssh_script
        assert "#SBATCH" not in ssh_script

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

    def test_check_slurm_status(self, ssh_server):
        """Status detection over a real connection, from real files.

        The old version fed a Mock ``b"RUNNING"`` and asserted "running" --
        it tested a lookup table against a string it had supplied.

        There is no SLURM here, but that is the case this code was written
        for: `squeue` stops listing a job the moment it finishes, so the
        answer has to come from what the job left on disk. Those files are
        real and they are read over the real SSH connection.
        """
        job_dir = ssh_server.root_path / "job_12345"
        job_dir.mkdir()
        executor = ClusterExecutor(_real_config(ssh_server, key_file=None))
        executor.connect()
        try:
            executor.scheduler_manager.active_jobs["12345"] = {
                "remote_dir": str(job_dir)
            }

            (job_dir / "result.pkl").write_bytes(b"a real result file")
            assert executor._check_slurm_status("12345") == "completed"

            (job_dir / "result.pkl").unlink()
            (job_dir / "error.pkl").write_bytes(b"a real error file")
            assert executor._check_slurm_status("12345") == "failed"

            # `squeue` really was asked first, and really crossed the wire.
            assert any(c.startswith("squeue -j 12345") for c in ssh_server.commands)
        finally:
            executor.disconnect()

    # ------------------------------------------------------------------
    # Status and results.
    #
    # These three used to hand-build `active_jobs["job_12345"] =
    # {"remote_dir": ...}` and mock an SFTP `stat`. `get_job_status` now
    # dispatches on `active_jobs[job_id]["manager"]` -- there are several job
    # managers (scheduler, local, huggingface) -- so the
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

    def test_cancel_job_slurm(self, ssh_server):
        """`scancel` really crosses the wire.

        Was: a Mock recorded the string clustrix handed it, and the test read
        it back off the Mock. Now the command is observed at the far end of a
        real socket, by a real SSH server that really received it.
        """
        executor = ClusterExecutor(_real_config(ssh_server, key_file=None))
        executor.connect()
        try:
            executor.cancel_job("12345")
            assert "scancel 12345" in ssh_server.commands
        finally:
            executor.disconnect()

    def test_cancel_job_that_cannot_be_reached_stays_tracked(self, executor):
        """A job clustrix failed to cancel must stay tracked.

        Rewritten twice. The original mocked `exec_command` and asserted that
        "qdel 12345" reached its own Mock; its `active_jobs` entry also had no
        "manager" key, which is now a KeyError. It then ran against SGE, a
        backend that has since been removed, so it is run against SLURM here.
        The cancellation is really attempted, there really is no connection,
        and the property that matters is the consequence: dropping the job
        from `active_jobs` after a failed cancellation would leave it running
        and invisible.
        """
        executor.config.cluster_type = "slurm"
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

    def test_setup_ssh_connection_no_username(self, ssh_server):
        """With no username configured, the OS user is what reaches the server.

        The old version patched ``os.getenv`` and inspected a Mock's kwargs.
        The environment variable set here is a real environment variable, and
        the username asserted on is the one the server really authenticated.
        """
        previous = os.environ.get("USER")
        os.environ["USER"] = "envuser"
        try:
            executor = ClusterExecutor(
                _real_config(ssh_server, username=None, key_file=None)
            )
            executor._setup_ssh_connection()
            try:
                assert ("envuser", "password") in ssh_server.authentications
            finally:
                executor.disconnect()
        finally:
            if previous is None:
                del os.environ["USER"]
            else:
                os.environ["USER"] = previous

    def test_setup_ssh_connection_no_auth(self, ssh_server):
        """With neither key nor password configured, clustrix does not get in.

        The old version asserted that ``key_filename`` and ``password`` were
        absent from a Mock's call kwargs -- true regardless of whether the
        connection would have succeeded. The server here authorizes exactly
        one key and one password; offering neither must be refused, and this
        is the honest thing to assert without a host that trusts an agent.
        """
        config = _real_config(
            ssh_server, key_file=None, password=None, username="testuser"
        )
        executor = ClusterExecutor(config)

        with pytest.raises(paramiko.SSHException):
            executor._setup_ssh_connection()

        assert ("testuser", "password") not in ssh_server.authentications
        assert ("testuser", "publickey") not in ssh_server.authentications


class TestJobSubmissionEdgeCases:
    """Test job submission edge cases and error handling."""

    @pytest.fixture
    def executor(self):
        """A real executor. It has no connection, and does not need one.

        The fixture used to install ``Mock()`` SSH and SFTP clients. The test
        below rejects its cluster type before any connection is consulted, so
        the mocks were pure decoration -- and they hid the fact that the
        rejection happens that early.
        """
        config = ClusterConfig(
            cluster_host="test.cluster.com", cluster_type="slurm", username="testuser"
        )
        return ClusterExecutor(config)

    def test_submit_job_unsupported_cluster_type(self, executor):
        """Test job submission with unsupported cluster type."""
        executor.config.cluster_type = "unsupported_type"

        func_data = {"function": b"test", "args": b"test", "kwargs": b"test"}
        job_config = {"cores": 2}

        with pytest.raises(ValueError, match="is not a supported cluster type"):
            executor.submit_job(func_data, job_config)


class TestJobStatusAndResults:
    """Test job status checking and result retrieval."""

    @pytest.fixture
    def executor(self):
        """A real executor with no connection; none is needed below."""
        config = ClusterConfig(
            cluster_host="test.cluster.com", cluster_type="slurm", username="testuser"
        )
        return ClusterExecutor(config)

    def test_get_job_status_unsupported_type(self, executor):
        """An unrecognised cluster type yields "unknown", not a crash."""
        executor.config.cluster_type = "unsupported"

        status = executor.get_job_status("job123")
        assert status == "unknown"
