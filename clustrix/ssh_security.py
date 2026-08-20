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
``ClusterConfig`` explicitly -- it is never the default. Even then, the key
is *appended* to ``known_hosts`` by :class:`AppendUnknownHostKeyPolicy`
rather than persisted the way ``paramiko.AutoAddPolicy`` does it, which is
by rewriting the whole file (issue #157).
"""

import base64
import hashlib
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

import paramiko
from paramiko.hostkeys import HostKeyEntry

from .config import write_text_securely

logger = logging.getLogger(__name__)

#: The only values accepted for ``ClusterConfig.ssh_host_key_policy``.
VALID_HOST_KEY_POLICIES = ("reject", "auto_add")

#: How OpenSSH spells each of those policies in ``StrictHostKeyChecking``.
#: ``yes`` refuses a host whose key is not already in ``known_hosts``;
#: ``accept-new`` trusts it on first contact and records it, which is what
#: :class:`AppendUnknownHostKeyPolicy` does on the paramiko side.
OPENSSH_STRICT_HOST_KEY_CHECKING = {"reject": "yes", "auto_add": "accept-new"}


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


class AppendUnknownHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Trust an unknown host key and *append* it to ``known_hosts``.

    Installed by ``ssh_host_key_policy="auto_add"`` in place of paramiko's
    own ``AutoAddPolicy``, which cannot be used here: its
    ``missing_host_key`` calls ``client.save_host_keys(filename)``, and
    that method reloads the file and then opens it ``"w"`` -- truncate --
    and re-emits every entry from paramiko's in-memory model. Accepting one
    key therefore rewrites the user's entire file. Three things follow, all
    of them measured rather than argued (issue #157):

    * **Content paramiko cannot round-trip is destroyed.** Comments, blank
      lines, one line naming several hosts, and any key type paramiko has
      no parser for (``sk-ssh-ed25519@openssh.com``, which OpenSSH itself
      reads) do not come back out. Nothing fails at the time.
    * **Concurrent writers interleave.** Twelve threads adding at once
      corrupted the file in 10 runs out of 10, leaving NUL runs and
      half-written base64 in the middle of unrelated entries.
    * **An interrupted rewrite truncates**, losing everything past the cut.

    Once one line is cut mid-base64, ``paramiko.HostKeys.load`` raises
    ``InvalidHostKey`` on it, so *every later* connection fails -- the
    user's own ``ssh`` included, to hosts that had nothing to do with
    clustrix.

    A new host key is one new line, so this appends that line and touches
    nothing else. It is what ``ssh-keyscan host >> ~/.ssh/known_hosts``
    does, and what the ``reject`` policy's own error message tells the user
    to run.

    **What this does not protect against.** A single ``O_APPEND`` write of
    one short line is atomic against other appenders on a local
    filesystem, which is why concurrent adds cannot interleave and a crash
    cannot leave half a line. It is *not* a lock, and it makes no claim
    about NFS, where ``O_APPEND`` is not honoured. It cannot defend the
    file against another tool that rewrites it wholesale -- ``ssh-keygen
    -R`` does exactly that -- it only guarantees clustrix is not one of
    them. And appending never removes anything, so a host whose key
    genuinely changed keeps its stale line; that is not a regression,
    because paramiko raises ``BadHostKeyException`` for a known host with a
    changed key without ever consulting this policy, and ``auto_add`` never
    had a say in it.
    """

    def missing_host_key(
        self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey
    ) -> None:
        line = HostKeyEntry([hostname], key).to_line()
        if line is None:  # pragma: no cover - paramiko sets valid=True in __init__
            raise paramiko.SSHException(
                f"Cannot record the host key offered by '{hostname}': paramiko "
                f"produced no known_hosts line for a {key.get_name()} key."
            )

        # In-memory first, so the rest of *this* process recognises the host
        # even if the file write fails and raises.
        client.get_host_keys().add(hostname, key.get_name(), key)

        known_hosts = user_known_hosts_path()
        # OpenSSH's own modes for a directory it creates before first
        # contact. write_text_securely creates the file itself at 0600.
        known_hosts.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # append=True is the mode write_text_securely documents for exactly
        # this file: it appends with a single O_APPEND write, does not
        # follow-and-chmod (known_hosts is commonly a symlink into a
        # dotfiles repo), and leaves the mode of a file it did not create
        # alone.
        write_text_securely(known_hosts, line, append=True)
        logger.warning(
            "Trusted the unverified host key %s offered by %s and appended it "
            "to %s.",
            _fingerprint(key),
            hostname,
            known_hosts,
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
    * ``load_host_keys`` fills ``_host_keys``, which is what
      ``client.get_host_keys()`` returns and what
      :class:`AppendUnknownHostKeyPolicy` adds to, so that a host accepted
      earlier in this process is recognised later in it.

    It also sets paramiko's ``_host_keys_filename``, which used to be the
    load-bearing part: ``paramiko.AutoAddPolicy`` persists a key only when
    that attribute is set, and it persists it by rewriting the whole file.
    Nothing calls ``save_host_keys`` any more (issue #157), so the attribute
    is now incidental -- but the call still is not redundant, because
    ``get_host_keys()`` would otherwise be empty. Covered by
    ``tests/unit/test_host_key_policy.py::test_user_known_hosts_file_is_actually_loaded``.
    """
    client.load_system_host_keys()
    user_known_hosts = user_known_hosts_path()
    if user_known_hosts.exists():
        client.load_host_keys(str(user_known_hosts))


def host_key_policy_name(config: Optional[object] = None) -> str:
    """The validated ``ssh_host_key_policy`` that applies to ``config``.

    Split out of :func:`configure_host_key_policy` because paramiko is not
    the only thing in clustrix that opens an SSH connection:
    ``ssh_utils.deploy_public_key`` shells out to ``ssh-copy-id``, and that
    subprocess used to hardcode ``StrictHostKeyChecking=accept-new`` --
    silently applying the deliberate opt-out to every user, including the
    default ``reject``. Two readings of the same setting drift, so there is
    one reading and both callers use it.

    Args:
        config: A ``ClusterConfig``, a mapping carrying an
            ``"ssh_host_key_policy"`` key (the notebook widget hands its
            configuration over as a dict), or ``None``. ``None`` and a
            missing key both mean the secure default, ``"reject"``.

    Raises:
        ValueError: if the value is neither ``"reject"`` nor ``"auto_add"``.
    """
    if isinstance(config, Mapping):
        policy_name = config.get("ssh_host_key_policy") or "reject"
    else:
        policy_name = getattr(config, "ssh_host_key_policy", None) or "reject"
    if policy_name not in VALID_HOST_KEY_POLICIES:
        raise ValueError(
            f"Invalid ssh_host_key_policy={policy_name!r}. "
            f"Valid values are {VALID_HOST_KEY_POLICIES!r}."
        )
    return policy_name


def openssh_strict_host_key_checking(config: Optional[object] = None) -> str:
    """``StrictHostKeyChecking`` value for an OpenSSH subprocess.

    The one translation of :func:`host_key_policy_name` into OpenSSH's
    vocabulary, so a ``ssh``/``ssh-copy-id`` invocation cannot end up more
    permissive than the paramiko connections beside it.
    """
    return OPENSSH_STRICT_HOST_KEY_CHECKING[host_key_policy_name(config)]


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

    policy_name = host_key_policy_name(config)

    if policy_name == "auto_add":
        logger.warning(
            "ssh_host_key_policy='auto_add': unknown SSH host keys will be "
            "trusted automatically without verification. This is insecure "
            "and vulnerable to machine-in-the-middle attacks; use only for "
            "deliberate first contact with a host you already trust "
            "out-of-band."
        )
        # Not paramiko.AutoAddPolicy: that one persists the key it accepted
        # by rewriting the user's entire known_hosts, which loses content it
        # cannot round-trip and corrupts the file outright under a concurrent
        # writer or an interrupted write. AppendUnknownHostKeyPolicy adds the
        # one new line instead, and creates the file and its 0700 directory
        # itself if they do not exist yet -- so, unlike the paramiko policy,
        # it does not silently persist nothing on a machine that has never
        # had a ~/.ssh/known_hosts. See issue #157 and the class docstring.
        client.set_missing_host_key_policy(AppendUnknownHostKeyPolicy())
    else:
        client.set_missing_host_key_policy(RejectUnknownHostKeyPolicy())
