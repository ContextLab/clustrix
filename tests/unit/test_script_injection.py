"""Shell injection into the scripts and commands clustrix generates.

Every value in ``ClusterConfig`` that reaches a generated job script used to be
pasted in raw, so ``remote_work_dir='/scratch/$(touch /tmp/pwn)'`` or
``default_partition="gpu --wrap='touch /tmp/pwn'"`` ran as a command on the
cluster (#126). Two treatments are correct depending on the site, and both are
exercised here:

* ordinary shell words are quoted with ``shlex.quote``;
* places that must stay unquoted -- ``module load`` arguments, ``#SBATCH`` and
  friends -- are validated against a strict allowlist and refused by name.

The quoting tests do not read the script and squint at it: they hand the
generated line to a real ``bash`` and check that the marker file the payload
tries to create does not appear.
"""

import os
import subprocess
from pathlib import Path

import pytest

from clustrix.config import ClusterConfig
from clustrix.utils import (
    create_job_script,
    environment_setup_lines,
    result_key_export_line,
    validate_env_var_name,
    validate_shell_fragment,
)


def _bash(script: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run a generated fragment through a real shell."""
    return subprocess.run(
        ["bash", "-c", script], cwd=cwd, capture_output=True, text=True
    )


class LocalShell:
    """A real shell as the transport: `exec_command` runs bash on this box.

    Not a mock of paramiko -- it executes the command clustrix actually
    generated, which is the only way to show a metacharacter in a config
    value does not become a command.
    """

    def __init__(self, stub_bin: Path, cwd: Path):
        self.stub_bin = stub_bin
        self.cwd = cwd
        self.commands: list = []

    def exec_command(self, command):
        self.commands.append(command)
        import os

        env = dict(os.environ, PATH=f"{self.stub_bin}:{os.environ['PATH']}")
        completed = subprocess.run(
            ["bash", "-c", command],
            cwd=self.cwd,
            env=env,
            capture_output=True,
        )
        return (
            None,
            _Stream(completed.stdout, completed.returncode),
            _Stream(completed.stderr, completed.returncode),
        )


class _Stream:
    def __init__(self, data: bytes, status: int):
        self._data = data
        self.channel = self
        self._status = status

    def read(self) -> bytes:
        return self._data

    def recv_exit_status(self) -> int:
        return self._status


BASE_JOB_CONFIG = {
    "cores": 4,
    "memory": "8GB",
    "time": "01:00:00",
}


# --------------------------------------------------------------------------
# Quoted sites: ordinary shell words
# --------------------------------------------------------------------------


# ``shlex.quote`` protects the REMOTE shell: clustrix generates a bash script
# that runs on a Linux cluster, so the quoting is correct no matter what the
# client machine runs, and a Windows client is fully supported here. What
# cannot be done on a Windows client is *this class's verification method* --
# handing the generated fragment to a real POSIX shell and checking the
# payload's marker file never appears. Windows has no POSIX shell (the CI
# runner's ``bash`` is the WSL launcher with no distribution installed), so
# the observation these tests make does not exist there. The code under test
# is unchanged and is still covered on Linux and macOS.
_NO_POSIX_SHELL_REASON = (
    "These tests verify remote-shell quoting by executing the generated "
    "fragment in a real POSIX shell; Windows has no POSIX shell to execute it "
    "in. The quoting itself is client-OS-independent and is exercised on "
    "Linux/macOS."
)


@pytest.mark.skipif(os.name == "nt", reason=_NO_POSIX_SHELL_REASON)
class TestQuotedSites:
    def test_command_substitution_in_remote_work_dir_does_not_run(self, tmp_path):
        marker = tmp_path / "pwn_workdir"
        job_dir = f"/scratch/$(touch {marker})/job_1"

        line = result_key_export_line(job_dir)
        result = _bash(line, tmp_path)

        assert not marker.exists(), f"injection ran: {line}"
        assert result.returncode == 0

    def test_ssh_script_cd_line_does_not_run_a_substitution(self, tmp_path):
        marker = tmp_path / "pwn_cd"
        job_dir = f"/scratch/$(touch {marker})/job_1"
        config = ClusterConfig(cluster_type="ssh")

        script = create_job_script("ssh", dict(BASE_JOB_CONFIG), job_dir, config)
        # Only the directory-handling prologue is executed; the body needs a
        # venv that does not exist here.
        prologue = "\n".join(script.splitlines()[:3])
        _bash(prologue, tmp_path)

        assert not marker.exists(), f"injection ran:\n{prologue}"

    def test_environment_variable_value_is_quoted_not_executed(self, tmp_path):
        marker = tmp_path / "pwn_env"
        config = ClusterConfig(
            environment_variables={"SAFE": f"bar; touch {marker}"},
        )

        lines = environment_setup_lines(config)
        result = _bash("\n".join(lines) + '\nprintf %s "$SAFE"', tmp_path)

        assert not marker.exists(), f"injection ran: {lines}"
        assert result.stdout == f"bar; touch {marker}"

    def test_a_value_with_spaces_survives_quoting(self, tmp_path):
        config = ClusterConfig(environment_variables={"SPACED": "a b c"})

        lines = environment_setup_lines(config)
        result = _bash("\n".join(lines) + '\nprintf %s "$SPACED"', tmp_path)

        assert result.stdout == "a b c"

    def test_requirement_strings_reach_pip_as_single_words(self, tmp_path):
        """A requirement string is a shell word, so it is quoted.

        This drives the real `setup_remote_environment` and executes the
        command it builds in a real bash. Only the interpreter and pip are
        stubbed -- the transport, the shell and the generated command are the
        real ones.
        """
        from clustrix.utils import setup_remote_environment

        marker = tmp_path / "pwn_req"
        spec_version = f"0.3.8; touch {marker}"

        stub = tmp_path / "bin"
        stub.mkdir()
        (stub / "python3").write_text(
            "#!/bin/sh\nmkdir -p venv/bin\n: > venv/bin/activate\n"
        )
        (stub / "pip").write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> pip_args.txt\n')
        for name in ("python3", "pip"):
            (stub / name).chmod(0o755)

        shell = LocalShell(stub, tmp_path)
        setup_remote_environment(
            shell,
            str(tmp_path),
            {"dill": spec_version},
            ClusterConfig(python_executable="python3"),
        )

        assert not marker.exists(), f"injection ran:\n{shell.commands}"
        recorded = (tmp_path / "pip_args.txt").read_text().splitlines()
        assert f"dill=={spec_version}" in recorded


# --------------------------------------------------------------------------
# Validated sites: fragments that must stay unquoted
# --------------------------------------------------------------------------


class TestValidatedSites:
    def test_module_load_entry_with_a_metacharacter_is_refused(self):
        config = ClusterConfig(module_loads=["gcc; touch /tmp/pwn_module"])

        with pytest.raises(ValueError, match="module_loads"):
            environment_setup_lines(config)

    def test_an_ordinary_module_name_still_works(self):
        config = ClusterConfig(module_loads=["python/3.9", "cuda/11.2"])

        assert environment_setup_lines(config) == [
            "module load python/3.9",
            "module load cuda/11.2",
        ]

    def test_environment_variable_name_that_is_not_an_identifier_is_refused(self):
        config = ClusterConfig(
            environment_variables={"FOO; touch /tmp/pwn_name": "bar"}
        )

        with pytest.raises(ValueError, match="environment_variables"):
            environment_setup_lines(config)

    def test_partition_carrying_an_sbatch_directive_is_refused(self):
        job_config = dict(BASE_JOB_CONFIG, partition="gpu --wrap='touch /tmp/pwn'")

        with pytest.raises(ValueError, match="partition"):
            create_job_script(
                "slurm", job_config, "/scratch/jobs/job_1", ClusterConfig()
            )

    def test_a_job_directory_with_shell_syntax_is_refused_in_directives(self):
        """Directive lines cannot be quoted, so the value has to be clean.

        Narrowed from ["slurm", "pbs", "sge"] to SLURM: PBS and SGE were
        removed, and SLURM is now the only backend that writes the job
        directory into a scheduler directive. SSH, the other remote backend,
        writes it only into shell commands and is covered by the next test.
        """
        with pytest.raises(ValueError, match="remote_work_dir"):
            create_job_script(
                "slurm",
                dict(BASE_JOB_CONFIG),
                "/scratch/$(touch /tmp/pwn)/job_1",
                ClusterConfig(),
            )

    @pytest.mark.parametrize(
        "hostile_dir",
        [
            "/scratch/$(touch /tmp/pwn)/job_1",
            "/scratch/`touch /tmp/pwn`/job_1",
            "/scratch/x'; touch /tmp/pwn; '",
        ],
    )
    def test_an_ssh_job_directory_is_quoted_rather_than_expanded(self, hostile_dir):
        """SSH has no directive lines, so it defends by quoting instead.

        The value reaches `cd` and `cat` only inside single quotes, which the
        shell does not expand. Asserting this explicitly because the previous
        parametrisation over ["slurm", "pbs", "sge"] never covered SSH at all,
        and substituting SSH into the directive test above would have been a
        false negative: it raises nothing because it has nothing to validate.
        """
        script = create_job_script(
            "ssh", dict(BASE_JOB_CONFIG), hostile_dir, ClusterConfig()
        )

        # No unquoted occurrence of the payload anywhere in the script.
        assert "touch /tmp/pwn" in script  # it is present...
        for line in script.splitlines():
            if "touch /tmp/pwn" in line:
                # ...but only ever inside a single-quoted word.
                assert line.count("'") >= 2, line
                assert not line.startswith("cd /scratch"), line
        assert "cd " + hostile_dir not in script

    def test_walltime_and_cores_are_validated_too(self):
        with pytest.raises(ValueError, match="time"):
            create_job_script(
                "slurm",
                dict(BASE_JOB_CONFIG, time="01:00:00 --wrap='touch /tmp/pwn'"),
                "/scratch/jobs/job_1",
                ClusterConfig(),
            )

    def test_validators_name_the_offending_setting(self):
        with pytest.raises(ValueError) as excinfo:
            validate_shell_fragment("module_loads", "gcc; rm -rf /")
        assert "module_loads" in str(excinfo.value)

        with pytest.raises(ValueError) as excinfo:
            validate_env_var_name("1BAD")
        assert "environment_variables" in str(excinfo.value)

    def test_ordinary_values_pass_through_unchanged(self):
        assert validate_shell_fragment("partition", "gpu-a100") == "gpu-a100"
        assert validate_shell_fragment("remote_work_dir", "/scratch/u/jobs") == (
            "/scratch/u/jobs"
        )
        assert validate_env_var_name("MY_VAR_1") == "MY_VAR_1"


# --------------------------------------------------------------------------
# Pre-execution commands stay a fragment on purpose
# --------------------------------------------------------------------------


def test_pre_execution_commands_are_passed_through():
    """They are shell commands by definition; restricting them removes the
    feature. The user asking for a command to run is not an injection."""
    config = ClusterConfig(pre_execution_commands=["export A=1 && echo hi"])

    assert environment_setup_lines(config) == ["export A=1 && echo hi"]


# --------------------------------------------------------------------------
# V5 -- the signing key does not stay in the job's environment
# --------------------------------------------------------------------------


class TestSecretsLeaveTheEnvironment:
    def test_hf_bootstrap_pops_the_key_before_installing_anything(self):
        from clustrix.hf_jobs import _bootstrap_source

        source = _bootstrap_source()
        assert "os.environ.pop('CLUSTRIX_HMAC_KEY')" in source
        assert "os.environ['CLUSTRIX_HMAC_KEY']" not in source
        # Before pip runs: a package's own install hooks are third-party code.
        assert source.index("CLUSTRIX_HMAC_KEY") < source.index("pip','install")

    def test_hf_bootstrap_pops_the_account_token_too(self):
        from clustrix.hf_jobs import _bootstrap_source

        source = _bootstrap_source()
        assert "os.environ.pop('CLUSTRIX_HF_TOKEN')" in source
        assert "os.environ['CLUSTRIX_HF_TOKEN']" not in source

    def test_generated_job_scripts_pop_the_result_key(self):
        config = ClusterConfig()
        script = create_job_script(
            "slurm", dict(BASE_JOB_CONFIG), "/scratch/jobs/job_1", config
        )
        assert "_os.environ.pop('CLUSTRIX_RESULT_KEY', '')" in script
        assert "_os.environ.get('CLUSTRIX_RESULT_KEY'" not in script


class TestLogBlockSelectionIsNotSpoofable:
    """The HF parser used to take the FIRST block it saw."""

    def _manager(self):
        from clustrix.hf_jobs import HFJobsManager

        return HFJobsManager(ClusterConfig(cluster_type="huggingface"))

    def _emit(self, obj, key, begin, end):
        import base64
        import hashlib
        import hmac

        import dill

        raw = dill.dumps(obj)
        tag = hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
        return [begin, tag, base64.b64encode(raw).decode(), end]

    def test_a_decoy_block_printed_first_does_not_win(self):
        from clustrix.hf_jobs import RESULT_BEGIN, RESULT_END

        key = "a" * 64
        decoy = self._emit({"owned": True}, "b" * 64, RESULT_BEGIN, RESULT_END)
        real = self._emit({"answer": 42}, key, RESULT_BEGIN, RESULT_END)

        decoded = self._manager()._decode_between(
            decoy + real, RESULT_BEGIN, RESULT_END, key
        )
        assert decoded == {"answer": 42}

    def test_a_junk_block_printed_first_does_not_abort_the_read(self):
        from clustrix.hf_jobs import RESULT_BEGIN, RESULT_END

        key = "a" * 64
        junk = [RESULT_BEGIN, "not-a-tag", "not!base64!", RESULT_END]
        real = self._emit({"answer": 42}, key, RESULT_BEGIN, RESULT_END)

        decoded = self._manager()._decode_between(
            junk + real, RESULT_BEGIN, RESULT_END, key
        )
        assert decoded == {"answer": 42}

    def test_a_log_with_only_unverifiable_blocks_is_still_refused(self):
        from clustrix.hf_jobs import RESULT_BEGIN, RESULT_END

        forged = self._emit({"owned": True}, "b" * 64, RESULT_BEGIN, RESULT_END)

        with pytest.raises(RuntimeError, match="integrity check"):
            self._manager()._decode_between(forged, RESULT_BEGIN, RESULT_END, "a" * 64)
