"""``@cluster`` from the decorator to the answer, with nothing stubbed.

The remote half of this module used to be a ``Mock`` SSH client that
answered ``b"Success"`` to every command, a hand-signed ``result.pkl`` fed
back through a ``MagicMock`` SFTP client, and ``secrets.token_hex`` pinned so
the forged signature would verify. It asserted ``add_numbers(10, 32) == 42``
and the only arithmetic involved was the test pickling 42.

The same call now crosses a real SSH connection to ``tests/ssh_server.py``:
the generated job script is really written, really run by a shell, the
result is really signed by the worker and really verified here. A real
scheduler is exercised by ``tests/real_world/test_local_sshd_slurm.py`` and
``tests/real_world/test_slurm_resources_real.py``.
"""

import importlib.metadata
import os
import sys
from pathlib import Path

import pytest

from clustrix import cluster, configure, get_config
from tests.ssh_server import LocalSSHServer

SSH_PASSWORD = "clustrix-test-password"


@pytest.fixture
def ssh_cluster(tmp_path):
    """clustrix configured for a real SSH server that lives for one test."""
    remote = tmp_path / "remote"
    remote.mkdir()
    with LocalSSHServer(root=str(remote), password=SSH_PASSWORD) as server:
        server.root_path = remote
        configure(
            cluster_type="ssh",
            cluster_host=server.host,
            cluster_port=server.port,
            username="tester",
            password=SSH_PASSWORD,
            ssh_host_key_policy="auto_add",
            remote_work_dir=str(remote / "jobs"),
            replicate_local_environment=False,
            python_executable=sys.executable,
        )
        yield server


class TestIntegration:
    """Integration tests for end-to-end functionality."""

    def test_end_to_end_simple_function(self, ssh_cluster):
        """Arguments go out, the function runs over there, the answer returns."""

        @cluster(cores=1, memory="1GB")
        def add_numbers(x, y, scale=1):
            import os

            return (x + y) * scale, os.getpid(), os.getcwd()

        total, remote_pid, remote_cwd = add_numbers(10, 32, scale=2)

        assert total == 84
        assert remote_pid != os.getpid(), "the function ran in this process"
        jobs_dir = ssh_cluster.root_path / "jobs"
        assert jobs_dir in Path(remote_cwd).parents
        assert ("tester", "password") in ssh_cluster.authentications
        # cleanup_on_success is the default: the job's directory is gone.
        assert not Path(remote_cwd).exists()

    def test_a_job_directory_is_kept_when_cleanup_is_turned_off(self, ssh_cluster):
        configure(cleanup_on_success=False)

        @cluster(cores=1)
        def where():
            import os

            return os.getcwd()

        job_dir = Path(where())

        assert job_dir.is_dir()
        assert (job_dir / "result.pkl").exists()
        assert (job_dir / "result.pkl.hmac").exists()

    def test_a_remote_exception_arrives_as_its_own_type(self, ssh_cluster):
        @cluster(cores=1)
        def lookup():
            return {}["missing-key"]

        with pytest.raises(KeyError, match="missing-key"):
            lookup()

    def test_error_handling_integration(self):
        """An exception raised inside a @cluster function must reach the caller.

        This used to drive a mocked SSH client whose exec_command answered
        b"Success" to every command -- including `echo $HOME`, so the remote
        home directory resolved to the string "Success". It then hand-built a
        signed error.pkl to feed back through the mock. It exercised the
        mocking, not the product: clustrix now correctly refuses a nonsense
        home directory, and the test failed on that rather than on anything to
        do with error handling.

        cluster_type="local" is a real backend, so the same question -- does the
        original exception, with its type and message, reach the caller? -- can
        be asked without inventing a cluster.
        """
        configure(cluster_type="local")

        @cluster(cores=2)
        def failing_function():
            raise ValueError("This function always fails")

        with pytest.raises(ValueError, match="This function always fails"):
            failing_function()

    def test_error_handling_preserves_exception_type(self):
        """Not just the message: the caller must get the real exception class.

        A previous defect returned every remote failure as a generic error,
        which meant `except KeyError:` around a @cluster call did not work.
        """
        configure(cluster_type="local")

        @cluster(cores=1)
        def raises_keyerror():
            raise KeyError("missing-key")

        with pytest.raises(KeyError):
            raises_keyerror()

    def test_configuration_persistence(self, tmp_path):
        """Test configuration loading and persistence."""
        config_file = tmp_path / "clustrix.yml"

        # Create configuration
        configure(
            cluster_type="ssh",
            cluster_host="gpu.cluster.com",
            username="sshuser",
            default_cores=16,
            default_memory="32GB",
            module_loads=["python/3.9", "gcc/11.2"],
            environment_variables={"OMP_NUM_THREADS": "16"},
        )

        # Save configuration
        from clustrix.config import save_config

        save_config(str(config_file))

        # Change every saved value first. This step used to be
        # ``configure(cluster_type="ssh")`` -- the value already in force --
        # so the assertions below held even if ``load_config`` did nothing.
        configure(
            cluster_type="local",
            cluster_host="elsewhere.example.edu",
            username="someone-else",
            default_cores=1,
            default_memory="1GB",
            module_loads=[],
        )
        assert get_config().cluster_host == "elsewhere.example.edu"

        # Load configuration
        from clustrix.config import load_config

        load_config(str(config_file))

        # Verify loaded configuration
        config = get_config()
        assert config.cluster_type == "ssh"
        assert config.cluster_host == "gpu.cluster.com"
        assert config.username == "sshuser"
        assert config.default_cores == 16
        assert config.default_memory == "32GB"
        assert config.module_loads == ["python/3.9", "gcc/11.2"]
        # Rewritten, not relaxed. This asserted that OMP_NUM_THREADS came
        # back, which encoded the rule that each environment_variables
        # entry is judged by its key name -- the rule that let
        # SSH_PASSPHRASE, GITHUB_PAT and a DATABASE_URL carrying a password
        # through. Those names and values are the user's, so clustrix
        # cannot classify them and no longer tries: the mapping is withheld
        # from a default save and returns empty on load. Use
        # save_to_file(include_secrets=True) to persist it deliberately.
        assert config.environment_variables == {}

    def test_local_execution_fallback(self):
        """Test that functions execute locally when no cluster is configured."""
        # Ensure no cluster is configured
        configure(cluster_host=None)

        @cluster(cores=8)
        def compute_locally(x, y):
            return x**y

        result = compute_locally(2, 10)
        assert result == 1024

    def test_environment_replication(self):
        """Test environment replication against the REAL local environment.

        This used to mock ``clustrix.utils.get_environment_info`` and only
        assert that a submitted job's freeze output got read. That patch
        target no longer exists: ``serialize_function`` used to call
        ``get_environment_info()`` purely so this mock would be exercised,
        discarding the result and paying for a ``pip list`` subprocess on
        every job submission for nothing. It has been deleted.

        Mocking the freeze step is also exactly how the real environment-
        replication bug went undetected for so long:
        ``get_environment_requirements()`` was silently dropping 187 of 563
        installed packages (anything with an ``@`` in its freeze line), and
        every test that faked the freeze output reported success regardless.
        So this exercises the real function against this interpreter's own
        installed packages instead of mocking anything.
        """
        from clustrix.utils import (
            get_environment_requirements,
            get_unreproducible_requirements,
            serialize_function,
        )

        requirements = get_environment_requirements()

        # A real development environment has far more than a couple of
        # packages -- this is what the "drop anything with an @ in it" bug
        # would have reduced it to.
        assert len(requirements) > 50

        # Pick a package we can independently confirm is installed via
        # importlib.metadata itself (not hardcoded, so this holds on any
        # machine with a normal clustrix dev install) and check the reported
        # version agrees with metadata's own answer.
        installed = {
            dist.metadata["Name"]: dist.version
            for dist in importlib.metadata.distributions()
            if dist.metadata["Name"]
        }
        assert "numpy" in installed, "test environment is expected to have numpy"
        assert requirements["numpy"] == installed["numpy"]

        # clustrix itself must never be pinned: the generated worker never
        # imports clustrix, so requiring it would be both unnecessary and
        # (for an editable checkout, as in this repo) unreproducible.
        assert "clustrix" not in requirements

        unreproducible = get_unreproducible_requirements()
        assert "clustrix" not in unreproducible

        # If this environment happens to have an editable or VCS install
        # (common on a development machine -- e.g. `pip install -e .` of some
        # other project), it must be classified as unreproducible with a
        # reason, and must NOT be pinned as a plain requirement.
        for dist in importlib.metadata.distributions():
            name = dist.metadata["Name"]
            if not name or name == "clustrix":
                continue
            try:
                raw = dist.read_text("direct_url.json")
            except Exception:
                raw = None
            if not raw:
                continue
            is_editable = '"editable": true' in raw
            is_vcs = '"vcs_info"' in raw
            if is_editable or is_vcs:
                assert name in unreproducible, (
                    f"{name} is an editable/VCS install and should be "
                    "reported as unreproducible"
                )
                assert name not in requirements, (
                    f"{name} is an editable/VCS install and must not be "
                    "pinned as a plain requirement"
                )
                break

        # And confirm the requirement set is actually what gets attached to a
        # submitted job, rather than testing get_environment_requirements()
        # in isolation from the thing that calls it.
        func_data = serialize_function(lambda: None, (), {})
        assert func_data["requirements"] == get_environment_requirements()
