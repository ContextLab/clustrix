"""Tests for enhanced features: dependency handling, uv support, and configuration enhancements."""

import pytest
import shutil
import tempfile
import os

import dill

from clustrix.config import ClusterConfig, get_config, configure
from clustrix.executor_connections import ConnectionManager
from clustrix.utils import (
    get_environment_requirements,
    get_unreproducible_requirements,
    get_environment_info,
    get_package_manager_command,
    setup_remote_environment,
)
from tests.ssh_server import LocalSSHServer


class TestEnhancedDependencyHandling:
    """Test enhanced dependency handling with pip list --format=freeze."""

    def test_get_environment_requirements_covers_the_whole_environment(self):
        """Dependency capture must account for every installed distribution.

        Real environment, real metadata. The previous version of this test fed
        a hand-written `pip list` transcript through a mocked subprocess, so it
        could not see that the command actually run on a machine with uv
        installed produces a different -- and much shorter -- answer.
        """
        from clustrix.utils import _distribution_records

        records = _distribution_records()
        assert records, "no distributions found in this interpreter"

        requirements = get_environment_requirements()
        unreproducible = get_unreproducible_requirements()

        for canonical, record in records.items():
            if canonical == "clustrix":
                continue
            name = record["name"]
            if record["reason"]:
                assert name in unreproducible
                assert name not in requirements
            else:
                assert requirements.get(name) == record["version"]

    def test_get_environment_requirements_conda_packages(self):
        """conda-installed distributions must be captured like any other."""
        requirements = get_environment_requirements()
        conda_installed = [
            name
            for name in ("mkl", "conda", "intel-openmp", "numpy")
            if name in requirements
        ]
        if not conda_installed:
            pytest.skip("no conda-managed packages installed in this environment")
        for name in conda_installed:
            assert requirements[name]

    def test_get_environment_info_is_the_freeze_listing_of_this_interpreter(self):
        """One ``name==version`` line per package, read from the real pip.

        The version this replaces handed ``subprocess.run`` a ``Mock`` whose
        stdout was two invented lines and asserted the same two lines came
        back. Here pip really runs, and the listing has to agree with what
        this interpreter can import.
        """
        listing = get_environment_info().splitlines()

        assert f"dill=={dill.__version__}" in listing
        assert f"pytest=={pytest.__version__}" in listing


class TestUvPackageManagerSupport:
    """Test uv package manager support and integration.

    Detection of uv and conda, and what ``package_manager="auto"`` makes of
    it, is in ``tests/test_utils.py::TestPackageManager``, against real
    programs on a real ``PATH``.
    """

    def test_get_package_manager_command_unknown_fallback(self):
        """Test fallback to pip for unknown package manager."""
        config = ClusterConfig(package_manager="unknown")

        result = get_package_manager_command(config)

        assert result == "pip"


PASSWORD = "wrong_password"

PINNED = {"dill": "0.3.8", "cloudpickle": "3.0.0"}


def _program(directory, name, body):
    """A real program called ``name`` in ``directory``."""
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


class RemoteHost:
    """A host reached over real SSH whose ``PATH`` holds only what is put there.

    ``setup_remote_environment`` builds one shell command and runs it on the
    far side. These tests let it: the in-process SSH server carries the
    command, a real ``sh`` runs it, and the exit status and stderr that come
    back are the shell's own.

    What is *not* real is the software on the host. ``python3`` here creates
    an empty ``venv/bin/activate`` instead of a virtualenv, and ``pip`` and
    ``uv`` append their arguments to a log instead of contacting a package
    index, because a unit test may not need the network. That makes these
    stand-ins, kept honest by ``tests/real_world/
    test_remote_environment_setup.py``, which runs the same calls against a
    real host where a real pip and a real uv install into a real virtualenv.
    """

    def __init__(self, tmp_path):
        self.root = tmp_path / "remote"
        self.bin = self.root / "bin"
        self.work = self.root / "job"
        self.log = self.root / "installs.log"
        self.bin.mkdir(parents=True)
        self.work.mkdir()
        self.client = None
        self.config = None

    def has_python(self):
        _program(
            self.bin,
            "python3",
            # ``python3 -m venv venv``, as far as the rest of the command can
            # tell. mkdir by absolute path: this directory is all of PATH.
            f"{shutil.which('mkdir')} -p \"$3/bin\" && : > \"$3/bin/activate\"",
        )

    def has_installer(self, name, behaviour="exit 0"):
        _program(self.bin, name, f'echo "{name} $*" >> "{self.log}"\n{behaviour}')

    def installs(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def setup(self, requirements, **settings):
        for name, value in settings.items():
            setattr(self.config, name, value)
        return setup_remote_environment(
            self.client, str(self.work), requirements, self.config
        )


@pytest.fixture
def remote_host(tmp_path):
    host = RemoteHost(tmp_path)
    with LocalSSHServer(
        root=str(host.root), password=PASSWORD, env={"PATH": str(host.bin)}
    ) as server:
        host.config = ClusterConfig(
            cluster_type="ssh",
            cluster_host=server.host,
            cluster_port=server.port,
            username="tester",
            password=PASSWORD,
            # The server's key is made per run, so it can never be known.
            ssh_host_key_policy="auto_add",
            remote_work_dir=str(host.root),
            python_executable="python3",
        )
        connection = ConnectionManager(host.config)
        connection.setup_ssh_connection()
        host.client = connection.ssh_client
        try:
            yield host
        finally:
            connection.disconnect()


class TestRemoteEnvironmentSetup:
    """``setup_remote_environment``, run over SSH rather than described to a Mock."""

    @pytest.mark.parametrize(
        "on_the_host, package_manager, ran",
        [
            (("pip", "uv"), "uv", "uv pip install"),
            (("pip", "uv"), "pip", "pip install --timeout=120"),
            (("pip", "uv"), "auto", "uv pip install"),
            (("pip",), "auto", "pip install --timeout=120"),
        ],
        ids=["uv-named", "pip-named", "auto-host-has-uv", "auto-host-has-no-uv"],
    )
    def test_the_installer_that_runs_is_the_one_configured(
        self, remote_host, on_the_host, package_manager, ran
    ):
        """One install, by the right tool, of the serializers at the caller's pins.

        ``numpy`` is in the requirements and not in the command: this step
        installs only what the job script needs in order to load the
        function, and the old tests never looked.
        """
        remote_host.has_python()
        for installer in on_the_host:
            remote_host.has_installer(installer)

        outcome = remote_host.setup(
            {**PINNED, "numpy": "1.26.4"}, package_manager=package_manager
        )

        assert outcome == "Environment setup completed successfully"
        assert remote_host.installs() == [f"{ran} dill==0.3.8 cloudpickle==3.0.0"]
        assert (remote_host.work / "venv" / "bin" / "activate").exists()

    def test_auto_is_decided_by_the_remote_host_not_by_this_machine(
        self, remote_host, tmp_path, monkeypatch
    ):
        """uv installed here says nothing about the host the job runs on."""
        remote_host.has_python()
        remote_host.has_installer("pip")
        here = tmp_path / "local-bin"
        here.mkdir()
        _program(here, "uv", "exit 0")
        monkeypatch.setenv("PATH", str(here))

        remote_host.setup(dict(PINNED), package_manager="auto")

        assert remote_host.installs() == [
            "pip install --timeout=120 dill==0.3.8 cloudpickle==3.0.0"
        ]

    def test_without_pins_the_serializers_are_installed_unpinned(self, remote_host):
        remote_host.has_python()
        remote_host.has_installer("pip")

        remote_host.setup({"numpy": "1.26.4"}, package_manager="pip")

        assert remote_host.installs() == ["pip install --timeout=120 dill cloudpickle"]

    def test_a_pin_that_cannot_be_installed_is_retried_unpinned(self, remote_host):
        """Some dill beats no dill: without it the job cannot load anything."""
        remote_host.has_python()
        remote_host.has_installer(
            "pip", 'case "$*" in *==*) echo "no such version" >&2; exit 1;; esac'
        )

        outcome = remote_host.setup(dict(PINNED), package_manager="pip")

        assert outcome == "Environment setup completed successfully"
        assert remote_host.installs() == [
            "pip install --timeout=120 dill==0.3.8 cloudpickle==3.0.0",
            "pip install --timeout=120 dill cloudpickle",
        ]

    def test_an_install_that_fails_twice_raises_with_what_the_installer_said(
        self, remote_host
    ):
        remote_host.has_python()
        remote_host.has_installer(
            "pip", 'echo "ERROR: could not reach the package index" >&2; exit 1'
        )

        with pytest.raises(RuntimeError) as raised:
            remote_host.setup(dict(PINNED), package_manager="pip")

        message = str(raised.value)
        assert "Environment setup failed" in message
        assert "could not reach the package index" in message
        assert len(remote_host.installs()) == 2

    def test_a_host_with_no_python_and_no_pip_is_a_failed_setup(self, remote_host):
        """Nothing stands in for anything here: the host's ``PATH`` is empty.

        The shell's own "not found" is the reason, and it has to reach the
        caller -- the mocked version of this test supplied both the exit
        status and the message itself.
        """
        with pytest.raises(RuntimeError) as raised:
            remote_host.setup(dict(PINNED), package_manager="pip")

        message = str(raised.value)
        assert "Environment setup failed" in message
        assert "not found" in message
        assert not (remote_host.work / "venv").exists()


class TestConfigurationEnhancements:
    """Test enhanced configuration features."""

    def test_package_manager_configuration(self):
        """Test package manager configuration."""
        config = ClusterConfig(package_manager="uv")
        assert config.package_manager == "uv"

        config = ClusterConfig(package_manager="auto")
        assert config.package_manager == "auto"

        # Test default
        config = ClusterConfig()
        assert config.package_manager == "pip"

    def test_configuration_persistence_with_new_fields(self):
        """Test saving and loading configuration with new fields.

        Rewritten: this used to round-trip k8s_/cloud_/eks_ fields, which no
        longer exist on ClusterConfig. The property under test -- that a saved
        config reloads field for field -- is unchanged; only the fields it is
        stated over had to move to backends clustrix still has.
        """
        original_config = ClusterConfig(
            cluster_type="huggingface",
            hf_namespace="contextlab",
            hf_flavor="cpu-basic",
            package_manager="uv",
            venv_setup_timeout=600,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_file = f.name

        try:
            # Save configuration
            original_config.save_to_file(config_file)

            # Load configuration
            loaded_config = ClusterConfig.load_from_file(config_file)

            # Verify all fields are preserved
            assert loaded_config.cluster_type == "huggingface"
            assert loaded_config.hf_namespace == "contextlab"
            assert loaded_config.hf_flavor == "cpu-basic"
            assert loaded_config.package_manager == "uv"
            assert loaded_config.venv_setup_timeout == 600

        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)

    def test_configure_function_with_new_parameters(self):
        """Test configure function with new parameters.

        Rewritten: the parameters it named (k8s_namespace, k8s_image,
        cloud_provider) belonged to removed backends. Restated over the
        HuggingFace Jobs settings, which are the ones a user configures today.
        """
        configure(
            hf_namespace="contextlab",
            hf_flavor="cpu-basic",
            package_manager="uv",
        )

        config = get_config()
        assert config.hf_namespace == "contextlab"
        assert config.hf_flavor == "cpu-basic"
        assert config.package_manager == "uv"

    def test_configure_rejects_a_setting_from_a_removed_backend(self):
        """configure() must not silently accept a field that no longer exists.

        The message has to name the backend and its tracking issue. It used
        to come back through difflib as "did you mean ...?" pointed at an
        unrelated field, which sent the reader after the wrong thing.
        """
        with pytest.raises(ValueError) as excinfo:
            configure(k8s_namespace="production")

        message = str(excinfo.value)
        assert "k8s_namespace" in message
        assert "Kubernetes" in message
        assert "removed" in message
        assert "#142" in message
        assert "did you mean" not in message


class TestBackwardCompatibility:
    """Test backward compatibility of enhanced features."""

    def test_existing_configuration_still_works(self):
        """Test that existing configurations continue to work."""
        # Old-style configuration should still work
        config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="login.cluster.edu",
            username="user",
            remote_work_dir="/scratch/user",
        )

        assert config.cluster_type == "slurm"
        assert config.cluster_host == "login.cluster.edu"
        assert config.username == "user"
        assert config.remote_work_dir == "/scratch/user"

        # New fields should have defaults. The two cloud_* assertions that
        # were here are gone with the fields themselves.
        assert config.package_manager == "pip"
        assert config.replicate_local_environment is True

    def test_package_manager_command_unknown_input(self):
        """Test graceful handling of unknown package manager configuration."""
        config = ClusterConfig(package_manager="nonexistent")

        # Should fallback to pip
        result = get_package_manager_command(config)
        assert result == "pip"
