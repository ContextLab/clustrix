"""Shared SSH host key verification policy for all paramiko connections.

Every site in clustrix that opens a ``paramiko.SSHClient`` connection must
decide what to do when it doesn't recognize the remote host's key. Historically
every call site used ``paramiko.AutoAddPolicy()``, which silently accepts and
trusts *any* host key on first connection -- this makes every SSH connection
clustrix makes vulnerable to a machine-in-the-middle attack, since there is no
verification against the user's ``known_hosts`` at all.

This module is the single place that decision is made. Every call site in
clustrix must call :func:`configure_host_key_policy` on its ``SSHClient``
instead of calling ``set_missing_host_key_policy`` directly.

Default behavior (``ssh_host_key_policy="reject"``, the ``ClusterConfig``
default): host keys are checked against the system and user
``known_hosts`` files, and an unrecognized host key raises
:class:`HostKeyVerificationError` with the exact ``ssh-keyscan`` command
needed to add it. Opting into the old, insecure "trust everything"
behavior requires setting ``ssh_host_key_policy="auto_add"`` on
``ClusterConfig`` explicitly -- it is never the default.
"""

import base64
import hashlib
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

import paramiko

logger = logging.getLogger(__name__)

#: The only values accepted for ``ClusterConfig.ssh_host_key_policy``.
VALID_HOST_KEY_POLICIES = ("reject", "auto_add")


class HostKeyVerificationError(paramiko.SSHException):
    """Raised when a remote host's SSH key is not in the known_hosts files.

    Subclasses ``paramiko.SSHException`` so code that already catches that
    (or ``Exception``) continues to see connection failures as failures --
    it just gets a much more actionable message.
    """


def _fingerprint(key: paramiko.PKey) -> str:
    """SHA256 fingerprint in the same format ``ssh-keygen -l`` prints."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class RejectUnknownHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Reject any host key not already present in the client's known_hosts.

    This is paramiko's built-in ``RejectPolicy`` behavior, but with an
    actionable error message: which host, which key, and the exact command
    to run to add it deliberately.
    """

    def missing_host_key(
        self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey
    ) -> None:
        port_hint = ""
        transport = client.get_transport()
        if transport is not None:
            peer = transport.getpeername()
            if peer and len(peer) > 1 and peer[1] not in (22, None):
                port_hint = f" -p {peer[1]}"

        raise HostKeyVerificationError(
            f"Host key verification failed for '{hostname}': this host is not "
            f"in your known_hosts file(s), so clustrix refused the connection "
            f"rather than risk a machine-in-the-middle attack.\n"
            f"  Offered key: {key.get_name()} {_fingerprint(key)}\n\n"
            f"To fix this:\n"
            f"  1. If you recognize and trust this host, add its key with:\n"
            f"       ssh-keyscan{port_hint} {hostname} >> ~/.ssh/known_hosts\n"
            f"     then retry.\n"
            f"  2. If you understand the risk and want clustrix to trust "
            f"unknown host keys automatically (NOT recommended -- this is "
            f"exactly the behavior that enables MITM attacks), set on "
            f"ClusterConfig:\n"
            f'       ssh_host_key_policy="auto_add"\n'
        )


def user_known_hosts_path() -> Path:
    """The known_hosts file clustrix reads and writes.

    Derived from ``$HOME`` so a test, a container or a relocated home can
    redirect it. Every OpenSSH *subprocess* must be handed this path
    explicitly with ``-o UserKnownHostsFile=``: OpenSSH resolves ``~`` from
    the passwd database rather than the environment, so without that the
    Python side of clustrix verifies against one file while ssh appends to
    another.

    This is the single definition. ``ssh_utils`` imports it rather than
    recomputing the path, because two copies of a rule like this drift and
    the drift is invisible until the two disagree on a machine where the
    passwd home and ``$HOME`` differ.
    """
    return Path(os.path.expanduser("~")) / ".ssh" / "known_hosts"


def _load_known_hosts(client: paramiko.SSHClient) -> None:
    """Load system and user known_hosts files into the client.

    The second call looks redundant -- ``load_system_host_keys(None)`` already
    reads ``~/.ssh/known_hosts`` -- and it is not. The two land in different
    places inside paramiko:

    * ``load_system_host_keys`` fills ``_system_host_keys``, which is consulted
      when verifying and **never written back**.
    * ``load_host_keys`` fills ``_host_keys`` *and* sets
      ``_host_keys_filename``.

    ``AutoAddPolicy.missing_host_key`` saves only ``if
    client._host_keys_filename is not None``. So dropping the second call
    would leave verification working while silently stopping
    ``ssh_host_key_policy="auto_add"`` from ever persisting a key -- every
    connection would re-accept the same host forever, and nothing would fail
    to say so. Covered by
    ``tests/unit/test_host_key_policy.py::test_auto_add_persists_the_key_it_accepted``.
    """
    client.load_system_host_keys()
    user_known_hosts = user_known_hosts_path()
    if user_known_hosts.exists():
        client.load_host_keys(str(user_known_hosts))


def configure_host_key_policy(
    client: paramiko.SSHClient, config: Optional[object] = None
) -> None:
    """Set up host key loading and verification policy on ``client``.

    This is the single implementation every clustrix call site must use in
    place of ``client.set_missing_host_key_policy(paramiko.AutoAddPolicy())``.

    Args:
        client: The ``paramiko.SSHClient`` to configure. Must be configured
            before ``client.connect(...)`` is called.
        config: A ``ClusterConfig`` instance (or any object exposing a
            ``ssh_host_key_policy`` attribute), a mapping carrying an
            ``"ssh_host_key_policy"`` key (the notebook widget hands its
            configuration over as a dict), or ``None``. When ``None``, or
            when the key/attribute is absent, the secure default
            (``"reject"``) applies -- there is no code path that silently
            becomes insecure for lack of a config object.

    Raises:
        ValueError: if ``config.ssh_host_key_policy`` is set to something
            other than ``"reject"`` or ``"auto_add"``.
    """
    _load_known_hosts(client)

    # The notebook widget carries its configuration as a plain dict rather
    # than a ClusterConfig, so accept either. Reading it here keeps every
    # call site on the one policy decision instead of each one inventing a
    # way to hand its own shape over.
    if isinstance(config, Mapping):
        policy_name = config.get("ssh_host_key_policy") or "reject"
    else:
        policy_name = getattr(config, "ssh_host_key_policy", None) or "reject"
    if policy_name not in VALID_HOST_KEY_POLICIES:
        raise ValueError(
            f"Invalid ssh_host_key_policy={policy_name!r}. "
            f"Valid values are {VALID_HOST_KEY_POLICIES!r}."
        )

    if policy_name == "auto_add":
        logger.warning(
            "ssh_host_key_policy='auto_add': unknown SSH host keys will be "
            "trusted automatically without verification. This is insecure "
            "and vulnerable to machine-in-the-middle attacks; use only for "
            "deliberate first contact with a host you already trust "
            "out-of-band."
        )
        # AutoAddPolicy writes the key it accepted back to
        # ``client._host_keys_filename``, and paramiko only sets that
        # attribute inside ``load_host_keys``. _load_known_hosts skips that
        # call when the file does not exist yet -- it would raise -- so on a
        # machine with no ~/.ssh/known_hosts, auto_add accepted every host and
        # persisted nothing, re-accepting the same host on every connection
        # forever. That is trust-on-first-use with the "first" removed.
        #
        # Create it the way OpenSSH does before first contact: 0700 directory,
        # 0600 file. Only on this branch -- the reject policy must never write
        # to the user's filesystem as a side effect of verifying.
        known_hosts = user_known_hosts_path()
        if not known_hosts.exists():
            known_hosts.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            known_hosts.touch(mode=0o600)
            client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.set_missing_host_key_policy(RejectUnknownHostKeyPolicy())
