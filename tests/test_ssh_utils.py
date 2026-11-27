"""The parts of ``clustrix.ssh_utils`` that ``test_ssh_automation.py`` leaves out.

This module used to patch ``Path.home``, ``subprocess.run``, ``os.chmod`` and
``paramiko.SSHClient`` and then assert the patches had been called. Seventeen
of its twenty tests had a real counterpart in ``tests/test_ssh_automation.py``
already (key discovery, generation, deployment by ``ssh-copy-id`` and by
paramiko, ``~/.ssh/config`` entries, the full ``setup_ssh_keys`` workflow)
and are gone. What is here is what nothing real covered:

* discovery when there is nothing to discover;
* ``ssh-keygen`` really missing from ``PATH``, and really refusing a path;
* a public key file that is not there;
* a first ``~/.ssh/config`` entry, with a non-default port;
* ``get_ssh_key_info`` / ``list_ssh_keys`` reading real keys with the real
  ``ssh-keygen -l``.

``$HOME`` is a real temporary directory for every test (the autouse
``isolate_home`` fixture), so ``Path.home()`` is never patched.
"""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from clustrix.config import ClusterConfig
from clustrix.ssh_utils import (
    SSHKeyDeploymentError,
    SSHKeyGenerationError,
    deploy_public_key,
    detect_existing_ssh_key,
    find_ssh_keys,
    generate_ssh_key,
    get_ssh_key_info,
    list_ssh_keys,
    update_ssh_config,
)
from tests.ssh_server import LocalSSHServer, generate_keypair
from tests.terminal import run_on_a_terminal


@pytest.fixture
def ssh_dir():
    return Path(os.environ["HOME"]) / ".ssh"


class TestFindSSHKeys:
    def test_an_empty_ssh_directory_holds_no_keys(self, ssh_dir):
        assert list(ssh_dir.iterdir()) == []
        assert find_ssh_keys() == []

    def test_a_home_with_no_ssh_directory_holds_no_keys(self, ssh_dir):
        ssh_dir.rmdir()

        assert find_ssh_keys() == []
        assert not ssh_dir.exists(), "looking for keys must not create ~/.ssh"


class TestDetectExistingSSHKey:
    def test_a_key_the_server_was_never_given_is_not_reported_as_working(
        self, ssh_dir, tmp_path
    ):
        """A real, well-formed key that a real server really refuses."""
        generate_keypair(ssh_dir, "id_ed25519")
        root = tmp_path / "remote"
        root.mkdir()
        with LocalSSHServer(root=root, password="unused") as server:
            config = ClusterConfig(
                cluster_host=server.host,
                cluster_port=server.port,
                username="testuser",
                ssh_host_key_policy="auto_add",
            )

            found = detect_existing_ssh_key(
                server.host, "testuser", server.port, config=config
            )

            print("authentications:", server.authentications)
            assert found is None
            assert server.authentications == []


class TestGenerateSSHKey:
    def test_an_rsa_key_with_a_comment_and_a_passphrase(self, tmp_path):
        """The options ``generate_ssh_key_pair`` never passes."""
        key_path = str(tmp_path / "keys" / "id_rsa_test")

        private, public = generate_ssh_key(
            key_path, key_type="rsa", passphrase="correct horse", comment="me@laptop"
        )

        assert (private, public) == (key_path, f"{key_path}.pub")
        assert stat.S_IMODE(os.stat(private).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(public).st_mode) == 0o644
        assert stat.S_IMODE(os.stat(tmp_path / "keys").st_mode) == 0o700
        kind, _, comment = Path(public).read_text().split()
        assert (kind, comment) == ("ssh-rsa", "me@laptop")
        # The passphrase is real: the wrong one cannot unlock the key, and
        # the right one can.
        wrong = subprocess.run(
            ["ssh-keygen", "-y", "-P", "wrong", "-f", private], capture_output=True
        )
        right = subprocess.run(
            ["ssh-keygen", "-y", "-P", "correct horse", "-f", private],
            capture_output=True,
            text=True,
        )
        assert wrong.returncode != 0
        assert right.stdout.split()[:2] == Path(public).read_text().split()[:2]

    def test_without_ssh_keygen_on_the_path_it_says_what_to_install(
        self, tmp_path, monkeypatch
    ):
        empty_bin = tmp_path / "bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))
        assert shutil.which("ssh-keygen") is None

        with pytest.raises(SSHKeyGenerationError, match="ssh-keygen not found"):
            generate_ssh_key(str(tmp_path / "id_ed25519"))

        assert not (tmp_path / "id_ed25519").exists()

    def test_a_key_type_ssh_keygen_does_not_know_is_reported_with_its_reason(
        self, tmp_path
    ):
        with pytest.raises(SSHKeyGenerationError, match="Failed to generate SSH key"):
            generate_ssh_key(str(tmp_path / "id_bogus"), key_type="no-such-type")

        assert not (tmp_path / "id_bogus").exists()

    def test_on_a_terminal_an_existing_key_is_refused_rather_than_asked_about(
        self, tmp_path
    ):
        """``ssh-keygen`` asks "Overwrite (y/n)?" -- into a captured pipe.

        With a person's terminal as stdin it then waited for an answer to a
        question it had never shown them. Run on a real pty, because that
        is the only place the hang exists: under pytest's own stdin
        ``ssh-keygen`` reads end-of-file and gives up by itself.
        """
        existing = generate_keypair(tmp_path, "id_ed25519")
        before = existing.read_bytes()
        script = (
            "from clustrix.ssh_utils import generate_ssh_key, SSHKeyGenerationError\n"
            "try:\n"
            f"    generate_ssh_key({str(existing)!r})\n"
            "except SSHKeyGenerationError as exc:\n"
            "    print('REFUSED', exc)\n"
        )

        displayed, status = run_on_a_terminal(
            [sys.executable, "-c", script], [], dict(os.environ), timeout=30
        )

        assert status == 0
        assert "REFUSED Failed to generate SSH key" in displayed
        assert "already exists" in displayed, "the reason must reach the caller"
        assert existing.read_bytes() == before


class TestDeployPublicKey:
    def test_a_public_key_file_that_does_not_exist_is_an_error_before_any_connection(
        self, tmp_path
    ):
        with pytest.raises(SSHKeyDeploymentError, match="Cannot read public key file"):
            deploy_public_key(
                "nowhere.clustrix.invalid", "testuser", str(tmp_path / "missing.pub")
            )


class TestUpdateSSHConfig:
    def test_the_first_entry_creates_a_private_config_file(self, ssh_dir):
        config_path = ssh_dir / "config"
        assert not config_path.exists()

        update_ssh_config(
            "cluster.example.edu", "researcher", "/keys/id_ed25519", "hpc", port=2222
        )

        assert config_path.read_text() == (
            "\n# Clustrix auto-generated entry for hpc\n"
            "Host hpc\n"
            "    HostName cluster.example.edu\n"
            "    User researcher\n"
            "    IdentityFile /keys/id_ed25519\n"
            "    IdentitiesOnly yes\n"
            "    Port 2222\n"
        )
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600

    def test_openssh_reads_the_entry_the_way_it_was_meant(self, ssh_dir):
        """``ssh -G`` is OpenSSH's own account of what an alias resolves to."""
        update_ssh_config(
            "cluster.example.edu", "researcher", "/keys/id_ed25519", "hpc", port=2222
        )

        resolved = subprocess.run(
            ["ssh", "-F", str(ssh_dir / "config"), "-G", "hpc"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()

        print("\n".join(resolved[:12]))
        assert "hostname cluster.example.edu" in resolved
        assert "user researcher" in resolved
        assert "port 2222" in resolved
        assert "identityfile /keys/id_ed25519" in resolved
        assert "identitiesonly yes" in resolved

    def test_writing_the_same_entry_twice_leaves_one_entry(self, ssh_dir):
        for _ in range(2):
            update_ssh_config("cluster.example.edu", "researcher", "/keys/k", "hpc")

        assert (ssh_dir / "config").read_text().count("Host hpc\n") == 1


class TestSSHKeyInfo:
    def test_a_real_key_is_described_by_ssh_keygen(self, ssh_dir):
        private = generate_keypair(ssh_dir, "id_ed25519")
        fingerprint = subprocess.run(
            ["ssh-keygen", "-l", "-f", str(private)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()[1]

        info = get_ssh_key_info(str(private))

        print(info)
        assert info == {
            "path": str(private),
            "type": "ED25519",
            "bit_size": "256",
            "fingerprint": fingerprint,
            "comment": "clustrix-test",
            "exists": True,
        }
        assert fingerprint.startswith("SHA256:")

    def test_a_missing_key_is_reported_as_not_existing(self, tmp_path):
        missing = str(tmp_path / "id_missing")

        assert get_ssh_key_info(missing) == {"path": missing, "exists": False}

    def test_a_file_that_is_not_a_key_is_reported_as_not_existing(self, tmp_path):
        not_a_key = tmp_path / "id_rsa"
        not_a_key.write_text("this is not a key\n")

        assert get_ssh_key_info(str(not_a_key)) == {
            "path": str(not_a_key),
            "exists": False,
        }

    def test_listing_describes_exactly_the_keys_discovery_finds(self, ssh_dir):
        first = generate_keypair(ssh_dir, "id_ed25519")
        second = generate_keypair(ssh_dir, "id_ed25519_clustrix_me_hpc")
        generate_keypair(ssh_dir, "some_other_name")

        listed = list_ssh_keys()

        print(listed)
        assert [entry["path"] for entry in listed] == [str(first), str(second)]
        assert all(entry["exists"] and entry["type"] == "ED25519" for entry in listed)
        assert listed[0]["fingerprint"] != listed[1]["fingerprint"]
