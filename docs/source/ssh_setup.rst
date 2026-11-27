SSH setup and host keys
=======================

The ``ssh`` and ``slurm`` backends reach the cluster over SSH. Two things
have to be in place: this machine must recognise the host, and the host must
accept you.

.. contents:: On this page
   :local:
   :depth: 1

.. _host-key-verification:

Host key verification
---------------------

Every SSH connection clustrix makes -- for key setup, for job submission, for
file transfer -- checks the remote host's SSH key against your local
``known_hosts`` files (``/etc/ssh/ssh_known_hosts`` and
``~/.ssh/known_hosts``) before doing anything else. By default
(``ssh_host_key_policy="reject"``), **a host key that isn't already recorded
there causes clustrix to refuse the connection outright.** This is not a
prompt you can click through; it is a hard failure with an actionable
message:

.. code-block:: text

   HostKeyVerificationError: Host key verification failed for '[127.0.0.1]:2222': this host is not in your known_hosts file(s), so clustrix refused the connection rather than risk a machine-in-the-middle attack.
     Offered key: ssh-ed25519 SHA256:t71ZfprzQXrLFRY47Bhv+RehzAB+NGmKLeKfi1cK//c

   To fix this:
     1. If you recognize and trust this host, add its key with:
          ssh-keyscan -p 2222 127.0.0.1 >> ~/.ssh/known_hosts
        then retry.
     2. If you understand the risk and want clustrix to trust unknown host keys automatically (NOT recommended -- this is exactly the behavior that enables MITM attacks), set on ClusterConfig:
          ssh_host_key_policy="auto_add"

(Produced by connecting to a test ``sshd`` with an empty ``known_hosts``.)

The first connection to a host therefore needs one of the following.

1. **Connect once with** ``ssh``, compare the fingerprint it shows with the
   one your cluster's administrators publish, and accept it. The key is then
   in ``known_hosts`` and Clustrix accepts the host. Running the
   ``ssh-keyscan`` command from the error message records the key without
   asking you anything; compare the "Offered key" line yourself first.
2. **In the notebook widget, press "Trust this host".** The widget shows the
   fingerprint of the key the host offered, and the button records exactly
   that key. See :doc:`api/notebook_magic`.
3. **In code, call** :func:`clustrix.ssh_security.trust_offered_host_key` with
   the error you caught. It records exactly the key that was offered, where
   scanning the host again would trust whatever answers the second time:

   .. code-block:: python

      # cluster-required: needs an SSH host that is not yet in known_hosts
      from clustrix import ClusterExecutor, get_config
      from clustrix.ssh_security import (
          HostKeyVerificationError,
          trust_offered_host_key,
      )

      executor = ClusterExecutor(get_config())
      try:
          executor.connect()
      except HostKeyVerificationError as error:
          print("Offered:", error.fingerprint)
          # Compare with the fingerprint your administrators publish.
          if input("Trust this host? [yes/no] ") == "yes":
              print("Recorded in", trust_offered_host_key(error))
              executor.connect()
      finally:
          executor.disconnect()

   Run against a test ``sshd`` with the question answered "yes", this printed
   ``Offered: ssh-ed25519 SHA256:t71ZfprzQXrLFRY47Bhv+RehzAB+NGmKLeKfi1cK//c``,
   appended one line to ``~/.ssh/known_hosts`` (created with mode ``0600`` in
   a ``0700`` directory), and connected.
4. Explicitly opt out with ``ssh_host_key_policy="auto_add"`` in your
   ``ClusterConfig`` or ``configure(...)`` call -- but understand that this
   accepts whatever key a host offers, which is genuinely insecure. Only do
   this for a host you already trust through some other channel (e.g. you set
   it up yourself and typed the hostname).

   The opt-out has to come from **you**. Setting it in a ``./clustrix.yml``
   that arrived with a ``git clone``, or in a directory
   ``$CLUSTRIX_CONFIG_DIR`` happens to point at, is ignored and warned
   about: turning verification off is a security decision, and it is a
   persistent one, so it is subject to the same provenance rule as a stored
   credential. See :ref:`untrusted-security-settings`.

``auto_add`` writes what it accepts, and writes it by **appending one line**.
Clustrix creates ``~/.ssh/known_hosts`` if it does not exist yet -- the
directory at mode ``0700`` and the file at ``0600``, which is what OpenSSH
itself does before first contact -- and then appends the accepted key to it,
exactly as ``ssh-keyscan host >> ~/.ssh/known_hosts`` would. Nothing already
in the file is read back and re-emitted.

That distinction matters more than it sounds. Clustrix does *not* use
paramiko's own ``AutoAddPolicy``, which persists a key by rewriting the entire
file: it drops comments, splits a line naming several hosts, silently discards
any key type it cannot parse (``sk-ssh-ed25519@openssh.com``, which OpenSSH
reads fine), and -- if two processes do it at once, or one is interrupted --
leaves entries cut mid-key. One corrupt line is enough to make *every*
subsequent SSH connection fail, clustrix's and your own, to hosts that had
nothing to do with clustrix. Appending cannot do any of that.

What appending does not do: it is not a lock, it makes no promise on NFS, and
it cannot stop some other tool from rewriting the file. It also never removes
anything, so a host whose key genuinely changed keeps its old line -- which
changes nothing in practice, because a known host offering a changed key
raises ``BadHostKeyException`` without consulting the policy at all.

The ``reject`` policy never writes to your filesystem, since verifying is not
a reason to create anything.

The automated key setup described below obeys the same policy, and the
``ssh-copy-id`` it shells out to obeys it too: the subprocess is handed
``-o StrictHostKeyChecking=yes`` under ``reject`` and ``accept-new`` under
``auto_add``, so the one place clustrix reaches for OpenSSH cannot be more
permissive than the paramiko connections beside it. Under ``auto_add`` -- and
only then -- key setup also runs ``ssh-keyscan`` and appends the result to
your ``known_hosts``. Under the default ``reject`` it does not: it fails with
the message above, which names the exact ``ssh-keyscan`` command to run, and
trusting a new host stays your decision rather than a side effect of
deploying a key. Both the scan and ``ssh-copy-id`` are pointed at the
``known_hosts`` clustrix itself reads, with ``-o UserKnownHostsFile=``:
OpenSSH resolves ``~`` from the passwd database rather than from the
environment, so without that flag the Python half of clustrix would verify
against one file while ``ssh-copy-id`` appended to another -- which differ in
a container, under ``sudo -u``, and on a login node with a relocated home.

Key deployment is also held to the credential gate. If the ``cluster_host``
came from somewhere you did not choose -- a ``./clustrix.yml`` in a cloned
repository, say -- ``ssh-copy-id`` is additionally given
``-o IdentitiesOnly=yes``, ``-o IdentityFile=<the key being deployed>`` and
``-o IdentityAgent=none``, so OpenSSH offers that one key and neither your
default identities nor anything in your ssh-agent. For a host you chose,
nothing changes.

Authentication
--------------

Clustrix authenticates with the key named by ``key_file``, with the SSH
agent and your default keys, or with a password. A password is taken from
``password`` (for the current process only; it is never saved), from the
environment variable named by ``password_env_var``, or from the credential
file managed by ``clustrix credentials`` (:doc:`cli`).

A stored credential is offered only to the host it names, or to a host that
came from a configuration source you chose. See
:ref:`untrusted-security-settings`.

.. code-block:: python

   # cluster-required: needs an SSH host
   from clustrix import configure

   configure(
       cluster_type="slurm",
       cluster_host="login.cluster.example.edu",
       username="researcher",
       key_file="~/.ssh/id_ed25519",
   )

The port is ``cluster_port`` (default 22). ``ssh_port`` is accepted only as
a deprecated alias for it and emits a ``DeprecationWarning``.

Setting up a key
----------------

If you already have a key that the cluster accepts, there is nothing to do.
Otherwise Clustrix can generate one and install it, using your password
once. Three front ends do the same thing:

* the **Set up SSH key** button in the notebook widget
  (:doc:`api/notebook_magic`);
* ``clustrix ssh-setup --host HOST --user USER [--port N] [--alias NAME]``
  (:doc:`cli`), which prompts for the password;
* :func:`clustrix.setup_ssh_keys_with_fallback` in Python.

.. code-block:: python

   # cluster-required: connects to a host and installs a key on it
   from clustrix import ClusterConfig, setup_ssh_keys_with_fallback

   config = ClusterConfig(
       cluster_type="slurm",
       cluster_host="login.cluster.example.edu",
       username="researcher",
   )
   result = setup_ssh_keys_with_fallback(config, cluster_alias="mycluster")
   print(result["success"], result["key_path"], result["error"])

What it does:

1. Looks for a key in ``~/.ssh`` that the host already accepts and stops if
   it finds one (unless ``force_refresh=True``).
2. Generates an Ed25519 key (or RSA with ``key_type="rsa"``).
3. Installs the public key in ``~/.ssh/authorized_keys`` on the host. An
   earlier copy of the same key is removed first. **Other keys, including
   keys Clustrix installed earlier, are left in place.**
4. Tests a connection with the new key.
5. With an alias, writes a ``Host <alias>`` block to ``~/.ssh/config``. A
   block Clustrix wrote earlier for the same alias is replaced in place. A
   ``Host <alias>`` block that you wrote is left unchanged, with a warning.

The host key rules above apply to every step: under the default policy, key
setup does not add an unknown host to ``known_hosts``.

The password comes from the ``password`` argument, then (in Google Colab) a
Colab secret, then the environment, then an interactive prompt.

.. note::

   Key installation needs the password of a real account, which this
   documentation build did not have. The steps above were read from
   ``clustrix/ssh_utils.py``; they were not re-run for this page.

Doing it by hand
----------------

.. code-block:: bash

   ssh-keygen -t ed25519 -f ~/.ssh/cluster_key
   ssh-copy-id -i ~/.ssh/cluster_key.pub researcher@login.cluster.example.edu

.. code-block:: python

   # cluster-required: needs an SSH host
   from clustrix import configure

   configure(
       cluster_type="slurm",
       cluster_host="login.cluster.example.edu",
       username="researcher",
       key_file="~/.ssh/cluster_key",
   )

Looking at local keys
---------------------

.. code-block:: python

   from clustrix import find_ssh_keys, list_ssh_keys

   print(len(find_ssh_keys()), "private key(s) in ~/.ssh")
   for info in list_ssh_keys():
       if info["exists"]:
           print(info["path"], info["type"], info["fingerprint"])

Sites with Kerberos or multi-factor login
-----------------------------------------

Clustrix does not perform Kerberos or interactive multi-factor
authentication. If your site requires one of them for every connection, a
key alone will not let Clustrix in.

See also
--------

* :doc:`cli` for ``clustrix ssh-setup`` and ``clustrix status``.
* :doc:`troubleshooting`.
