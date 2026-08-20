"""The real-world credential helper must not publish credentials.

``tests/real_world/credential_manager.py`` used to call
``setup_test_credentials()`` at module scope, which resolved every credential
it could find and copied the results into ``os.environ`` as ``TEST_SSH_HOST``,
``TEST_SSH_PASSWORD``, ``TEST_SLURM_PASSWORD``, ``HUGGINGFACE_TOKEN`` and
friends. Harmless while the only sources were variables the developer had
exported anyway; a leak as soon as issue #153 wired ``~/.clustrix/.env`` into
the same lookup, because the resolved value was then the developer's real
cluster password.

It fires on an ordinary ``pytest tests/`` run --
``tests/unit/test_cluster_network_detection.py`` imports the module -- so the
password reached the environment of the test process and of every subprocess
it spawned, including anything a test happened to shell out to.

Every check below runs the code in a fresh interpreter with a throwaway
``CLUSTRIX_CONFIG_DIR``: the leak is an import-time side effect, so importing
the module in this process would only prove what this process already did.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The password written into the throwaway ``.env``. Distinctive enough that
#: finding it in an environment variable cannot be a coincidence.
SENTINEL_PASSWORD = "sentinel-pw-2f8c41d9e7b04a1a"


def write_env_file(config_dir: Path, contents: str) -> Path:
    """Create ``config_dir/.env`` with `contents` and 0600 permissions."""
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    env_file = config_dir / ".env"
    env_file.write_text(contents, encoding="utf-8")
    env_file.chmod(0o600)
    return env_file


def run_probe(script: str, config_dir: Path, home: Path, **extra_env) -> dict:
    """Run `script` in a clean interpreter and return the JSON it prints.

    The environment is built from scratch rather than inherited: the developer
    running these tests may well have SSH_PASSWORD exported, and a check for
    "did importing this module export a password?" that inherits an exported
    password proves nothing.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "USER": "probe-user",
        "CLUSTRIX_CONFIG_DIR": str(config_dir),
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONHASHSEED": "0",
    }
    env.update(extra_env)

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"probe failed ({completed.returncode}):\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    # Logging may precede the payload; the JSON is always the last line.
    return json.loads(completed.stdout.strip().splitlines()[-1])


IMPORT_PROBE = """
import json, os
before = set(os.environ)
import tests.real_world.credential_manager as cm

creds = cm.get_credential_manager().get_ssh_credentials()
sentinel = {sentinel!r}
print(json.dumps({{
    "resolved_password": (creds or {{}}).get("password"),
    "added": sorted(set(os.environ) - before),
    "leaked": sorted(
        name for name, value in os.environ.items() if sentinel in value
    ),
}}))
""".format(sentinel=SENTINEL_PASSWORD)


@pytest.fixture
def configured_env(tmp_path):
    """A ``.env`` of exactly the shape CREDENTIAL_SETUP_HINT describes."""
    config_dir = tmp_path / "clustrix"
    write_env_file(
        config_dir,
        "SSH_HOST=cluster.example.edu\n"
        "SSH_USERNAME=probe-user\n"
        f"SSH_PASSWORD={SENTINEL_PASSWORD}\n",
    )
    return config_dir


class TestImportingTheModuleExportsNothing:
    def test_the_env_file_password_reaches_no_environment_variable(
        self, tmp_path, configured_env
    ):
        result = run_probe(IMPORT_PROBE, configured_env, tmp_path)

        # Guard against passing for the wrong reason: the credential must
        # actually have been resolved, otherwise there was nothing to leak.
        assert result["resolved_password"] == SENTINEL_PASSWORD
        assert result["leaked"] == [], (
            "the .env password was exported into "
            f"{result['leaked']} -- every subprocess of the test run can read it"
        )

    def test_importing_adds_no_credential_variables_at_all(
        self, tmp_path, configured_env
    ):
        result = run_probe(IMPORT_PROBE, configured_env, tmp_path)

        assert result["added"] == [], (
            "importing tests.real_world.credential_manager changed os.environ: "
            f"{result['added']}"
        )


UNCONFIGURED_PROBE = """
import json
import tests.real_world.credential_manager as cm

manager = cm.get_credential_manager()
print(json.dumps({
    "ssh": manager.get_ssh_credentials(),
    "slurm": manager.get_slurm_credentials(),
    "status": manager.get_credential_status(),
}))
"""


class TestUnconfiguredMeansUnconfigured:
    """``get_ssh_credentials`` used to invent a localhost/$USER target.

    Nothing was configured, yet the answer was a truthy dictionary, so the
    ``if not ssh_creds: pytest.skip(...)`` gates in conftest.py,
    test_ssh_real.py and test_ssh_job_execution_real.py never fired and their
    tests pointed clustrix at the developer's own machine over SSH.
    """

    def test_nothing_configured_yields_no_ssh_credentials(self, tmp_path):
        config_dir = tmp_path / "clustrix"

        result = run_probe(UNCONFIGURED_PROBE, config_dir, tmp_path)

        assert result["ssh"] is None
        assert result["slurm"] is None
        assert result["status"]["ssh"] is False
        assert result["status"]["slurm"] is False

    def test_exported_test_ssh_variables_still_work(self, tmp_path):
        """The TEST_SSH_* path is narrowed, not removed."""
        config_dir = tmp_path / "clustrix"

        result = run_probe(
            UNCONFIGURED_PROBE,
            config_dir,
            tmp_path,
            TEST_SSH_HOST="ssh.example.edu",
            TEST_SSH_USERNAME="probe-user",
            TEST_SSH_PASSWORD=SENTINEL_PASSWORD,
        )

        assert result["ssh"] == {
            "host": "ssh.example.edu",
            "username": "probe-user",
            "password": SENTINEL_PASSWORD,
            "private_key_path": None,
            "port": "22",
        }

    def test_a_host_without_a_secret_is_not_a_credential(self, tmp_path):
        """Two thirds of a login only fails several seconds into a connect."""
        config_dir = tmp_path / "clustrix"

        result = run_probe(
            UNCONFIGURED_PROBE,
            config_dir,
            tmp_path,
            TEST_SSH_HOST="ssh.example.edu",
            TEST_SSH_USERNAME="probe-user",
        )

        assert result["ssh"] is None


class TestAKeyFileMustExist:
    def test_a_missing_key_file_is_not_a_credential(self, tmp_path):
        config_dir = tmp_path / "clustrix"
        write_env_file(
            config_dir,
            "SSH_HOST=cluster.example.edu\n"
            "SSH_USERNAME=probe-user\n"
            f"SSH_PRIVATE_KEY_PATH={tmp_path / 'absent_key'}\n",
        )

        result = run_probe(UNCONFIGURED_PROBE, config_dir, tmp_path)

        assert result["ssh"] is None

    def test_a_key_file_that_exists_is_a_credential(self, tmp_path):
        key_file = tmp_path / "present_key"
        key_file.write_text("not a real key, but a real file\n", encoding="utf-8")
        key_file.chmod(0o600)
        config_dir = tmp_path / "clustrix"
        write_env_file(
            config_dir,
            "SSH_HOST=cluster.example.edu\n"
            "SSH_USERNAME=probe-user\n"
            f"SSH_PRIVATE_KEY_PATH={key_file}\n",
        )

        result = run_probe(UNCONFIGURED_PROBE, config_dir, tmp_path)

        assert result["ssh"] is not None
        assert result["ssh"]["private_key_path"] == str(key_file)


HINT_PROBE = """
import json
import tests.real_world.credential_manager as cm

print(json.dumps({
    "hint": cm.credential_setup_hint(),
    "default": cm.CREDENTIAL_SETUP_HINT,
    "unreadable": str(cm.unreadable_env_file() or ""),
}))
"""


class TestAnUnreadableEnvFileSaysSo:
    """chmod 000 used to be indistinguishable from "no credentials set"."""

    def test_a_readable_env_file_gets_the_ordinary_hint(self, tmp_path, configured_env):
        result = run_probe(HINT_PROBE, configured_env, tmp_path)

        assert result["unreadable"] == ""
        assert result["hint"] == result["default"]

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root can read a mode-000 file, so there is nothing to detect",
    )
    def test_an_unreadable_env_file_names_the_permission_problem(
        self, tmp_path, configured_env
    ):
        env_file = configured_env / ".env"
        env_file.chmod(0o000)
        try:
            result = run_probe(HINT_PROBE, configured_env, tmp_path)
        finally:
            env_file.chmod(0o600)

        assert result["unreadable"] == str(env_file)
        assert result["hint"] != result["default"]
        assert "permission denied" in result["hint"]
        assert str(env_file) in result["hint"]
