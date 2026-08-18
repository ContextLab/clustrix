"""Tests for the HuggingFace Jobs backend.

These exercise the parts that do not need the network: the bootstrap program
the container runs, the integrity check applied to results, the cost guard on
GPU flavors, and the payload contract. No mocks -- everything here runs the
real functions against real bytes.

The end-to-end path (a real job in a real container) is covered by
tests/real_world/test_hf_jobs_real.py, which is opt-in because it costs money.
"""

import base64
import hashlib
import hmac

import dill
import pytest

from clustrix.config import ClusterConfig
from clustrix.hf_jobs import (
    DEFAULT_FLAVOR,
    ERROR_BEGIN,
    ERROR_END,
    MAX_PAYLOAD_BYTES,
    RESULT_BEGIN,
    RESULT_END,
    HFJobsManager,
    _MISSING,
    _bootstrap_source,
    is_gpu_flavor,
)

#: Every flavor HuggingFace actually offers, from huggingface_hub.JobHardware.
ALL_FLAVORS = [
    "cpu-basic",
    "cpu-upgrade",
    "cpu-performance",
    "cpu-xl",
    "t4-small",
    "t4-medium",
    "l4x1",
    "l4x4",
    "l40sx1",
    "l40sx4",
    "l40sx8",
    "a10g-small",
    "a10g-large",
    "a10g-largex2",
    "a10g-largex4",
    "a100-large",
    "a100x4",
    "a100x8",
    "h200",
    "h200x2",
    "h200x4",
    "h200x8",
    "rtx-pro-6000",
    "rtx-pro-6000x2",
    "rtx-pro-6000x4",
    "rtx-pro-6000x8",
]
GPU_FLAVORS = [f for f in ALL_FLAVORS if is_gpu_flavor(f)]


def _manager(**overrides) -> HFJobsManager:
    config = ClusterConfig(cluster_type="huggingface", **overrides)
    return HFJobsManager(config)


def _emit(obj, key: str, begin: str, end: str):
    """Reproduce exactly what the container bootstrap prints."""
    raw = dill.dumps(obj)
    return [
        begin,
        hmac.new(key.encode(), raw, hashlib.sha256).hexdigest(),
        base64.b64encode(raw).decode(),
        end,
    ]


class TestBootstrap:
    """The program that runs inside the container."""

    def test_bootstrap_is_valid_python(self):
        compile(_bootstrap_source(), "<bootstrap>", "exec")

    def test_bootstrap_survives_shell_embedding(self):
        """It is passed as `python -c "..."` inside a double-quoted shell word.

        A double quote would end that word early and a backslash or an
        unescaped `$` would be mangled by the shell, so none may appear.
        """
        src = _bootstrap_source()
        assert '"' not in src, "double quote would terminate the shell argument"
        assert "\\" not in src, "backslash would be consumed by the shell"
        assert "$" not in src, "dollar sign would be expanded by the shell"

    def test_bootstrap_reads_the_documented_payload_keys(self):
        """The bootstrap must unpack what serialize_function() produces."""
        src = _bootstrap_source()
        for key in ("function", "args", "kwargs"):
            assert f"p['{key}']" in src

    def test_bootstrap_signs_everything_it_emits(self):
        src = _bootstrap_source()
        assert "hmac.new(k,b,hashlib.sha256).hexdigest()" in src
        # Both the success and failure paths go through the signing helper.
        assert src.count("emit(") == 3  # one definition, two calls


class TestResultIntegrity:
    """A result recovered from a log stream is not trusted on sight."""

    def test_valid_result_round_trips(self):
        mgr = _manager()
        key = "a" * 64
        payload = {"answer": 42, "items": [1, 2, 3]}
        lines = _emit(payload, key, RESULT_BEGIN, RESULT_END)

        assert mgr._decode_between(lines, RESULT_BEGIN, RESULT_END, key) == payload

    def test_tampered_payload_is_refused(self):
        """Changing the bytes without the key must not deserialize."""
        mgr = _manager()
        key = "a" * 64
        lines = _emit({"answer": 42}, key, RESULT_BEGIN, RESULT_END)
        # Substitute an attacker-chosen object, leaving the original tag.
        lines[2] = base64.b64encode(dill.dumps({"answer": "owned"})).decode()

        with pytest.raises(RuntimeError, match="integrity check"):
            mgr._decode_between(lines, RESULT_BEGIN, RESULT_END, key)

    def test_wrong_key_is_refused(self):
        """A result signed with a different job's key must not be accepted."""
        mgr = _manager()
        lines = _emit({"answer": 42}, "b" * 64, RESULT_BEGIN, RESULT_END)

        with pytest.raises(RuntimeError, match="integrity check"):
            mgr._decode_between(lines, RESULT_BEGIN, RESULT_END, "a" * 64)

    def test_absent_block_reports_missing(self):
        mgr = _manager()
        assert (
            mgr._decode_between(["nothing here"], RESULT_BEGIN, RESULT_END, "k")
            is _MISSING
        )

    def test_marker_without_payload_reports_missing(self):
        """A truncated log must not raise something unrelated."""
        mgr = _manager()
        assert (
            mgr._decode_between([RESULT_BEGIN], RESULT_BEGIN, RESULT_END, "k")
            is _MISSING
        )

    def test_a_function_returning_none_is_not_mistaken_for_a_missing_result(self):
        """None is a perfectly good return value.

        Using None as the "no block found" signal made every function that
        returned None fail with "produced no clustrix result marker".
        """
        mgr = _manager()
        key = "d" * 64
        lines = _emit(None, key, RESULT_BEGIN, RESULT_END)

        decoded = mgr._decode_between(lines, RESULT_BEGIN, RESULT_END, key)
        assert decoded is None
        assert decoded is not _MISSING

    def test_stray_end_marker_in_user_output_does_not_truncate_the_block(self):
        """A function may print anything, including our own marker text."""
        mgr = _manager()
        key = "e" * 64
        lines = ["some output", RESULT_END, "more output"]
        lines += _emit({"ok": True}, key, RESULT_BEGIN, RESULT_END)

        assert mgr._decode_between(lines, RESULT_BEGIN, RESULT_END, key) == {"ok": True}

    def test_truncated_base64_is_reported_as_transport_not_as_an_attack(self):
        """A cut-off log is a re-run, not a forgery. The message must say so."""
        mgr = _manager()
        key = "f" * 64
        lines = _emit({"answer": 42}, key, RESULT_BEGIN, RESULT_END)
        lines[2] = lines[2][:-5]  # truncate the payload

        with pytest.raises(RuntimeError, match="truncated or interleaved"):
            mgr._decode_between(lines, RESULT_BEGIN, RESULT_END, key)

    def test_non_ascii_tag_is_a_mismatch_not_a_crash(self):
        """The tag comes off a log stream and may be anything at all."""
        mgr = _manager()
        key = "a" * 64
        lines = _emit({"answer": 42}, key, RESULT_BEGIN, RESULT_END)
        lines[1] = "\u00e9" * 64

        with pytest.raises(RuntimeError, match="integrity check"):
            mgr._decode_between(lines, RESULT_BEGIN, RESULT_END, key)

    def test_error_block_uses_the_same_protection(self):
        mgr = _manager()
        key = "c" * 64
        err = {"error": "boom", "traceback": "Traceback ..."}
        lines = _emit(err, key, ERROR_BEGIN, ERROR_END)

        assert mgr._decode_between(lines, ERROR_BEGIN, ERROR_END, key) == err

    def test_result_of_a_job_this_manager_did_not_submit_is_refused(self):
        """Without the per-job key there is nothing to verify against."""
        mgr = _manager(hf_token="x", hf_namespace="someone")
        with pytest.raises(RuntimeError, match="not submitted by this manager"):
            mgr.wait_for_result("a-job-id-we-never-saw")


class TestCostGuards:
    """GPU flavors bill real money, so selecting one is an explicit act."""

    @pytest.mark.parametrize("flavor", GPU_FLAVORS)
    def test_gpu_flavors_are_refused_by_default(self, flavor):
        mgr = _manager(hf_flavor=flavor)
        with pytest.raises(ValueError, match="GPU flavor"):
            mgr._flavor({})

    @pytest.mark.parametrize("flavor", GPU_FLAVORS)
    def test_gpu_flavors_are_allowed_with_explicit_opt_in(self, flavor):
        mgr = _manager(hf_flavor=flavor, hf_allow_gpu_flavors=True)
        assert mgr._flavor({}) == flavor

    def test_gpu_flavor_via_job_config_is_also_guarded(self):
        """The per-call override must not bypass the guard."""
        mgr = _manager()
        with pytest.raises(ValueError, match="GPU flavor"):
            mgr._flavor({"hf_flavor": "h200x8"})

    def test_an_unknown_flavor_is_treated_as_a_gpu_flavor(self):
        """The gate must fail closed when HuggingFace adds new hardware.

        A denylist of GPU names would wave through anything it had not heard
        of, which is the wrong way round for something that bills per second.
        """
        mgr = _manager()
        with pytest.raises(ValueError, match="GPU flavor"):
            mgr._flavor({"hf_flavor": "some-future-accelerator-x16"})

    @pytest.mark.parametrize("flavor", [f for f in ALL_FLAVORS if not is_gpu_flavor(f)])
    def test_cpu_flavors_need_no_opt_in(self, flavor):
        assert _manager(hf_flavor=flavor)._flavor({}) == flavor

    def test_default_flavor_is_a_cpu_flavor(self):
        assert _manager()._flavor({}) == DEFAULT_FLAVOR
        assert not is_gpu_flavor(DEFAULT_FLAVOR)


class TestImageSelection:
    """dill embeds CPython bytecode, so the container Python must match."""

    def test_default_image_tracks_the_local_interpreter(self):
        import sys

        expected = f"python:{sys.version_info.major}.{sys.version_info.minor}-slim"
        assert _manager()._image() == expected

    def test_explicit_image_is_respected(self):
        assert _manager(hf_image="python:3.11-slim")._image() == "python:3.11-slim"


class TestPayload:
    def test_oversized_payload_is_rejected_with_actionable_advice(self):
        mgr = _manager()
        big = b"x" * MAX_PAYLOAD_BYTES
        func_data = {"function": big, "args": b"", "kwargs": b""}

        with pytest.raises(ValueError, match="Hub dataset"):
            mgr.submit_job(func_data, {})

    def test_namespace_falls_back_to_username(self):
        assert _manager(hf_username="someone")._namespace() == "someone"

    def test_namespace_prefers_the_explicit_setting(self):
        mgr = _manager(hf_username="a-user", hf_namespace="an-org")
        assert mgr._namespace() == "an-org"


class TestAuthenticationErrors:
    def test_missing_token_says_what_to_set(self, monkeypatch, tmp_path):
        """With no token anywhere, the error must name every way to supply one.

        Isolated from the developer's own credentials: HF_TOKEN is cleared and
        HOME is redirected, or this passes or fails depending on whether
        whoever runs it happens to be logged in to HuggingFace.
        """
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        with pytest.raises(RuntimeError) as excinfo:
            _ = _manager().api

        message = str(excinfo.value)
        assert "hf_token" in message
        assert "HF_TOKEN" in message
        assert "hf auth login" in message

    def test_environment_variable_is_honoured(self, monkeypatch, tmp_path):
        """The error names HF_TOKEN, so following that advice must work.

        It previously read only the config field, so exporting HF_TOKEN --
        exactly what the message told you to do -- still failed.
        """
        monkeypatch.setenv("HF_TOKEN", "hf_from_the_environment")
        monkeypatch.setenv("HOME", str(tmp_path))

        assert _manager().api is not None

    def test_cli_login_token_is_honoured(self, monkeypatch, tmp_path):
        """`hf auth login` writes here; clustrix should not ask again."""
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("HF_HOME", raising=False)
        # expanduser("~") reads HOME on POSIX and USERPROFILE on Windows, so a
        # test that sets only HOME silently reads the real user's token there.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        cache = tmp_path / ".cache" / "huggingface"
        cache.mkdir(parents=True)
        (cache / "token").write_text("hf_from_the_cli\n")

        assert _manager().api is not None

    def test_hf_home_relocates_the_token(self, monkeypatch, tmp_path):
        """HF_HOME moves the whole directory; the token moves with it."""
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "empty"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty"))
        elsewhere = tmp_path / "scratch" / "hf"
        elsewhere.mkdir(parents=True)
        (elsewhere / "token").write_text("hf_from_hf_home\n")
        monkeypatch.setenv("HF_HOME", str(elsewhere))

        assert _manager().api is not None

    def test_config_field_wins_over_the_environment(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HF_TOKEN", "hf_from_the_environment")
        monkeypatch.setenv("HOME", str(tmp_path))

        manager = _manager(hf_token="hf_from_the_config")
        assert manager.api.token == "hf_from_the_config"


class TestAFunctionRaisingIsNotAJobFailure:
    """A raised exception is an ordinary outcome, not a broken job.

    The container reported the exception faithfully and then re-raised, so the
    process exited non-zero, Hugging Face marked the job ERROR, and the account
    owner got a "status changed to ERROR" email -- for a Python function doing
    exactly what it was written to do. Only a failure to *report* is a job
    failure.

    These run the real bootstrap in a subprocess. The pip step is satisfied
    from the local environment, so no network is needed.
    """

    @staticmethod
    def _run_bootstrap(func, args):
        import base64
        import os
        import subprocess
        import sys

        import dill

        from clustrix.hf_jobs import _bootstrap_source

        payload = dill.dumps(
            {
                "function": dill.dumps(func, recurse=True),
                "args": dill.dumps(args),
                "kwargs": dill.dumps({}),
            },
            protocol=4,
        )
        env = dict(
            os.environ,
            CLUSTRIX_PAYLOAD=base64.b64encode(payload).decode(),
            CLUSTRIX_HMAC_KEY="0" * 64,
            CLUSTRIX_PACKAGES="",
        )
        return subprocess.run(
            [sys.executable, "-c", _bootstrap_source()],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_the_container_exits_cleanly_when_the_function_raises(self):
        def explode(x):
            raise ValueError(f"boom {x}")

        result = self._run_bootstrap(explode, (7,))
        assert result.returncode == 0, result.stderr[-500:]

    def test_the_error_is_still_reported(self):
        """Exiting cleanly must not mean exiting quietly."""
        from clustrix.hf_jobs import ERROR_BEGIN, ERROR_END

        def explode(x):
            raise ValueError(f"boom {x}")

        result = self._run_bootstrap(explode, (7,))
        assert ERROR_BEGIN in result.stdout and ERROR_END in result.stdout

    def test_the_traceback_stays_in_the_job_log(self):
        """Whoever opens the job page should still see what went wrong."""

        def explode(x):
            raise ValueError(f"boom {x}")

        result = self._run_bootstrap(explode, (7,))
        assert "ValueError: boom 7" in result.stdout + result.stderr

    def test_a_successful_function_still_emits_a_result(self):
        from clustrix.hf_jobs import RESULT_BEGIN

        def double(x):
            return x * 2

        result = self._run_bootstrap(double, (21,))
        assert result.returncode == 0
        assert RESULT_BEGIN in result.stdout
