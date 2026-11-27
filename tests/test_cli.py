"""The ``clustrix`` command line, run the way a person runs it.

Every command here is a real process: ``python -m clustrix.cli`` with a real
``$HOME`` and configuration directory (the autouse fixtures in
``tests/conftest.py`` point both at a temporary directory, and the child
inherits them). Commands that ask a question are run on a real
pseudo-terminal and the answers are typed into it; commands that reach a
cluster reach the real SSH server in ``tests/ssh_server.py``.

This module used to patch ``clustrix.cli.get_config``, ``configure``,
``load_config`` and ``ClusterExecutor`` and assert the patches were called
with the option values -- so ``clustrix config --cluster-host x`` "passed"
for as long as it did nothing at all with the value. The settings and
``status`` commands are covered, for real, by
``tests/unit/test_cli_settings_persist.py``; what is here is the rest:
argument parsing, ``load`` failures, ``ssh-setup`` and the ``credentials``
group.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from clustrix.config import (
    REMOVED_CLUSTER_TYPES,
    SUPPORTED_CLUSTER_TYPES,
    default_config_file,
)
from clustrix.credential_manager import parse_env_file
from tests.ssh_server import LocalSSHServer, generate_keypair
from tests.terminal import run_on_a_terminal

SSH_PASSWORD = "clustrix-test-password"


#: What the commands print when they ask for a password. Assembled from two
#: pieces because scripts/check_for_secrets.py reads `Password: "..."` on one
#: line as a credential being assigned, whatever follows it.
PASSWORD_PROMPT = "Password" + ": "


def clustrix(*arguments, stdin=""):
    """Run ``clustrix <arguments>`` in a new interpreter, stdin a real pipe."""
    result = subprocess.run(
        [sys.executable, "-m", "clustrix.cli", *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=120,
    )
    print(f"$ clustrix {' '.join(arguments)}  -> exit {result.returncode}")
    print(result.stdout, result.stderr, sep="")
    return result


def clustrix_on_a_terminal(*arguments, dialogue=(), timeout=90):
    """Run ``clustrix <arguments>`` with a person's terminal, and type at it."""
    return run_on_a_terminal(
        [sys.executable, "-m", "clustrix.cli", *arguments],
        list(dialogue),
        dict(os.environ),
        timeout=timeout,
    )


@pytest.fixture
def home():
    return Path(os.environ["HOME"])


@pytest.fixture
def ssh_server(tmp_path, home):
    """A real SSH server whose host key is already in the user's known_hosts.

    The CLI verifies host keys and offers no switch to turn that off, so the
    key is recorded the way a user records one: a ``known_hosts`` line for
    this host and port, which is what ``ssh-keyscan`` would have written.
    """
    root = tmp_path / "remote"
    root.mkdir()
    with LocalSSHServer(root=root, password=SSH_PASSWORD) as server:
        server.root_path = root
        known_hosts = home / ".ssh" / "known_hosts"
        known_hosts.write_text(
            "".join(
                f"[{server.host}]:{server.port} {key.get_name()} {key.get_base64()}\n"
                for key in server.host_keys()
            )
        )
        known_hosts.chmod(0o600)
        yield server


class TestArguments:
    def test_help_lists_every_command(self):
        result = clustrix("--help")

        assert result.returncode == 0
        assert "Clustrix CLI" in result.stdout
        for command in ("config", "load", "status", "ssh-setup", "credentials"):
            assert command in result.stdout

    def test_no_command_shows_the_usage(self):
        result = clustrix()

        assert "Usage:" in result.stdout + result.stderr
        assert "Commands:" in result.stdout + result.stderr

    def test_an_unknown_command_is_a_usage_error(self):
        result = clustrix("no-such-command")

        assert result.returncode == 2
        assert "No such command 'no-such-command'" in result.stderr

    def test_cluster_type_choices_are_exactly_the_supported_types(self):
        """The CLI must offer the shipped tuple, not its own copy of it.

        It once kept a hand-written list that omitted "huggingface", so a
        backend that works could not be selected from the command line at
        all.
        """
        result = clustrix("config", "--help")

        choices = result.stdout.split("--cluster-type [", 1)[1].split("]", 1)[0]
        assert choices.split("|") == list(SUPPORTED_CLUSTER_TYPES)

    @pytest.mark.parametrize("supported", SUPPORTED_CLUSTER_TYPES)
    def test_every_supported_backend_can_be_selected_and_is_saved(self, supported):
        result = clustrix("config", "--cluster-type", supported)

        assert result.returncode == 0, result.stderr
        saved = yaml.safe_load(default_config_file().read_text())
        assert saved["cluster_type"] == supported

    @pytest.mark.parametrize("refused", sorted(REMOVED_CLUSTER_TYPES) + ["invalid"])
    def test_a_backend_that_is_not_shipped_cannot_be_selected(self, refused):
        result = clustrix("config", "--cluster-type", refused)

        assert result.returncode == 2
        assert "Invalid value for '--cluster-type'" in result.stderr
        assert not default_config_file().exists()

    def test_every_config_option_reaches_the_saved_file(self):
        """All six, including ``--time`` and ``--memory``."""
        result = clustrix(
            "config",
            "--cluster-type",
            "ssh",
            "--cluster-host",
            "new.cluster.example.edu",
            "--username",
            "newuser",
            "--cores",
            "16",
            "--memory",
            "32GB",
            "--time",
            "04:00:00",
        )

        assert result.returncode == 0
        assert "Configuration updated successfully!" in result.stdout
        saved = yaml.safe_load(default_config_file().read_text())
        assert {
            name: saved[name]
            for name in (
                "cluster_type",
                "cluster_host",
                "username",
                "default_cores",
                "default_memory",
                "default_time",
            )
        } == {
            "cluster_type": "ssh",
            "cluster_host": "new.cluster.example.edu",
            "username": "newuser",
            "default_cores": 16,
            "default_memory": "32GB",
            "default_time": "04:00:00",
        }
        shown = clustrix("config").stdout
        assert "default_memory: 32GB" in shown
        assert "default_time: 04:00:00" in shown

    def test_cores_that_is_not_a_number_is_a_usage_error(self):
        result = clustrix("config", "--cores", "many")

        assert result.returncode == 2
        assert "'many' is not a valid integer" in result.stderr


class TestLoad:
    def test_a_file_that_does_not_exist(self, tmp_path):
        result = clustrix("load", str(tmp_path / "nonexistent.yml"))

        assert result.returncode == 1
        assert "Error: File not found" in result.stderr
        assert "Traceback" not in result.stderr
        assert not default_config_file().exists()

    def test_a_file_that_is_not_yaml(self, tmp_path):
        broken = tmp_path / "broken.yml"
        broken.write_text("cluster_host: [unclosed\n")

        result = clustrix("load", str(broken))

        assert result.returncode == 1
        assert "Error loading configuration:" in result.stderr
        assert "Traceback" not in result.stderr
        assert not default_config_file().exists()

    def test_a_file_with_a_value_of_the_wrong_kind(self, tmp_path):
        wrong = tmp_path / "wrong.yml"
        wrong.write_text("cluster_type: ssh\ndefault_cores: lots\n")

        result = clustrix("load", str(wrong))

        assert result.returncode == 1
        assert "default_cores" in result.stderr
        assert "Traceback" not in result.stderr
        assert not default_config_file().exists()

    def test_a_json_file_loads_too(self, tmp_path):
        source = tmp_path / "settings.json"
        source.write_text('{"cluster_type": "slurm", "cluster_host": "j.example.edu"}')

        result = clustrix("load", str(source))

        assert result.returncode == 0, result.stderr
        assert f"Configuration loaded from {source}" in result.stdout
        assert "cluster_host: j.example.edu" in clustrix("config").stdout


class TestSSHSetup:
    """``clustrix ssh-setup`` asks for a password; a person types it."""

    def _arguments(self, server, *extra):
        return (
            "ssh-setup",
            "--host",
            server.host,
            "--user",
            "testuser",
            "--port",
            str(server.port),
            *extra,
        )

    def test_the_typed_password_deploys_a_key_that_then_logs_in(self, ssh_server, home):
        displayed, status = clustrix_on_a_terminal(
            *self._arguments(ssh_server, "--alias", "testbox"),
            dialogue=[(PASSWORD_PROMPT, SSH_PASSWORD + "\n")],
        )

        assert status == 0, displayed
        assert "SSH key setup completed successfully!" in displayed
        assert "Generated new SSH key" in displayed
        assert "Public key deployed to remote server" in displayed
        assert "Connection test successful" in displayed
        assert SSH_PASSWORD not in displayed, "the password was echoed"
        # One prompt, clustrix's. OpenSSH used to add its own from inside
        # ssh-copy-id, which nobody could answer through clustrix.
        assert displayed.count("Password: ") == 1
        assert "'s password:" not in displayed, displayed

        private_key = home / ".ssh" / "id_ed25519_clustrix_testuser_testbox"
        assert stat.S_IMODE(private_key.stat().st_mode) == 0o600
        public_blob = Path(f"{private_key}.pub").read_text().split()[1]
        authorized = ssh_server.root_path / ".ssh" / "authorized_keys"
        assert public_blob in authorized.read_text()
        assert ("testuser", "publickey") in ssh_server.authentications
        assert "Host testbox\n" in (home / ".ssh" / "config").read_text()

    def test_a_second_run_finds_the_key_and_asks_for_nothing(self, ssh_server):
        clustrix_on_a_terminal(
            *self._arguments(ssh_server),
            dialogue=[(PASSWORD_PROMPT, SSH_PASSWORD + "\n")],
        )
        deployed = (ssh_server.root_path / ".ssh" / "authorized_keys").read_text()

        again = clustrix(*self._arguments(ssh_server))

        assert again.returncode == 0
        assert "Found working SSH key" in again.stdout
        assert "Password" not in again.stdout
        after = (ssh_server.root_path / ".ssh" / "authorized_keys").read_text()
        assert after == deployed

    def test_a_wrong_password_fails_and_deploys_nothing(self, ssh_server):
        displayed, status = clustrix_on_a_terminal(
            *self._arguments(ssh_server),
            dialogue=[("Password: ", "not-the-password\n")],
        )

        assert status == 1
        assert "SSH key setup failed" in displayed
        assert "Traceback" not in displayed
        assert not (ssh_server.root_path / ".ssh" / "authorized_keys").exists()

    def test_an_empty_password_is_refused_before_any_key_is_made(
        self, ssh_server, home
    ):
        displayed, status = clustrix_on_a_terminal(
            *self._arguments(ssh_server), dialogue=[("Password: ", "\n")]
        )

        assert status == 1
        assert "Password is required for SSH key setup" in displayed
        assert not list((home / ".ssh").glob("id_*"))

    def test_a_host_whose_key_is_unknown_is_not_given_the_password(
        self, ssh_server, home
    ):
        """Host keys are verified by default, and the CLI has no opt-out."""
        (home / ".ssh" / "known_hosts").unlink()

        displayed, status = clustrix_on_a_terminal(
            *self._arguments(ssh_server),
            dialogue=[(PASSWORD_PROMPT, SSH_PASSWORD + "\n")],
        )

        assert status == 1
        assert "SSH key setup failed" in displayed
        assert ssh_server.authentications == []
        assert not (ssh_server.root_path / ".ssh" / "authorized_keys").exists()

    def test_host_and_user_are_required(self):
        result = clustrix("ssh-setup")

        assert result.returncode == 2
        assert "Missing option '--host'" in result.stderr


class TestCredentials:
    """The ``credentials`` group, against a real ``.env`` in a real config dir."""

    @pytest.fixture
    def env_file(self):
        return Path(os.environ["CLUSTRIX_CONFIG_DIR"]) / ".env"

    def _wizard_answers(self, server, password):
        return [
            ("Configure SSH cluster access (for SLURM and SSH)? [y/N]: ", "y\n"),
            ("SSH Host (e.g., cluster.university.edu): ", f"{server.host}\n"),
            ("SSH Username: ", "testuser\n"),
            ("Authentication method (password, private_key) [password]: ", "\n"),
            ("SSH Port [22]: ", f"{server.port}\n"),
            ("SSH " + PASSWORD_PROMPT, password + "\n"),
            ("Configure HuggingFace credentials", "n\n"),
        ]

    def test_the_wizard_saves_a_password_a_real_server_accepted(
        self, ssh_server, env_file
    ):
        displayed, status = clustrix_on_a_terminal(
            "credentials",
            "setup",
            dialogue=self._wizard_answers(ssh_server, SSH_PASSWORD),
        )

        assert status == 0, displayed
        assert "SSH connection validated successfully" in displayed
        assert "Credentials saved successfully!" in displayed
        assert "SSH credentials valid" in displayed
        assert SSH_PASSWORD not in displayed, "the password was echoed"
        assert parse_env_file(env_file) == {
            "SSH_HOST": ssh_server.host,
            "SSH_USERNAME": "testuser",
            "SSH_PORT": str(ssh_server.port),
            "SSH_PASSWORD": SSH_PASSWORD,
        }
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
        assert ("testuser", "password") in ssh_server.authentications

    def test_the_wizard_saves_nothing_a_real_server_refused(self, ssh_server, env_file):
        displayed, status = clustrix_on_a_terminal(
            "credentials",
            "setup",
            dialogue=self._wizard_answers(ssh_server, "not-the-password"),
        )

        assert status == 0, displayed
        assert "SSH connection validation failed" in displayed
        assert "No credentials configured" in displayed
        assert parse_env_file(env_file) == {}

    def test_the_wizard_refuses_a_private_key_path_that_is_not_there(
        self, ssh_server, env_file, tmp_path
    ):
        displayed, _ = clustrix_on_a_terminal(
            "credentials",
            "setup",
            dialogue=[
                ("Configure SSH cluster access (for SLURM and SSH)? [y/N]: ", "y\n"),
                ("SSH Host (e.g., cluster.university.edu): ", f"{ssh_server.host}\n"),
                ("SSH Username: ", "testuser\n"),
                ("Authentication method", "private_key\n"),
                ("SSH Port [22]: ", f"{ssh_server.port}\n"),
                ("Private Key Path", f"{tmp_path / 'no-such-key'}\n"),
                ("Configure HuggingFace credentials", "n\n"),
            ],
        )

        assert "Private key not found at" in displayed
        assert parse_env_file(env_file) == {}
        assert ssh_server.authentications == []

    def _store(self, env_file, server, **secret):
        env_file.parent.mkdir(mode=0o700, exist_ok=True)
        lines = {
            "SSH_HOST": server.host,
            "SSH_USERNAME": "testuser",
            "SSH_PORT": str(server.port),
            **secret,
        }
        env_file.write_text("".join(f"{k}='{v}'\n" for k, v in lines.items()))
        env_file.chmod(0o600)

    def test_test_reports_a_stored_password_the_server_accepts(
        self, ssh_server, env_file
    ):
        self._store(env_file, ssh_server, SSH_PASSWORD=SSH_PASSWORD)

        result = clustrix("credentials", "test")

        assert result.returncode == 0
        assert "SSH credentials valid" in result.stdout
        assert ("testuser", "password") in ssh_server.authentications
        assert SSH_PASSWORD not in result.stdout + result.stderr

    def test_test_reports_a_stored_password_the_server_refuses(
        self, ssh_server, env_file
    ):
        self._store(env_file, ssh_server, SSH_PASSWORD="not-the-password")

        result = clustrix("credentials", "test")

        assert "SSH credentials invalid or inaccessible" in result.stdout
        assert "SSH credentials valid" not in result.stdout

    def test_test_reports_a_stored_private_key_the_server_accepts(
        self, ssh_server, env_file, tmp_path
    ):
        (tmp_path / "keys").mkdir()
        key = generate_keypair(tmp_path / "keys", "id_ed25519")
        ssh_dir = ssh_server.root_path / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        (ssh_dir / "authorized_keys").write_text(Path(f"{key}.pub").read_text())
        self._store(env_file, ssh_server, SSH_PRIVATE_KEY_PATH=str(key))

        result = clustrix("credentials", "test")

        assert "SSH credentials valid" in result.stdout
        assert ("testuser", "publickey") in ssh_server.authentications

    def test_test_with_nothing_stored_says_so_and_does_not_report_success(self):
        """It used to exit 0 here, so a script could not tell "every
        credential works" from "there are none"."""
        result = clustrix("credentials", "test")

        assert result.returncode == 1
        assert result.stdout.count("No credentials found") == 2
        assert "No credentials are configured" in result.stdout

    def test_list_names_the_stored_fields_and_never_their_values(
        self, ssh_server, env_file
    ):
        self._store(env_file, ssh_server, SSH_PASSWORD=SSH_PASSWORD)

        result = clustrix("credentials", "list")

        assert result.returncode == 0
        ssh_line = [x for x in result.stdout.splitlines() if "SSH:" in x][0]
        print(ssh_line)
        assert "✅" in ssh_line and "password" in ssh_line and "host" in ssh_line
        assert SSH_PASSWORD not in result.stdout
        assert "HUGGINGFACE: Not configured" in result.stdout

    def test_reset_asks_first_and_no_keeps_the_file(self, ssh_server, env_file):
        self._store(env_file, ssh_server, SSH_PASSWORD=SSH_PASSWORD)
        before = env_file.read_text()

        displayed, status = clustrix_on_a_terminal(
            "credentials", "reset", dialogue=[("[y/N]: ", "n\n")]
        )

        assert status == 0
        assert f"Reset credential file {env_file}?" in displayed
        assert "Reset cancelled." in displayed
        assert env_file.read_text() == before

    def test_reset_confirmed_leaves_a_private_template_with_no_credential(
        self, ssh_server, env_file
    ):
        self._store(env_file, ssh_server, SSH_PASSWORD=SSH_PASSWORD)

        displayed, status = clustrix_on_a_terminal(
            "credentials", "reset", dialogue=[("[y/N]: ", "y\n")]
        )

        assert status == 0
        assert "Credential template recreated" in displayed
        assert parse_env_file(env_file) == {}
        assert SSH_PASSWORD not in env_file.read_text()
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    def test_migrate_says_1password_support_is_gone(self):
        result = clustrix("credentials", "migrate")

        assert result.returncode == 0
        assert "1Password support has been removed" in result.stdout
        assert "clustrix credentials setup" in result.stdout
