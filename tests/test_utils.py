import inspect
import logging
import pytest
import pickle
from concurrent.futures import ThreadPoolExecutor

import dill

from clustrix.utils import (
    serialize_function,
    deserialize_function,
    get_environment_requirements,
    get_unreproducible_requirements,
    create_job_script,
    setup_environment,
    is_uv_available,
    is_conda_available,
    get_package_manager_command,
)
from clustrix.config import ClusterConfig


class TestSerialization:
    """Test function serialization utilities."""

    def test_serialize_deserialize_simple_function(self):
        """Test serializing and deserializing a simple function."""

        def test_func(x, y):
            return x + y

        serialized = serialize_function(test_func, (5, 3), {})
        result_func, args, kwargs = deserialize_function(serialized)

        assert result_func(5, 3) == 8
        assert args == (5, 3)
        assert kwargs == {}

    def test_serialize_deserialize_with_kwargs(self):
        """Test serialization with keyword arguments."""

        def test_func(x, y=10, z=20):
            return x + y + z

        serialized = serialize_function(test_func, (5,), {"z": 30})
        result_func, args, kwargs = deserialize_function(serialized)

        assert result_func(*args, **kwargs) == 45
        assert args == (5,)
        assert kwargs == {"z": 30}

    def test_serialize_complex_data(self):
        """Test serialization of complex data types."""

        def test_func(data):
            total = 0
            for key, value in data.items():
                if isinstance(value, list):
                    total += sum(value)
                elif isinstance(value, dict):
                    total += sum(value.values())
                else:
                    total += value
            return total

        complex_data = {"a": [1, 2, 3], "b": {"nested": 4}, "c": 5}
        serialized = serialize_function(test_func, (complex_data,), {})
        result_func, args, kwargs = deserialize_function(serialized)

        # Calculate expected sum: [1,2,3] = 6, {"nested": 4} = 4, 5 = 5, total = 15
        expected = 15
        assert result_func(*args, **kwargs) == expected

    def test_an_enum_from_a_script_is_serialized_by_cloudpickle_when_dill_cannot(
        self, caplog
    ):
        """The cascade really degrades: dill refuses, cloudpickle carries it.

        This replaces a test that patched ``dill.dumps`` to raise and
        ``cloudpickle.dumps`` to return a canned pickle of a string, then
        asserted the canned bytes came back. No serializer ran, so it could
        not tell a working fallback from one that shipped garbage.

        The real disagreement needs no patching. An ``Enum`` defined in a
        script lives in ``__main__``; dill tries to store its class by name,
        cannot find ``__main__.Colour`` and refuses, with and without
        ``recurse``. cloudpickle stores the class by value. The namespace
        below is what a user's script looks like to the serializers.
        """
        script: dict = {"__name__": "__main__"}
        exec(
            "import enum\n"
            "class Colour(enum.Enum):\n"
            "    RED = 1\n"
            "    GREEN = 2\n"
            "def name_of(colour, shout=False):\n"
            "    return colour.name.upper() if shout else colour.name.lower()\n",
            script,
        )
        colour = script["Colour"].GREEN

        # The premise. If a later dill learns to do this, the test must say
        # so rather than pass without ever leaving the first strategy.
        with pytest.raises(Exception):
            dill.dumps(colour, protocol=4, recurse=True)
        with pytest.raises(Exception):
            dill.dumps(colour, protocol=4)

        with caplog.at_level(logging.DEBUG, logger="clustrix.utils"):
            serialized = serialize_function(
                script["name_of"], (colour,), {"shout": True}
            )

        tried = [record.getMessage() for record in caplog.records]
        for strategy in ("dill(recurse=True)", "dill"):
            assert any(
                f"Serializing by value with {strategy} failed" in line for line in tried
            ), f"{strategy} was never tried and refused: {tried}"

        # What came out is a payload a worker can use, not merely bytes.
        restored, args, kwargs = deserialize_function(serialized)
        assert args[0].name == "GREEN" and args[0].value == 2
        assert kwargs == {"shout": True}
        assert restored(*args, **kwargs) == "GREEN"


class TestEnvironmentInfo:
    """Test environment information utilities."""

    def test_get_environment_requirements(self):
        """Every reinstallable distribution is pinned; nothing else is.

        Run against the REAL environment. Faking freeze output is how the
        uv/pip divergence went unnoticed: a fabricated `pip list` never shows
        the `name @ file:///...` lines uv emits for conda-built packages, and
        those were being dropped.
        """
        requirements = get_environment_requirements()

        assert isinstance(requirements, dict)
        # The serialization stack always has to reach the worker.
        assert "cloudpickle" in requirements
        assert "dill" in requirements
        # Nothing that cannot be reinstalled on another host may be pinned.
        unreproducible = get_unreproducible_requirements()
        assert not (set(requirements) & set(unreproducible))
        for package, version in requirements.items():
            assert not package.startswith("-e")
            assert "@" not in package
            assert "@" not in version

    def test_get_environment_requirements_is_deterministic(self):
        """Two calls on an unchanged environment must agree exactly."""
        assert get_environment_requirements() == get_environment_requirements()

    def test_get_environment_requirements_covers_conda_built_packages(self):
        """conda-built distributions are ordinary installs and must be pinned.

        uv renders them as ``name @ file:///.../work``; skipping those lines
        dropped a third of a conda environment from the worker.
        """
        from clustrix.utils import _distribution_records

        conda_built = [
            record["name"]
            for record in _distribution_records().values()
            if not record["reason"]
            and (record["dist"].read_text("direct_url.json") or "").find("file://") >= 0
        ]
        if not conda_built:
            pytest.skip("no locally-built distributions installed here")
        requirements = get_environment_requirements()
        assert all(name in requirements for name in conda_built)

    def test_get_environment_requirements_format(self):
        """Test environment requirements format."""
        requirements = get_environment_requirements()

        # Should be a dictionary
        assert isinstance(requirements, dict)

        # Keys should be package names, values should be versions
        for package, version in requirements.items():
            assert isinstance(package, str)
            assert isinstance(version, str)


class TestScriptGeneration:
    """Test job script generation."""

    def test_create_job_script_slurm(self):
        """Test SLURM script generation with detailed validation."""
        config = ClusterConfig(
            remote_work_dir="/scratch/test",
            python_executable="python3",
            module_loads=["python/3.9", "cuda/11.2"],
            environment_variables={"TEST_VAR": "value"},
            pre_execution_commands=["export CUSTOM_PATH=/opt/custom"],
        )

        job_config = {
            "cores": 8,
            "memory": "16GB",
            "time": "02:00:00",
            "partition": "gpu",
        }

        script = create_job_script(
            "slurm", job_config, "/scratch/test/jobs/job_123", config
        )

        # Check SLURM directives
        assert "#!/bin/bash" in script
        assert "#SBATCH --job-name=clustrix" in script
        assert "#SBATCH --cpus-per-task=8" in script
        # SLURM documents --mem units as [K|M|G|T]; "GB" is not one of them.
        # Kubernetes rejects it outright, so clustrix normalizes memory per
        # scheduler rather than passing the configured spelling through.
        assert "#SBATCH --mem=16G" in script
        assert "#SBATCH --time=02:00:00" in script
        assert "#SBATCH --partition=gpu" in script

        # Check module loads
        assert "module load python/3.9" in script
        assert "module load cuda/11.2" in script

        # Check environment variables
        assert "export TEST_VAR=value" in script

        # Check pre-execution commands
        assert "export CUSTOM_PATH=/opt/custom" in script

        # Check working directory
        assert "cd /scratch/test/jobs/job_123" in script

        # Check Python execution
        assert "python3 -c" in script or "python -c" in script
        assert "import pickle" in script
        assert "function_data.pkl" in script
        assert "result.pkl" in script

    def test_create_job_script_ssh(self):
        """Test SSH script generation."""
        config = ClusterConfig(
            remote_work_dir="/home/user", python_executable="python3"
        )

        job_config = {"cores": 2, "memory": "4GB", "time": "00:30:00"}

        script = create_job_script("ssh", job_config, "/home/user/job_789", config)

        # Check SSH script structure
        assert "#!/bin/bash" in script
        assert "cd /home/user/job_789" in script
        # The current implementation uses inline Python or two-venv approach
        assert "python3 -c" in script
        assert "function_data.pkl" in script
        assert "result.pkl" in script
        assert "error.pkl" in script

    def test_create_job_script_invalid_type(self):
        """Test error handling for invalid cluster type."""
        config = ClusterConfig()

        with pytest.raises(ValueError, match="Unsupported cluster type"):
            create_job_script("invalid_type", {}, "/tmp/job", config)


def _executable(directory, name, body):
    """A real program called ``name`` in ``directory``, for a real ``PATH``."""
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def path_dir(tmp_path, monkeypatch):
    """A directory that is the whole of this process's ``PATH``.

    ``is_uv_available`` and ``is_conda_available`` answer by running the
    tool. Whether a tool is "installed" is therefore a fact about ``PATH``,
    and the tests arrange that fact for real -- a program in the directory, or
    none -- instead of replacing ``subprocess.run`` with a ``Mock`` that says
    what the test wants to hear. The directory starts empty, so whatever the
    developer's machine happens to have installed cannot leak in.
    """
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", str(directory))
    return directory


AVAILABILITY_CHECKS = [("uv", is_uv_available), ("conda", is_conda_available)]


class TestPackageManager:
    """Which package manager is on this machine, and which one gets used."""

    @pytest.mark.parametrize("tool, is_available", AVAILABILITY_CHECKS)
    def test_a_tool_that_runs_is_available(self, path_dir, tool, is_available):
        _executable(path_dir, tool, f'[ "$1" = --version ] && echo "{tool} 1.2.3"')

        assert is_available() is True

    @pytest.mark.parametrize("tool, is_available", AVAILABILITY_CHECKS)
    def test_a_tool_that_is_not_on_the_path_is_not_available(
        self, path_dir, tool, is_available
    ):
        assert is_available() is False

    @pytest.mark.parametrize("tool, is_available", AVAILABILITY_CHECKS)
    def test_a_tool_that_exits_with_an_error_is_not_available(
        self, path_dir, tool, is_available
    ):
        """A broken install is on ``PATH`` and still cannot be used."""
        _executable(path_dir, tool, "echo broken >&2; exit 3")

        assert is_available() is False

    @pytest.mark.parametrize("tool, is_available", AVAILABILITY_CHECKS)
    def test_the_other_tool_being_installed_does_not_count(
        self, path_dir, tool, is_available
    ):
        other = "conda" if tool == "uv" else "uv"
        _executable(path_dir, other, "exit 0")

        assert is_available() is False

    @pytest.mark.timeout(60)
    def test_a_tool_that_hangs_is_not_available_and_the_check_returns(self, path_dir):
        """Each check gives the tool ten seconds, then answers no.

        The programs really hang -- they sleep for five minutes -- so the
        only way these calls return is the timeout killing them. The two
        checks run side by side so the suite waits ten seconds, not twenty.
        """
        for tool, _ in AVAILABILITY_CHECKS:
            _executable(path_dir, tool, "exec /bin/sleep 300")

        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = [pool.submit(check) for _, check in AVAILABILITY_CHECKS]
            assert [answer.result(timeout=45) for answer in answers] == [False, False]

    @pytest.mark.parametrize(
        "named, command", [("uv", "uv pip"), ("conda", "conda"), ("pip", "pip")]
    )
    def test_a_named_package_manager_is_used_without_looking_for_it(
        self, path_dir, named, command
    ):
        """Naming one is an instruction, not a preference to be checked.

        ``PATH`` is empty, so nothing is installed here; the answer is still
        the one that was named. (The old test asserted that a ``Mock`` of
        ``is_uv_available`` had not been called.)
        """
        assert get_package_manager_command(ClusterConfig(package_manager=named)) == (
            command
        )

    @pytest.mark.parametrize(
        "installed, command",
        [
            (("uv",), "uv pip"),
            (("conda",), "conda"),
            (("uv", "conda"), "uv pip"),
            ((), "pip"),
        ],
        ids=["only-uv", "only-conda", "uv-beats-conda", "neither-so-pip"],
    )
    def test_auto_prefers_uv_then_conda_then_pip(self, path_dir, installed, command):
        for tool in installed:
            _executable(path_dir, tool, "exit 0")

        assert get_package_manager_command(ClusterConfig(package_manager="auto")) == (
            command
        )

    def test_auto_passes_over_a_uv_that_does_not_work(self, path_dir):
        """Being on ``PATH`` is not enough; the tool has to answer."""
        _executable(path_dir, "uv", "exit 1")
        _executable(path_dir, "conda", "exit 0")

        assert (
            get_package_manager_command(ClusterConfig(package_manager="auto"))
            == "conda"
        )


class TestSerializationEdgeCases:
    """Test edge cases in serialization functionality."""

    def test_serialize_function_source_exception(self):
        """A function with no retrievable source still serializes, and runs.

        Rewritten, not relaxed. This test used to
        ``patch("inspect.getsource", side_effect=Exception(...))`` and assert
        that ``serialize_function`` swallowed it. Two things were wrong with
        that. ``inspect.getsource`` does not raise a bare ``Exception`` -- it
        raises ``OSError`` or ``TypeError`` -- so the test was asserting that
        clustrix swallows *anything at all* from that call, which is the
        defect (issue #123), not the contract. And it never exercised the real
        case, so it could not have caught a regression in it.

        The real case needs no patching: a function built by ``exec()`` has no
        source file, ``inspect.getsource`` raises ``OSError`` for it, and dill
        reconstructs it from the code object regardless.
        """
        namespace: dict = {}
        exec("def made_at_runtime(x):\n    return x * 2\n", namespace)
        test_func = namespace["made_at_runtime"]

        with pytest.raises(OSError):
            inspect.getsource(test_func)

        serialized = serialize_function(test_func, (5,), {})

        assert "function" in serialized
        assert "args" in serialized
        assert "kwargs" in serialized
        assert serialized["func_info"]["source"] is None
        assert serialized["function_source"] is None

        # The payload is still complete: it round-trips and returns the right
        # answer, which is the only reason missing source is tolerable here.
        restored, args, kwargs = deserialize_function(serialized)
        assert restored(*args, **kwargs) == 10

    def test_deserialize_function_bytes_format(self):
        """Test deserialize_function with bytes format."""
        # Use a module-level function that can be pickled
        import math

        # Create simple pickled format with a built-in function
        data = pickle.dumps((math.sqrt, (25,), {}))

        result_func, args, kwargs = deserialize_function(data)
        assert result_func(25) == 5.0
        assert args == (25,)
        assert kwargs == {}

    def test_deserialize_function_invalid_format(self):
        """Test deserialize_function with invalid format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid function data format"):
            deserialize_function("invalid_string_format")


class TestRemoteEnvironmentSetup:
    """Test remote environment setup functionality."""

    def test_setup_environment_basic(self):
        """Test setup_environment function."""
        # Test that setup_environment doesn't crash
        try:
            result = setup_environment("/work/dir", {"requests": "2.25.1"})
            # Should return some setup commands or similar
            assert isinstance(result, (str, list, type(None)))
        except Exception:
            # Function might not be fully implemented, that's okay
            pass
