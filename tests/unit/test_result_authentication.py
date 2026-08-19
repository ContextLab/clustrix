"""Authentication of everything a job hands back before it is deserialized.

``result.pkl`` was verified against a per-job HMAC key before ``dill.loads``
and ``error.pkl`` was not, so "make the job fail" was a complete bypass: a
hostile or compromised cluster only had to exit non-zero and leave a pickle
whose ``__reduce__`` runs whatever it likes. That code then ran on the
*submitting* machine, and both call sites wrapped the load in
``except Exception: pass``, so a failed attempt was silent (#121, #126).

Nothing here is mocked. The "remote host" is a real directory of real files;
the payloads are really pickled, really signed and really tampered with; the
worker programs clustrix generates are really executed by a real interpreter,
and the proof of code execution is a real directory appearing on disk.
"""

import base64
import hashlib
import hmac
import os
import pickle
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import cloudpickle
import dill
import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_core import ClusterExecutor
from clustrix.executor_scheduler_status import SchedulerStatusManager
from clustrix.utils import (
    PayloadAuthenticationError,
    job_execution_lines,
    serialize_function,
    verify_signed_payload,
)

KEY = "0123456789abcdef" * 4
OTHER_KEY = "f" * 64


def _sign(payload: bytes, key: str = KEY) -> str:
    return hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------
# A stand-in for the compromised cluster's SSH/SFTP surface. It is a real
# transport over a real directory: nothing is faked except the network.
# --------------------------------------------------------------------------


class LocalTransport:
    """`remote` paths are real paths in a real directory on this machine."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.commands: list = []

    def remote_file_exists(self, path) -> bool:
        return os.path.exists(path)

    def download_file(self, remote, local):
        shutil.copy(remote, local)

    def execute_remote_command(self, command, check=False):
        self.commands.append(command)
        # Mirrors `cat <file> 2>/dev/null`: the shell prints nothing and
        # succeeds when the file is absent.
        if command.startswith("cat "):
            target = command.split()[1].strip("'\"")
            try:
                return Path(target).read_text(), ""
            except OSError:
                return "", ""
        return "", ""

    def disconnect(self):
        """ClusterExecutor.__del__ calls this on teardown."""


class ProofOfExecution:
    """A payload whose reconstruction creates a directory on disk.

    ``os.makedirs`` rather than ``os.system``: the point is to prove that
    arbitrary callables run during unpickling, and a directory appearing is
    proof enough without spawning a shell inside the test suite.
    """

    def __init__(self, marker: Path):
        self.marker = str(marker)

    def __reduce__(self):
        return (os.makedirs, (self.marker,))


def _write_error_pkl(remote_dir: Path, payload_obj, key=None) -> bytes:
    blob = dill.dumps(payload_obj, protocol=4)
    (remote_dir / "error.pkl").write_bytes(blob)
    if key is not None:
        (remote_dir / "error.pkl.hmac").write_text(_sign(blob, key))
    return blob


# --------------------------------------------------------------------------
# V1 -- error.pkl must clear the same bar as result.pkl
# --------------------------------------------------------------------------


class TestErrorPayloadAuthentication:
    @pytest.fixture
    def remote(self, tmp_path):
        directory = tmp_path / "job_1"
        directory.mkdir()
        return directory

    def _manager(self, remote):
        return SchedulerStatusManager(ClusterConfig(), LocalTransport(remote))

    def _active(self, remote, key=KEY):
        info = {"remote_dir": str(remote)}
        if key is not None:
            info["result_key"] = key
        return {"job1": info}

    def test_unsigned_error_pkl_is_refused_and_never_executed(self, remote, tmp_path):
        """The exploit: fail the job, ship a pickle that runs code."""
        marker = tmp_path / "pwned_get_error_log"
        _write_error_pkl(
            remote,
            {"error": "boom", "traceback": "t", "exception": ProofOfExecution(marker)},
        )
        manager = self._manager(remote)

        with pytest.raises(PayloadAuthenticationError, match="no signature"):
            manager.get_error_log("job1", self._active(remote))

        assert not marker.exists(), "unsigned error.pkl was deserialized"

    def test_unsigned_error_pkl_is_refused_by_extract_original_exception(
        self, remote, tmp_path
    ):
        marker = tmp_path / "pwned_extract"
        _write_error_pkl(
            remote,
            {"error": "boom", "traceback": "t", "exception": ProofOfExecution(marker)},
        )
        manager = self._manager(remote)

        with pytest.raises(PayloadAuthenticationError, match="no signature"):
            manager.extract_original_exception("job1", self._active(remote))

        assert not marker.exists(), "unsigned error.pkl was deserialized"

    def test_refusal_is_not_swallowed_into_the_text_log_fallback(self, remote):
        """`except Exception: pass` here would hide the forgery entirely."""
        _write_error_pkl(remote, {"error": "boom", "traceback": "t"})
        (remote / "job.err").write_text("some ordinary stderr")
        manager = self._manager(remote)

        with pytest.raises(PayloadAuthenticationError):
            manager.get_error_log("job1", self._active(remote))

    def test_correctly_signed_error_pkl_is_accepted(self, remote):
        _write_error_pkl(
            remote,
            {
                "error": "boom",
                "traceback": "Traceback ...",
                "exception": ValueError("boom"),
            },
            key=KEY,
        )
        manager = self._manager(remote)

        log = manager.get_error_log("job1", self._active(remote))
        assert "boom" in log
        exc = manager.extract_original_exception("job1", self._active(remote))
        assert isinstance(exc, ValueError)

    def test_tampered_error_pkl_is_refused(self, remote, tmp_path):
        """Keep the tag, swap the bytes -- the attack the HMAC exists for."""
        _write_error_pkl(remote, {"error": "boom", "traceback": "t"}, key=KEY)
        marker = tmp_path / "pwned_tamper"
        (remote / "error.pkl").write_bytes(
            dill.dumps({"exception": ProofOfExecution(marker)}, protocol=4)
        )
        manager = self._manager(remote)

        with pytest.raises(PayloadAuthenticationError, match="integrity check"):
            manager.get_error_log("job1", self._active(remote))
        assert not marker.exists()

    def test_error_pkl_signed_with_another_jobs_key_is_refused(self, remote):
        _write_error_pkl(remote, {"error": "boom"}, key=OTHER_KEY)
        manager = self._manager(remote)

        with pytest.raises(PayloadAuthenticationError, match="integrity check"):
            manager.get_error_log("job1", self._active(remote))

    def test_error_pkl_with_no_recorded_key_is_refused(self, remote):
        """V3 on the error path: no key means no way to tell, so refuse."""
        _write_error_pkl(remote, {"error": "boom"}, key=KEY)
        manager = self._manager(remote)

        with pytest.raises(PayloadAuthenticationError, match="No result-signing key"):
            manager.get_error_log("job1", self._active(remote, key=None))

    def test_absent_error_pkl_falls_through_to_the_text_logs(self, remote):
        (remote / "job.err").write_text("plain stderr output")
        manager = self._manager(remote)

        assert "plain stderr" in manager.get_error_log("job1", self._active(remote))


# --------------------------------------------------------------------------
# V3 -- a missing key is a refusal, not a warning
# --------------------------------------------------------------------------


class TestVerificationFailsClosed:
    PAYLOAD = pickle.dumps({"answer": 42}, protocol=4)

    def _executor(self, tmp_path, key):
        remote = tmp_path / "job_1"
        remote.mkdir()
        (remote / "result.pkl.hmac").write_text(_sign(self.PAYLOAD))
        executor = ClusterExecutor(ClusterConfig(cluster_type="ssh"))
        executor.connection_manager = LocalTransport(remote)  # type: ignore[assignment]
        entry = {"remote_dir": str(remote)}
        if key is not None:
            entry["result_key"] = key
        executor.scheduler_manager.active_jobs["job-1"] = entry
        return executor, remote

    def test_no_key_recorded_is_refused(self, tmp_path):
        executor, remote = self._executor(tmp_path, key=None)

        with pytest.raises(PayloadAuthenticationError, match="No result-signing key"):
            executor._verify_result_signature("job-1", str(remote), self.PAYLOAD)

    def test_empty_key_string_is_refused(self, tmp_path):
        executor, remote = self._executor(tmp_path, key="")

        with pytest.raises(PayloadAuthenticationError, match="No result-signing key"):
            executor._verify_result_signature("job-1", str(remote), self.PAYLOAD)

    def test_untracked_job_is_refused(self, tmp_path):
        executor, remote = self._executor(tmp_path, key=KEY)
        executor.scheduler_manager.active_jobs.clear()

        with pytest.raises(PayloadAuthenticationError, match="No result-signing key"):
            executor._verify_result_signature("job-1", str(remote), self.PAYLOAD)

    def test_a_recorded_key_still_verifies(self, tmp_path):
        executor, remote = self._executor(tmp_path, key=KEY)
        executor._verify_result_signature("job-1", str(remote), self.PAYLOAD)


# --------------------------------------------------------------------------
# The worker half: the generated programs really do sign what they write.
# These run the emitted Python for real, in a subprocess.
# --------------------------------------------------------------------------


def _single_venv_program(config=None) -> str:
    """The `python -c` program out of the generated single-venv job script."""
    lines = job_execution_lines("/does/not/matter", config or ClusterConfig())
    start = next(i for i, line in enumerate(lines) if line.rstrip().endswith('-c "'))
    end = next(i for i, line in enumerate(lines) if i > start and line == '"')
    return "\n".join(lines[start + 1 : end])


def _write_function_data(directory: Path, func, args=(), kwargs=None) -> None:
    data = serialize_function(func, args, kwargs or {})
    with open(directory / "function_data.pkl", "wb") as handle:
        pickle.dump(data, handle, protocol=4)


def _run_worker(directory: Path, program: str, key: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, CLUSTRIX_RESULT_KEY=key)
    return subprocess.run(
        [sys.executable, "-c", program],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
    )


def _boom():
    raise ValueError("the function raised")


def _read_the_key():
    """Stands in for a malicious dependency inside the job process."""
    return os.environ.get("CLUSTRIX_RESULT_KEY")


class TestGeneratedWorkerSignsWhatItWrites:
    def test_error_pkl_is_written_with_a_verifiable_signature(self, tmp_path):
        _write_function_data(tmp_path, _boom)
        result = _run_worker(tmp_path, _single_venv_program(), KEY)

        assert result.returncode != 0, result.stdout
        blob = (tmp_path / "error.pkl").read_bytes()
        tag = (tmp_path / "error.pkl.hmac").read_text()
        # No exception means the caller would accept it.
        verify_signed_payload(blob, tag, KEY, "worker")
        assert isinstance(dill.loads(blob)["exception"], ValueError)

    def test_result_pkl_is_written_with_a_verifiable_signature(self, tmp_path):
        _write_function_data(tmp_path, _read_the_key)
        result = _run_worker(tmp_path, _single_venv_program(), KEY)

        assert result.returncode == 0, result.stderr
        blob = (tmp_path / "result.pkl").read_bytes()
        verify_signed_payload(
            blob, (tmp_path / "result.pkl.hmac").read_text(), KEY, "worker"
        )

    def test_the_key_is_gone_from_the_environment_before_the_function_runs(
        self, tmp_path
    ):
        """V5 on the SSH path: anything in the job could forge a result."""
        _write_function_data(tmp_path, _read_the_key)
        result = _run_worker(tmp_path, _single_venv_program(), KEY)

        assert result.returncode == 0, result.stderr
        blob = (tmp_path / "result.pkl").read_bytes()
        verify_signed_payload(
            blob, (tmp_path / "result.pkl.hmac").read_text(), KEY, "worker"
        )
        assert dill.loads(blob) is None, "the function could still read the key"


# --------------------------------------------------------------------------
# V2 -- the cloud path signs its result and the caller checks it
# --------------------------------------------------------------------------


class TestCloudResultAuthentication:
    def _script(self, work_dir: Path) -> str:
        from clustrix.executor_cloud import CloudJobManager

        return CloudJobManager(ClusterConfig())._create_cloud_execution_script(
            str(work_dir), {}
        )

    def test_cloud_worker_signs_its_result(self, tmp_path):
        script = self._script(tmp_path)
        data = serialize_function(_read_the_key, (), {})
        with open(tmp_path / "func_data.pkl", "wb") as handle:
            cloudpickle.dump(data, handle)
        script_path = tmp_path / "execute_job.py"
        script_path.write_text(script)

        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=tmp_path,
            env=dict(os.environ, CLUSTRIX_RESULT_KEY=KEY),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        blob = (tmp_path / "result.pkl").read_bytes()
        verify_signed_payload(
            blob, (tmp_path / "result.pkl.hmac").read_text(), KEY, "cloud worker"
        )
        # Written with dill, as the caller's dill.load has always claimed.
        assert dill.loads(blob) is None

    def test_the_caller_verifies_before_deserializing(self):
        """A regression guard on the seam that had no check at all."""
        import inspect

        from clustrix.executor_cloud import CloudJobManager

        source = inspect.getsource(CloudJobManager._execute_job_on_cloud_instance)
        assert "verify_signed_payload" in source
        assert "result.pkl.hmac" in source or ".hmac" in source

    def test_an_unsigned_cloud_result_would_be_refused(self):
        blob = dill.dumps({"answer": 42}, protocol=4)

        with pytest.raises(PayloadAuthenticationError, match="no signature"):
            verify_signed_payload(blob, "", KEY, "Cloud job x")


# --------------------------------------------------------------------------
# V6 -- the serializer requirement is stated, not silently degraded
# --------------------------------------------------------------------------


class TestSerializerRequirementIsExplicit:
    def test_single_venv_program_refuses_to_fall_back_to_stdlib_pickle(self, tmp_path):
        """Without dill or cloudpickle it used to reach pickle.loads(dill bytes)."""
        program = _single_venv_program()
        assert "_argser = dill or cloudpickle or pickle" not in program

        _write_function_data(tmp_path, _read_the_key)
        # Hide both serializers from the child by pointing it at a sitecustomize
        # that blocks the imports -- a real interpreter without them.
        blocker = tmp_path / "blocker"
        blocker.mkdir()
        # find_spec, not find_module: the legacy finder API was removed in
        # Python 3.12, so a find_module-based blocker is simply ignored there
        # and the child imports dill perfectly well -- the test then passes
        # vacuously on <=3.11 and fails on 3.12 for the wrong reason.
        (blocker / "sitecustomize.py").write_text(textwrap.dedent("""
                import sys
                class _Block:
                    def find_spec(self, name, path=None, target=None):
                        if name in ("dill", "cloudpickle"):
                            raise ImportError(name)
                        return None
                sys.meta_path.insert(0, _Block())
                """))
        env = dict(os.environ, CLUSTRIX_RESULT_KEY=KEY, PYTHONPATH=str(blocker))
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "pip install dill" in result.stderr, result.stderr

    def test_two_venv_preamble_says_why_instead_of_using_pickle(self):
        from clustrix.utils import generate_two_venv_execution_commands

        script = "\n".join(generate_two_venv_execution_commands("/j", None, None))
        assert "_ser = pickle" not in script
        assert "pip install dill" in script


def test_signature_helper_refuses_every_unverifiable_case():
    payload = base64.b64encode(b"payload")
    verify_signed_payload(payload, _sign(payload), KEY, "x")

    for tag, key, match in (
        (_sign(payload), None, "No result-signing key"),
        (_sign(payload), "", "No result-signing key"),
        ("", KEY, "no signature"),
        ("   \n ", KEY, "no signature"),
        (_sign(payload)[:32], KEY, "integrity check"),
        (_sign(payload, OTHER_KEY), KEY, "integrity check"),
    ):
        with pytest.raises(PayloadAuthenticationError, match=match):
            verify_signed_payload(payload, tag, key, "x")
