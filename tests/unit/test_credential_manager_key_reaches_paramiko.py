#!/usr/bin/env python3
"""An SSH key named in ``~/.clustrix/.env`` must actually be used.

``ConnectionManager.setup_ssh_connection`` asked the credential manager for
SSH credentials and then read them like this::

    if "password" in ssh_credentials: ...
    elif "key_file" in ssh_credentials: ...

``resolve_provider_credentials`` has never emitted ``key_file``. The field
it fills from ``SSH_PRIVATE_KEY_PATH`` is called ``private_key_path``, so
the second branch was unreachable: a user who put a key path in their
``.env`` and no password silently fell through to the SSH agent and the
default key files, and if neither of those held the right key the
connection failed with an authentication error naming none of it.

Nothing here is mocked. A real keypair is generated, a real in-process SSH
server is told to accept it, and the shipped ``ConnectionManager`` is
pointed at it with nothing but the ``.env`` file to go on -- so the test
passes only if the credential really did travel from the file to paramiko.
"""

import shutil
import subprocess

import pytest

import clustrix.credential_manager as credential_manager_module
from clustrix.config import ClusterConfig, get_config_dir
from clustrix.executor_connections import ConnectionManager
from tests.ssh_server import LocalSSHServer


@pytest.fixture
def keypair(tmp_path):
    """A real ed25519 keypair on disk, made by ssh-keygen."""
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is not installed")
    private = tmp_path / "id_ed25519"
    subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "clustrix-test",
            "-f",
            str(private),
        ],
        check=True,
        capture_output=True,
    )
    return private, private.with_suffix(".pub")


def _env_file_naming(private_key):
    config_dir = get_config_dir()
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    env_file = config_dir / ".env"
    env_file.write_text(f"SSH_PRIVATE_KEY_PATH={private_key}\n", encoding="utf-8")
    env_file.chmod(0o600)
    credential_manager_module._credential_manager = None
    return env_file


def test_a_key_path_from_the_env_file_authenticates(keypair, tmp_path, monkeypatch):
    """The whole path: .env -> credential manager -> paramiko -> logged in."""
    private, public = keypair
    monkeypatch.delenv("SSH_PASSWORD", raising=False)
    monkeypatch.delenv("SSH_PRIVATE_KEY_PATH", raising=False)
    _env_file_naming(private)

    root = tmp_path / "served"
    root.mkdir()
    # ``password=None`` refuses password auth outright, so the connection
    # can only succeed by presenting the key the .env file named.
    with LocalSSHServer(
        root=str(root), password=None, authorized_keys=[str(public)]
    ) as server:
        config = ClusterConfig(
            cluster_type="ssh",
            cluster_host=server.host,
            cluster_port=server.port,
            username="tester",
            ssh_host_key_policy="auto_add",
            remote_work_dir=str(root),
        )
        manager = ConnectionManager(config)
        manager.setup_ssh_connection()
        try:
            assert manager.ssh_client.get_transport().is_authenticated()
            assert server.authentications[-1][1] == "publickey"
        finally:
            manager.disconnect()


def test_the_key_is_the_only_thing_that_could_have_worked(keypair, tmp_path):
    """The test above must not be passing on the agent or a default key.

    With the .env file empty, the same connection has to fail -- otherwise
    the assertion above proves nothing about where the credential came
    from.
    """
    _private, public = keypair
    config_dir = get_config_dir()
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (config_dir / ".env").write_text("", encoding="utf-8")
    credential_manager_module._credential_manager = None

    root = tmp_path / "served"
    root.mkdir()
    with LocalSSHServer(
        root=str(root), password=None, authorized_keys=[str(public)]
    ) as server:
        config = ClusterConfig(
            cluster_type="ssh",
            cluster_host=server.host,
            cluster_port=server.port,
            username="tester",
            ssh_host_key_policy="auto_add",
            remote_work_dir=str(root),
        )
        manager = ConnectionManager(config)
        with pytest.raises(Exception):
            manager.setup_ssh_connection()
        manager.disconnect()
