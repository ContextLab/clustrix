"""Tests for verifying a job's result before deserializing it.

Loading a pickle executes arbitrary code, so ``result.pkl`` -- a file fetched
from a remote host -- must not be opened on trust (#121). At submission the job
directory is created 0700 with a random key inside it; the job tags its result
with an HMAC over the exact bytes it wrote; the caller checks the tag first.

These drive the real verification method against real bytes. The end-to-end
behaviour is exercised against a live cluster too -- see the commit for
``_verify_result_signature`` -- but that needs a cluster, and these do not.
"""

import hashlib
import hmac
import pickle

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_core import ClusterExecutor

KEY = "0123456789abcdef" * 4
REMOTE_DIR = "/scratch/jobs/job_1"


class FakeConnection:
    """Returns a canned `cat result.pkl.hmac`, and records what was asked."""

    def __init__(self, tag):
        self.tag = tag
        self.commands = []

    def execute_remote_command(self, command):
        self.commands.append(command)
        if "result.pkl.hmac" in command:
            return self.tag, ""
        return "", ""

    def disconnect(self):
        """ClusterExecutor.__del__ calls this on teardown."""


def _executor(tag, *, key=KEY, track=True):
    executor = ClusterExecutor(ClusterConfig(cluster_type="ssh"))
    executor.connection_manager = FakeConnection(tag)  # type: ignore[assignment]
    if track:
        entry = {"remote_dir": REMOTE_DIR}
        if key is not None:
            entry["result_key"] = key
        executor.scheduler_manager.active_jobs["job-1"] = entry
    return executor


def _sign(payload: bytes, key: str = KEY) -> str:
    return hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()


PAYLOAD = pickle.dumps({"answer": 42}, protocol=4)


class TestSignatureVerification:
    def test_correctly_signed_result_is_accepted(self):
        executor = _executor(_sign(PAYLOAD))
        # No exception means accepted.
        executor._verify_result_signature("job-1", REMOTE_DIR, PAYLOAD)

    def test_tampered_payload_is_refused(self):
        """The attack this exists to stop: swap the bytes, keep the tag."""
        executor = _executor(_sign(PAYLOAD))
        evil = pickle.dumps({"answer": "owned"}, protocol=4)

        with pytest.raises(RuntimeError, match="integrity check"):
            executor._verify_result_signature("job-1", REMOTE_DIR, evil)

    def test_result_signed_with_another_jobs_key_is_refused(self):
        executor = _executor(_sign(PAYLOAD, key="f" * 64))

        with pytest.raises(RuntimeError, match="integrity check"):
            executor._verify_result_signature("job-1", REMOTE_DIR, PAYLOAD)

    def test_unsigned_result_is_refused(self):
        """An absent signature must not be read as 'nothing to check'."""
        executor = _executor("")

        with pytest.raises(RuntimeError, match="no signature"):
            executor._verify_result_signature("job-1", REMOTE_DIR, PAYLOAD)

    def test_whitespace_only_signature_is_refused(self):
        executor = _executor("   \n  ")

        with pytest.raises(RuntimeError, match="no signature"):
            executor._verify_result_signature("job-1", REMOTE_DIR, PAYLOAD)

    def test_truncated_signature_is_refused(self):
        executor = _executor(_sign(PAYLOAD)[:32])

        with pytest.raises(RuntimeError, match="integrity check"):
            executor._verify_result_signature("job-1", REMOTE_DIR, PAYLOAD)

    def test_signature_is_read_from_the_job_directory(self):
        executor = _executor(_sign(PAYLOAD))
        executor._verify_result_signature("job-1", REMOTE_DIR, PAYLOAD)

        assert any(
            f"{REMOTE_DIR}/result.pkl.hmac" in command
            for command in executor.connection_manager.commands
        )


class TestUntrackedJobs:
    """No key means no way to tell a real result from a forged one.

    This used to log a warning and load the pickle anyway, which made
    "arrange for the key to be forgotten" -- an adopted job id, a cleared
    table -- a complete bypass of the check. Verification fails closed.
    """

    def test_job_without_a_key_is_refused(self):
        executor = _executor(_sign(PAYLOAD), key=None)

        with pytest.raises(RuntimeError, match="No result-signing key"):
            executor._verify_result_signature("job-1", REMOTE_DIR, PAYLOAD)

    def test_job_with_an_empty_key_is_refused(self):
        executor = _executor(_sign(PAYLOAD), key="")

        with pytest.raises(RuntimeError, match="No result-signing key"):
            executor._verify_result_signature("job-1", REMOTE_DIR, PAYLOAD)

    def test_completely_unknown_job_is_refused(self):
        executor = _executor(_sign(PAYLOAD), track=False)

        with pytest.raises(RuntimeError, match="No result-signing key"):
            executor._verify_result_signature("nope", REMOTE_DIR, PAYLOAD)


class TestJobScriptEmitsTheSignature:
    """The remote half has to actually write the tag, or nothing verifies."""

    def test_stage_three_writes_a_signature_file(self):
        from clustrix.utils import generate_two_venv_execution_commands

        script = "\n".join(generate_two_venv_execution_commands("/j", "e1", "e2"))
        assert "result.pkl.hmac" in script
        assert "CLUSTRIX_RESULT_KEY" in script

    def test_signature_covers_the_bytes_actually_written(self):
        """Signing a re-serialization instead of the written bytes would give
        a tag that never matches what the caller downloads."""
        from clustrix.utils import generate_two_venv_execution_commands

        script = "\n".join(generate_two_venv_execution_commands("/j", "e1", "e2"))
        assert "_payload_bytes = _ser.dumps(result, protocol=4)" in script
        assert "f.write(_payload_bytes)" in script
        assert (
            "_hmac.new(_CLUSTRIX_KEY.encode(), _payload_bytes, _hashlib.sha256)"
            in script
        )

    def test_the_error_report_is_signed_too(self):
        """A job that fails must not be a cheaper way in than one that works."""
        from clustrix.utils import generate_two_venv_execution_commands

        script = "\n".join(generate_two_venv_execution_commands("/j", "e1", "e2"))
        assert "error.pkl.hmac" in script
        assert "_hmac.new(_CLUSTRIX_KEY.encode(), _blob, _hashlib.sha256)" in script

    def test_the_key_is_taken_out_of_the_environment(self):
        """Anything running in the job could otherwise forge a result."""
        from clustrix.utils import generate_two_venv_execution_commands

        script = "\n".join(generate_two_venv_execution_commands("/j", "e1", "e2"))
        assert "_os.environ.pop('CLUSTRIX_RESULT_KEY', '')" in script
        assert "environ.get('CLUSTRIX_RESULT_KEY'" not in script

    def test_key_is_read_from_a_file_not_baked_into_the_script(self):
        """job.sh is readable by others on some shared filesystems."""
        from clustrix.utils import result_key_export_line

        line = result_key_export_line("/scratch/job_1")
        assert "/scratch/job_1/.clustrix_result_key" in line
        assert "export CLUSTRIX_RESULT_KEY=" in line
