"""One gate for every credential release.

**The finding this module exists for is a count, not a bug.** Issue #167 was
reported as one leak and closed as seven. Each was the same shape: a place
that *obtained* a stored secret, and a decision about who may receive it that
lived somewhere else, or nowhere at all.

1. A ``.env`` holding only ``SSH_PASSWORD`` yields a credential whose host is
   ``""``, and ``credential_host in target`` made the empty string a
   substring of every hostname there is.
2. ``./clustrix.yml`` names ``cluster_host``, and the automatic search adopts
   it, so ``git clone && cd`` chose who received the cluster password.
3. ``ProfileManager.load_from_file`` built configs from parsed file content
   that ``__post_init__`` stamped ``runtime`` -- the trusted end of the scale.
4. The modern widget's Load menu globbed the working directory and handed
   what it found to a loader that called it an explicitly named file.
5. ``%%clusterfy`` globbed ``"."`` for ``./config.yml``, which the automatic
   search does not read at all, so nothing was tainted and no warning fired.
6. ``ClusterConfig.get_env_password()`` read ``os.environ[password_env_var]``
   with **no host check and no provenance check**, and ``validation.py`` fed
   the result straight into ``paramiko.connect(hostname=config.cluster_host)``.
7. The interactive prompt offered to write ``SSH_HOST=<untrusted host>`` plus
   the password the user had just typed into ``~/.clustrix/.env`` --
   manufacturing a permanently trusted binding, in the one file every remedy
   text tells the user to trust.

Seven call sites for one decision is not a bug with instances; it is a
decision with no home. This module is the home. Everything that hands out a
stored SSH secret goes through :func:`release_credential`, whose **first
positional parameter is the recipient**.

Three locks, in decreasing strength:

1. **There is nothing else public to call.** The store's entry point is
   ``FlexibleCredentialManager._ensure_credential_unchecked``; the
   module-level convenience function that used to sit beside it is gone,
   and so are the three doors round one left open --
   ``load_credentials_optional`` (a public function *and* a public method,
   thirty lines above the one that was privatised, with zero callers in the
   tree and the password in its return value), the ``sources`` attribute
   (``mgr.sources[0].get_credentials("ssh")``: privatising the method while
   leaving the objects it reads reachable closed the door and left the
   window open), and ``_stored_credential``, which anything could import.
   A developer who wants a password and greps for one finds one name, and
   it demands a target.
2. **A target that names nobody cannot be constructed.**
   :meth:`CredentialTarget.__post_init__` refuses a hostname that does not
   normalise, so route 1 -- "a credential with no host, offered to a host
   with no name" -- is not a comparison that can go wrong, it is an object
   that cannot exist. :class:`CredentialRelease` refuses to carry both a
   secret and a refusal, or neither, so "empty means configured" cannot come
   back in a new costume.
3. **The store checks its caller's module and its name.**
   ``_ensure_credential_unchecked`` raises unless the frame above it is
   :data:`STORE_CALLERS`, and ``_stored_credential`` raises unless the frame
   above *it* is :data:`CREDENTIAL_OBTAINERS`. The second half is what makes
   it a lock rather than a coincidence: a module check alone is satisfied
   **by construction** for anything reached from inside this file, so an
   outsider who imported ``_stored_credential`` was judged one frame too
   late and passed. That check is **always on**: it makes no reference to
   tests and behaves identically whether or not pytest is running, so it is
   not the test-awareness ``CLAUDE.md`` forbids. An eighth route written the
   old way raises on its first run rather than at review. Its honest limit is
   that a caller which rebinds ``__name__`` defeats it -- a caller that
   hostile already has the interpreter.

**A refusal has to stop the connection, not just the release.** Route 13:
three connection paths -- ``ConnectionManager.setup_ssh_connection``,
``ClusterFilesystem`` and ``validation.validate_ssh_key_auth``, the last of
which asked no gate at all -- logged this module's refusal and then called
``paramiko.connect()`` anyway, with ``look_for_keys`` and ``allow_agent``
left at paramiko's defaults. Paramiko then ran its *own* search of
``~/.ssh`` and the ssh-agent and authenticated. A ``./clustrix.yml`` naming
only ``cluster_host`` -- no ``key_file``, no password, no stored credential
-- was measured getting ``('victim', 'publickey')`` on all three, which is
strictly stronger than route 10.

A fourth site reaches the same identities without paramiko's help:
``ssh_utils.setup_ssh_keys`` tries *every* key in ``~/.ssh`` against
``config.cluster_host`` one ``key_filename=`` at a time, so turning the
implicit search off says nothing about it.

Those identities name no host, exactly as a bare ``SSH_PASSWORD`` does, so
they are rule 2 like everything else. The connection paths read
:attr:`CredentialRelease.local_identities`, which is
:func:`hostless_secret_refusal`'s answer decided here; the two that are
offering identities rather than asking for a credential ask that function
directly. What none of them do is decide for themselves -- four call sites
deciding separately is how there came to be four of them wrong.

**What this gate relies on being true of its input, and what it cannot
check.** ``release_credential`` decides with two facts: the hostname about
to receive the secret, and *who chose that hostname*. The first it is
handed. The second it **derives** -- :func:`derived_provenance`, from
:func:`clustrix.config.source_that_named_hostname` and
:func:`clustrix.config.get_config_source` -- and that answer is only as
good as the provenance that reached this process. A choke point cannot
recover a fact that was destroyed upstream of it.

Deriving it is not a detail. :class:`CredentialTarget` used to carry a
``provenance`` field the caller filled in, and
``CredentialTarget(hostname=<anything>, provenance="runtime", ...)``
released -- even when the honest, untrusted ``config`` was passed in the
same call, because the rule returned on the target's word before consulting
it. A gate whose caller supplies the answer is decoration. There is no
``provenance`` parameter now, anywhere: naming one is a ``TypeError``.

There is a known way to destroy it, and it is **route 8**: the profile
store persists ``strip_secret_fields(asdict(config))``, and provenance is
deliberately not a dataclass field (an attacker's file could otherwise
declare itself trusted), so it does not survive the write. A profile
refused in one process because a bundle was discovered in the working
directory is copied into ``<config dir>/profiles/profiles.yml`` by any of
the auto-persisting mutators, and the next process reads it back from a
directory the user *did* choose, computes ``user-config-dir`` entirely
legitimately, and releases. By the time this module runs, every input it
has says the release is correct. **That is fixed where the fact is lost --
in ``ProfileManager._persist`` / ``ClusterConfig.save_to_file`` -- and not
here.**

What this module does do about it is fail closed on the inputs it *can*
judge. :func:`derived_provenance` answers ``None`` when nothing accompanied
the request that records a chooser, and ``None`` is not a member of
:data:`clustrix.config.TRUSTED_CONFIG_SOURCES`, so a release nobody can
account for is refused rather than allowed. And
:func:`clustrix.config.get_config_source` answers ``working-directory`` --
the untrusted end -- for an object carrying no record, so a config that
lost its stamp (unpickled, ``setattr``-ed, restored) is distrusted rather
than trusted by default.

**Two things this deliberately does not do.**

*It does not wrap the secret in a ``Secret`` type* whose plaintext is only
reachable via ``.reveal(target)``. Paramiko wants a ``str``; every call site
would call ``.reveal()`` on the next line, so the type buys ceremony and a
new way to get it wrong. The recipient belongs on the *obtainer*, not on the
*carrier*.

*It does not make trust a registry or a plugin point.*
``clustrix.config.CONFIG_SOURCES`` is a five-element frozenset and should
stay one.

**One rule that is not here, and why.**
:class:`clustrix.auth_methods.FlexibleCredentialAuthMethod` applies an
*additional* filter after this gate has answered: it uses a stored credential
only when the credential itself names both the host and the username of the
connection. That is not a second trust decision -- it cannot release anything
this module refused, only refuse something this module allowed -- it is the
auth chain's applicability test, deciding *which* stored credential is the
one for a connection whose hostname need not be ``config.cluster_host`` at
all. The comparison it uses is :func:`hostname_matches` from this module, so
there is still exactly one definition of "same host".
"""

import logging
import os
import sys
from dataclasses import dataclass, replace
from typing import Any, Dict, Mapping, Optional, Sequence

from .config import (
    TRUSTED_CONFIG_SOURCES,
    ClusterConfig,
    get_config_source,
    normalize_hostname,
    source_that_named_hostname,
)

logger = logging.getLogger(__name__)

#: The module name ``_ensure_credential_unchecked`` will accept as a caller.
#: Written once so the guard and its error message cannot drift apart.
GATE_MODULE = __name__

#: Every branch :func:`release_credential` can answer from.
RELEASE_SOURCES = (
    "stored-credential",
    "environment",
    "config-field",
    "fallback-environment",
)

#: The variables the SSH-key fallback path scans that **name the host**.
#: ``CLUSTRIX_PASSWORD_HPC_EXAMPLE_EDU`` is the user saying which host may
#: have that password, exactly as ``SSH_HOST`` in the credential file is, so
#: these are rule 1 and are released to the host they name.
HOST_NAMED_PASSWORD_VARIABLES = (
    "CLUSTRIX_PASSWORD_{host}",
    "CLUSTER_PASSWORD_{host}",
    "{host}_PASSWORD",
)

#: The variables that name **no host**, and so are rule 2: only for a
#: ``cluster_host`` the user chose. These are route 9. ``get_cluster_password``
#: read them with no host check and no provenance check and handed what it
#: found to whatever hostname it was passed, which on the
#: ``setup_auth_with_fallback`` path is ``config.cluster_host`` -- so a
#: cloned repository's ``clustrix.yml`` collected ``$CLUSTRIX_DEFAULT_PASSWORD``
#: while ``release_credential`` was refusing the same host in the same
#: process.
HOSTLESS_PASSWORD_VARIABLES = ("CLUSTRIX_DEFAULT_PASSWORD", "CLUSTER_PASSWORD")

#: The branches a caller that names none is offered, in the order they are
#: tried. ``"config-field"`` is deliberately absent: it exists for the two
#: connection paths that used to read ``config.key_file`` and
#: ``config.password`` themselves, *before* the gate, and adding it to the
#: default would start returning ``config.password`` from the auth chain's
#: credential-store method -- a behaviour change with no security argument
#: behind it.
DEFAULT_RELEASE_SOURCES = ("stored-credential", "environment")

#: The functions *of this module* that may obtain a raw stored credential.
#: A module-name check alone is satisfied by construction for anything
#: reached from inside this file, which made ``_stored_credential`` an
#: import away from being a public store; see
#: :func:`assert_called_from`.
CREDENTIAL_OBTAINERS = ("describe_credential", "_release_stored")

#: The function of this module that may call the store. Named here rather
#: than in ``credential_manager`` so that the gate owns both halves of its
#: own rule.
STORE_CALLERS = ("_stored_credential",)

#: The module the credential store lives in.
STORE_MODULE = "clustrix.credential_manager"

#: The methods of the store that may read its configured
#: :class:`~clustrix.credential_manager.CredentialSource` objects.
#:
#: Privatising ``_ensure_credential_unchecked`` and then checking its caller
#: left one door: ``get_credential_manager()._sources[0].get_credentials("ssh")``
#: returned the password with no target named and no frame judged at all.
#: An underscore alone is exactly the standard ``_stored_credential`` was
#: found insufficient by, so the list the store points at is behind the same
#: kind of check its store call is.
SOURCE_READERS = (
    "_ensure_credential_unchecked",
    "_configured_fields",
    "list_available_providers",
    "get_credential_status",
)

#: Hosts :meth:`CredentialTarget.fixed_service` may name: services compiled
#: into clustrix, which no configuration file can move. Not a registry and
#: not a plugin point -- a name that belongs here is one written in this
#: source file.
FIXED_SERVICE_HOSTS = ("huggingface.co",)

#: The Hub URL an authenticated HuggingFace client must be pointed at.
#:
#: Beside :data:`FIXED_SERVICE_HOSTS` because it is the same fact, and the
#: two drifting apart was route 13b: ``fixed_service("huggingface.co")``
#: said "no configuration file can move this recipient" while every client
#: was built as ``HfApi(token=...)`` with no ``endpoint=``, and
#: ``huggingface_hub`` fills that in from ``$HF_ENDPOINT``. So an inherited
#: environment variable chose where the released token was actually sent --
#: the same vector the redirected-configuration-directory rule already
#: distrusts, and measured: with ``HF_ENDPOINT=https://attacker.invalid``
#: the gate released the token for ``huggingface.co`` and the client
#: carrying it was pointed at the attacker.
HUGGINGFACE_ENDPOINT = f"https://{FIXED_SERVICE_HOSTS[0]}"

#: Every secret-bearing surface in the tree, as ``(module, symbol)`` pairs.
#:
#: This is an allowlist, and it is the honest core of the enforcement test in
#: ``tests/unit/test_every_credential_goes_through_one_gate.py``: a new
#: surface fails that test until somebody writes it down in the file named
#: after the rule. It converts "forgot" into "had to say so out loud"; it
#: does not, and cannot, prove that no other surface exists.
SECRET_SURFACES = (
    (
        "clustrix.credential_manager",
        "FlexibleCredentialManager._ensure_credential_unchecked",
    ),
    ("clustrix.credential_manager", "CredentialSource.get_credentials"),
    # The sources themselves. Privatising the *method* while leaving the
    # objects it reads on a public attribute closed the door and left the
    # window open: ``mgr.sources[0].get_credentials("ssh")`` returned the
    # password with no recipient named.
    ("clustrix.credential_manager", "FlexibleCredentialManager._sources"),
    # The gate's own one-line call to the store. Importable, and until it
    # started checking its caller by *function* the store's frame check
    # passed it by construction.
    ("clustrix.credential_release", "_stored_credential"),
    ("clustrix.config", "ClusterConfig.password"),
    ("clustrix.config", "ClusterConfig.key_file"),
    ("clustrix.config", "ClusterConfig.password_env_var"),
    # Route 9, converted rather than merely written down: it takes a
    # CredentialTarget now and its environment branch is
    # ``release_credential(..., sources=("fallback-environment",))``. Listed
    # because it is still a place a secret comes out -- the interactive
    # prompt -- and because a surface nobody has written down is the one
    # that gets closed eighth.
    ("clustrix.auth_fallbacks", "get_cluster_password"),
)


def huggingface_client_kwargs() -> Dict[str, str]:
    """The keyword arguments every authenticated HuggingFace client carries.

    One helper rather than an ``endpoint=`` at each call site, for the same
    reason :func:`release_credential` is one function: there were five
    places building a client around a released token (``HfApi`` in
    ``hf_jobs``, ``staging`` and ``cli_credentials``, ``hf_hub_download``
    twice in ``staging``), each of them free to forget, and the one that
    forgets is the one that sends the token somewhere ``$HF_ENDPOINT``
    chose. ``tests/unit/test_every_credential_goes_through_one_gate.py``
    asserts that no call site builds one of those without going through
    here.
    """
    return {"endpoint": HUGGINGFACE_ENDPOINT}


def hostname_matches(target: object, credential_host: object) -> bool:
    """Whether a credential stored for ``credential_host`` is for ``target``.

    Public, and deliberately so: ``auth_methods`` needs the *same* answer to
    "is this the same host" and importing a private name across modules is
    a contradiction in terms. It carries no secret -- it compares two
    hostnames -- so nothing about the store's privacy depends on it.

    **Exact, after normalisation.** Nothing else is safe, and the three
    relaxations this replaces were each exploitable:

    * ``credential_host in target`` -- substring containment. A ``.env``
      holding only ``SSH_PASSWORD`` yields ``credential_host == ""``, and
      the empty string is a substring of every hostname there is, so the
      cluster password was offered to *any* host that was asked for. Even
      with a real value it means a credential for ``hpc.example.edu`` is
      handed to ``hpc.example.edu.attacker.test``, a name anybody can
      register under a domain they control.
    * ``target in credential_host`` -- the same thing backwards.
    * ``target.split(".")[0] == credential_host.split(".")[0]`` -- first
      label only, so ``hpc.evil.test`` collects the password stored for
      ``hpc.example.edu``.

    A hostname is the identity of the party about to receive the secret, so
    a *partial* match is not a weaker check, it is a different check that
    answers a question nobody asked. Nor is suffix-on-a-dot-boundary right
    here: ``hpc.example.edu`` has no authority over ``node1.hpc.example.edu``
    and a credential for the parent is not a credential for the child.

    The cost of being strict is a credential that is simply not offered
    when the user spelled the host differently in ``.env`` than in their
    config -- at which point the fallback chain moves on and prompts, and
    the guidance in :meth:`FlexibleCredentialAuthMethod.attempt_auth` names
    the fix. That is a safe failure. Every relaxation above is an unsafe
    success.
    """
    normalized_target = normalize_hostname(target)
    normalized_credential = normalize_hostname(credential_host)
    if not normalized_target or not normalized_credential:
        # A credential that does not say which host it is for cannot be
        # checked against one, and "unchecked" may not read as "matches".
        # This is the same class of defect as the ``{"port": "22"}`` default
        # that made every unconfigured machine look like it had SSH
        # credentials: an absent value must never satisfy a test.
        return False
    return normalized_target == normalized_credential


def derived_provenance(
    config: Optional[ClusterConfig], hostname: object
) -> Optional[str]:
    """Who chose ``hostname``, as far as this process can establish.

    **Derived, never declared.** This is the answer the gate decides on, and
    every input to it is a record the caller does not write:
    :func:`clustrix.config.source_that_named_hostname` is written by the
    loaders and keyed by the name, and :func:`clustrix.config.get_config_source`
    reads an attribute that is deliberately not a dataclass field. A caller
    that says otherwise is not consulted, because a gate that asks a question
    whose answer the caller supplies is decoration.

    The hostname record outranks the config: a name a file named earlier in
    this process stays that file's, whatever object is holding it now, and
    that is the one check that follows a connection to a host which is *not*
    ``config.cluster_host`` -- the case the auth chain drives routinely and
    the case ``get_config_source`` alone cannot see.

    ``None`` when nothing accompanied the request that records a chooser. It
    is not a member of :data:`clustrix.config.TRUSTED_CONFIG_SOURCES`, so
    every test against this value fails closed.

    **Two ways this used to answer about somebody else.**

    *The fall-through was about the wrong host.*
    :func:`clustrix.config.get_config_source` answers "where did
    ``config.cluster_host`` come from", and this function was returning it
    for **any** hostname it was asked about. So a config the user really did
    choose vouched for every host in the world that no file had happened to
    name: ``derived_provenance(trusted_cfg, "totally-unrelated.attacker.example")``
    answered ``user-config-dir``, and the hostless ``SSH_PASSWORD`` was
    released to it. The config only speaks for the host it names, so the
    fall-through now requires that the host asked about *is* that one.

    *A falsy hostname skipped the guard.* The test was ``if hostname and not
    normalize_hostname(hostname)``, so ``0``, ``None``, ``False``, ``[]``
    and ``""`` never reached it -- they went on to inherit the config's
    trust, while ``"   "`` and ``123`` were correctly refused. Whether an
    unusable hostname is falsy or truthy is not a distinction anything
    downstream can act on: neither can be recorded in the taint map and
    neither can be compared by :func:`hostname_matches`, so both are
    ``None``.
    """
    if not normalize_hostname(hostname):
        # Unrecordable is exactly the state the taint map cannot describe,
        # and "the map has nothing on it" may not read as "it is fine". The
        # same rule ``config_source_is_trusted`` applies, applied to the
        # host actually being connected to.
        return None
    named_by = source_that_named_hostname(hostname)
    if named_by:
        return named_by
    if config is None:
        return None
    if not hostname_matches(hostname, getattr(config, "cluster_host", None)):
        # The config is evidence about its own ``cluster_host`` and about
        # nothing else. Anything else is a host nothing in this process
        # accounted for, which is exactly what ``None`` means.
        return None
    return get_config_source(config)


def stored_credential_is_for_config(
    config: Optional[ClusterConfig],
    credentials: Dict[str, Any],
    *,
    hostname: Optional[str] = None,
) -> Optional[str]:
    """Why a stored SSH credential may not be used for ``config``, or ``None``.

    ``hostname`` overrides ``config.cluster_host`` for a connection to
    somewhere else, which is what the auth chain drives; the provenance is
    then :func:`derived_provenance`'s answer *about that host*, so a target
    renamed to a host some file named does not escape the record.

    ``FlexibleCredentialAuthMethod`` answers this question for a connection
    the auth chain is driving. ``ConnectionManager.setup_ssh_connection``
    reads ``ensure_credential("ssh")`` directly and used to answer it not at
    all: whatever came out of ``~/.clustrix/.env`` was applied to whatever
    ``config.cluster_host`` said, so the *file* decided who received the
    user's cluster password.

    That is a live exfiltration path rather than a theoretical one, because
    ``config.cluster_host`` is not necessarily the user's. The search of the
    standard locations includes ``./clustrix.yml``, so a repository that
    ships one names the host, and the working-directory candidates normally
    win outright (``~/.clustrix/clustrix.yml`` is not searched -- only
    ``config.yml`` is). ``git clone && cd && python -c "import clustrix..."``
    was enough to have the password sent to a host of the repository's
    choosing.

    Two rules, and the second is the one that keeps the documented setup
    working:

    1. **If the credential names a host, it must be that host.** Exactly,
       after normalisation -- :func:`hostname_matches`, the same comparison
       and the same reasoning as the auth-chain path. Substring, suffix and
       first-label matches were each exploitable there and are no better
       here.
    2. **If the credential names no host, the host must come from a source
       the user chose.** A bare ``SSH_PASSWORD=...`` in ``.env`` with the
       host in a config file is the documented, supported setup and has to
       keep working, so requiring an ``SSH_HOST`` outright is not available.
       What separates it from the attack is not the credential at all --
       both look identical -- it is *who chose the hostname*. A host from
       ``~/.clustrix/config.yml``, from ``load_config(path)``, or from
       Python is the user's. A host from ``./clustrix.yml`` is whatever
       directory the process is in. See
       :func:`clustrix.config.config_source_is_trusted`.

    **The refusal names only remedies that work.** It used to offer
    ``configure(cluster_host=...)``, and that is a lie: an untrusted source
    taints the *hostname* for the life of the process
    (``clustrix.config._HOSTS_NAMED_BY_UNTRUSTED_SOURCES``), so handing the
    same string back through ``configure`` or ``load_config`` leaves it
    refused. It has to: the notebook widget's Apply button *is*
    ``configure(cluster_host=<the file's host>, ...)``, so a rule that let an
    explicit ``configure`` clear the taint would reopen the laundering route
    round two closed, and nothing distinguishes the two calls. The two things
    that do work are ``SSH_HOST`` in the credential file -- authorisation
    that no round trip can manufacture -- and removing the offending file and
    starting again, since the record is per-process.

    Returns the reason it may not be used, so the caller can say so; ``None``
    means it may.
    """
    if hostname is None:
        if config is None:
            raise ValueError(
                "stored_credential_is_for_config needs a config or an "
                "explicit hostname: there is no recipient to decide about."
            )
        hostname = config.cluster_host
    host = hostname
    credential_host = credentials.get("host", "")
    if normalize_hostname(credential_host):
        if hostname_matches(host, credential_host):
            return None
        return (
            f"the stored credential is for {credential_host!r} and this "
            f"connection is to {host!r}"
        )

    provenance = derived_provenance(config, host)
    if provenance in TRUSTED_CONFIG_SOURCES:
        return None

    origin = (
        f"came from {provenance}"
        if provenance
        else "arrived with no configuration recording who chose it"
    )
    return (
        f"the stored credential names no host, and cluster_host="
        f"{host!r} {origin} -- "
        f"a file chosen by where the process runs or by an inherited "
        f"environment variable, not by you. That is settled for the life of "
        f"this process: passing "
        f"the same hostname to configure(cluster_host=...) or "
        f"load_config(path) does not clear it, because a value handed back "
        f"through a function call is not evidence that anyone chose it. "
        f"Either set SSH_HOST={host!r} in the credential file, "
        f"which is you naming the host that may receive the secret, or move "
        f"the host into the clustrix configuration directory (config.yml), "
        f"remove the file it came from, and start a new process"
    )


@dataclass(frozen=True)
class CredentialTarget:
    """The party about to receive a secret, and who chose it.

    Frozen, because a target that can be edited after the decision was made
    about it is not a decision. Constructing one is the act of naming a
    recipient, and it fails loudly when there is nobody to name: an
    unnormalisable hostname is exactly the state
    :data:`clustrix.config._HOSTS_NAMED_BY_UNTRUSTED_SOURCES` cannot key on
    and :func:`hostname_matches` can never satisfy, so it must not be
    possible to ask for a release to one.
    """

    #: Where the secret would go. Non-empty and normalisable, guaranteed.
    hostname: str
    #: Who it would authenticate as. May be ``""`` -- not every provider has
    #: one, and the ``.env`` that holds only ``SSH_PASSWORD`` names none.
    username: str
    #: How to name this target in a refusal, e.g.
    #: ``"cluster_host from ./clustrix.yml"``.
    described_as: str

    #: **There is deliberately no ``provenance`` field.** There was one, and
    #: it was the gate's worst defect: ``CredentialTarget(hostname=<any>,
    #: provenance="runtime", ...)`` released, because the rule that needs
    #: provenance read it off the target *before* consulting the config the
    #: honest caller had also passed. A caller that can assert its own
    #: provenance turns the gate into a question whose answer the caller
    #: supplies. Provenance is now :func:`derived_provenance`'s answer,
    #: computed inside the gate from records the caller does not write, and
    #: naming the keyword here is a ``TypeError`` rather than a release.

    def __post_init__(self) -> None:
        if not normalize_hostname(self.hostname):
            raise ValueError(
                f"A credential target must name the host that would receive "
                f"the secret; {self.hostname!r} does not normalise to one. "
                f"This is the check that closes the route where a credential "
                f"naming no host matched every host there is -- see "
                f"clustrix.credential_release."
            )
        if not isinstance(self.username, str):
            raise ValueError(
                f"username must be a string (possibly empty), not "
                f"{type(self.username).__name__}."
            )

    @classmethod
    def for_config(
        cls,
        config: ClusterConfig,
        *,
        hostname: Optional[str] = None,
        username: Optional[str] = None,
    ) -> "CredentialTarget":
        """The recipient of a connection driven by ``config``.

        ``hostname`` and ``username`` override the config's own for a
        connection to somewhere else -- the auth chain passes connection
        parameters that need not be ``config.cluster_host``. Nothing about
        provenance is carried on the object; the gate derives it from this
        config and from the process record for the host actually being
        connected to, when it decides. The source only appears here in
        ``described_as``, which is prose for a refusal message.
        """
        host = hostname if hostname is not None else config.cluster_host
        user = username if username is not None else config.username
        source = derived_provenance(config, host) or get_config_source(config)
        return cls(
            # ``str(host)`` so that a value ``__post_init__`` can then judge
            # is what reaches it -- PyYAML hands back an *int* for an
            # unquoted ``0x7f000001``. Not for ``None``, though: ``str(None)``
            # is ``"None"``, which normalises perfectly well, so a config
            # naming no host at all produced a target naming the literal host
            # ``None`` and the ValueError four call sites catch to mean
            # "there is nobody to release a credential to" was never raised.
            hostname=(
                "" if host is None else (host if isinstance(host, str) else str(host))
            ),
            username=user or "",
            described_as=f"cluster_host={host!r} (from {source})",
        )

    @classmethod
    def fixed_service(cls, hostname: str, *, why: str) -> "CredentialTarget":
        """A recipient compiled into clustrix rather than read from anywhere.

        ``huggingface.co`` is the only kind: no configuration file can move
        it, so nothing untrusted can have chosen it. The name is checked
        against :data:`FIXED_SERVICE_HOSTS` rather than taken on trust,
        because "compiled in" is a claim about *which* host, and a
        constructor that accepted any hostname while asserting that one
        would be the declared-provenance defect wearing a different hat.

        **What this does not by itself establish**, and route 13b is the
        proof: naming the recipient here settles who the gate *decided*
        about, not where the token is *sent*. Every client was built as
        ``HfApi(token=...)`` with no ``endpoint=``, and ``huggingface_hub``
        reads ``$HF_ENDPOINT`` when none is given -- so with
        ``HF_ENDPOINT=https://attacker.invalid`` the gate released the token
        for ``huggingface.co`` and the object carrying it pointed at the
        attacker. The recipient is only fixed if the client is pinned too,
        which is :func:`huggingface_client_kwargs`.
        """
        if normalize_hostname(hostname) not in FIXED_SERVICE_HOSTS:
            raise ValueError(
                f"{hostname!r} is not a service compiled into clustrix. "
                f"fixed_service is for {sorted(FIXED_SERVICE_HOSTS)} and "
                f"nothing else; a recipient read from configuration is "
                f"CredentialTarget.for_config(config), which derives who "
                f"chose it."
            )
        return cls(
            hostname=hostname,
            username="",
            described_as=why,
        )


@dataclass(frozen=True)
class CredentialRelease:
    """Either a secret and who it is for, or the reason there is none.

    Never both, and never neither. "Neither" is the ``{"port": "22"}``
    defect in a new costume -- an object that is not a secret and not a
    reason, which every caller then reads as whichever suits it.
    """

    target: CredentialTarget
    #: ``"stored-credential"``, ``"environment"``, ``"config-password"`` or
    #: ``"config-key"``. ``None`` on a refusal.
    method: Optional[str] = None
    password: Optional[str] = None
    key_path: Optional[str] = None
    #: The secret for a token-shaped provider (HuggingFace). A third field
    #: rather than reusing ``password`` because a caller that puts a token in
    #: a ``password=`` keyword has already made the mistake this module is
    #: about.
    token: Optional[str] = None
    #: Why nothing was released, in the user's terms, naming a remedy that
    #: works.
    refusal: Optional[str] = None
    #: Whether paramiko's own credential discovery -- ``look_for_keys`` and
    #: ``allow_agent`` -- may run for this target. **Route 13.**
    #:
    #: A refusal that still authenticates is not a refusal. Every connection
    #: path logged this module's refusal and then called ``connect()``
    #: anyway with paramiko's defaults, so paramiko searched ``~/.ssh`` and
    #: the running agent and authenticated with the victim's own key. A
    #: ``./clustrix.yml`` naming *only* ``cluster_host`` -- no ``key_file``,
    #: no password, no stored credential -- got ``('victim', 'publickey')``
    #: on the wire, which is strictly stronger than route 10 because it
    #: needs the attacker to name nothing at all.
    #:
    #: ``~/.ssh/id_rsa`` and an agent identity are secrets that name no
    #: host, so they are rule 2 of :func:`stored_credential_is_for_config`
    #: and no different from a bare ``SSH_PASSWORD``: usable for a
    #: ``cluster_host`` the user chose, and for nobody else. It is a field
    #: of the release rather than a flag each call site sets because that
    #: is the whole point of a choke point -- three call sites each deciding
    #: for themselves is how there came to be three of them wrong.
    #:
    #: ``False`` by default, so a ``CredentialRelease`` built anywhere but
    #: :func:`release_credential` cannot open the search by omission.
    local_identities: bool = False

    def __post_init__(self) -> None:
        has_secret = bool(self.password) or bool(self.key_path) or bool(self.token)
        if has_secret and self.refusal is not None:
            raise ValueError(
                "A credential release carries a secret or a refusal, never "
                "both: a caller reading only one of the two fields would "
                "silently use a credential this gate refused."
            )
        if not has_secret and self.refusal is None:
            raise ValueError(
                "A credential release must carry a secret or a refusal. "
                "Neither is the 'empty means configured' defect: the caller "
                "cannot tell 'nothing is set up' from 'this was denied'."
            )
        if has_secret and not self.method:
            raise ValueError(
                "A released credential must name the method that produced "
                "it, so that a log line can say where a secret came from."
            )

    def __bool__(self) -> bool:
        return self.refusal is None


def _stored_credential(provider: str) -> Optional[Dict[str, str]]:
    """The raw stored credential for ``provider``. Gate-internal.

    The one call to the private store, so that it is a single greppable
    line inside this module -- and a door of its own until it started
    checking. ``from clustrix.credential_release import _stored_credential``
    is a public import in every way that matters, and the store's frame
    check passed it *by construction*: the frame it judges is this
    function's, which is in this module whoever called it. So this asks the
    same question one frame further down, about the function calling it.
    """
    assert_called_from(GATE_MODULE, CREDENTIAL_OBTAINERS)

    from .credential_manager import get_credential_manager

    return get_credential_manager()._ensure_credential_unchecked(provider)


@dataclass(frozen=True)
class CredentialDescription:
    """What is stored for a provider, with none of what is stored.

    Every field is a boolean or a value that is not a secret: the host and
    username a credential names are the *recipient*, which the user wrote
    down themselves, and a key path is a path. The password and the private
    key never appear here, and ``tests`` assert that the sentinel appears in
    neither the fields nor the ``repr`` -- a description that leaks is worse
    than no description, because it is printed by a status command.
    """

    provider: str
    available: bool = False
    host: str = ""
    username: str = ""
    has_password: bool = False
    has_token: bool = False
    key_path: str = ""
    port: str = ""

    @property
    def has_key_path(self) -> bool:
        return bool(self.key_path)


def describe_credential(provider: str) -> CredentialDescription:
    """Non-secret facts about the stored credential for ``provider``.

    This is what a status command wants, and it is the reason privatising
    the store does not make ``clustrix credentials status`` impossible: the
    question "is something configured, and for whom" never needed the
    secret, and answering it without one means the status path is not a
    release at all and needs no target.
    """
    credentials = _stored_credential(provider)
    if not credentials:
        return CredentialDescription(provider=provider)
    return CredentialDescription(
        provider=provider,
        available=True,
        host=credentials.get("host", "") or "",
        username=credentials.get("username", "") or "",
        has_password=bool(credentials.get("password")),
        has_token=bool(credentials.get("token")),
        key_path=credentials.get("private_key_path", "") or "",
        port=str(credentials.get("port", "") or ""),
    )


def describe_stored_credential(provider: str) -> Dict[str, str]:
    """The non-secret identifying fields of the stored credential.

    A two-key mapping rather than the whole credential, so that a caller
    asking "which host is this credential for" cannot accidentally end up
    holding the password as well.
    """
    described = describe_credential(provider)
    return {"host": described.host, "username": described.username}


def _release_stored(
    target: CredentialTarget,
    provider: str,
    config: Optional[ClusterConfig],
) -> Optional[CredentialRelease]:
    """The stored-credential branch, or ``None`` if it has nothing to say."""
    credentials = _stored_credential(provider)
    if not credentials:
        return None

    if provider == "ssh":
        refusal = _stored_ssh_is_for_target(target, credentials, config)
        if refusal:
            return CredentialRelease(target=target, refusal=refusal)

    password = credentials.get("password")
    if password:
        return CredentialRelease(
            target=target, method="stored-credential", password=password
        )
    key_path = credentials.get("private_key_path")
    if key_path:
        return CredentialRelease(
            target=target, method="stored-credential", key_path=key_path
        )
    token = credentials.get("token")
    if token:
        return CredentialRelease(target=target, method="stored-credential", token=token)
    return None


def _stored_ssh_is_for_target(
    target: CredentialTarget,
    credentials: Mapping[str, Any],
    config: Optional[ClusterConfig],
) -> Optional[str]:
    """Why a stored SSH credential may not go to ``target``, or ``None``.

    The two rules of :func:`stored_credential_is_for_config`, asked about
    the host actually being connected to. It is one call rather than a
    second copy of the rules, and that is the point: the version this
    replaces read ``target.provenance`` first and *returned* on it, so a
    caller that constructed its own target with ``provenance="runtime"``
    was released to -- even when the honest, untrusted ``config`` was passed
    in the same call. Provenance is derived here, from
    :func:`derived_provenance`, and there is nothing on the target to
    consult instead.
    """
    return stored_credential_is_for_config(
        config, dict(credentials), hostname=target.hostname
    )


def _release_environment(
    target: CredentialTarget, config: Optional[ClusterConfig]
) -> Optional[CredentialRelease]:
    """The ``password_env_var`` branch -- route 6, with a gate on it.

    ``ClusterConfig.get_env_password()`` used to be this, with no host check
    and no provenance check, and ``validation.py`` handed its result to
    ``paramiko.connect(hostname=config.cluster_host)``. With a
    working-directory config the *whole* method was the attacker's: the file
    names ``password_env_var`` as well as ``cluster_host``, so an ungated
    version reads an environment variable of the repository's choosing and
    sends it to a host of the repository's choosing.
    """
    if config is None or not config.use_env_password or not config.password_env_var:
        return None

    # The variable names no host, so this is rule 2 of
    # ``stored_credential_is_for_config``: the host has to come from
    # somewhere the user chose. Reused rather than restated -- a second
    # implementation of "may this credential go to this host" is a second
    # thing to get wrong.
    refusal = stored_credential_is_for_config(config, {}, hostname=target.hostname)
    if refusal:
        return CredentialRelease(
            target=target,
            refusal=(
                f"${config.password_env_var} was not offered: {refusal}. "
                f"The environment variable names no host, so it is only used "
                f"for a cluster_host you chose."
            ),
        )

    # And it belongs to *this* config's host, so a connection to some other
    # host does not get it either.
    if not hostname_matches(target.hostname, config.cluster_host):
        return CredentialRelease(
            target=target,
            refusal=(
                f"${config.password_env_var} is configured for "
                f"{config.cluster_host!r} and this connection is to "
                f"{target.hostname!r}. Set cluster_host to the host you are "
                f"connecting to, or supply the password for this host "
                f"another way."
            ),
        )

    password = os.environ.get(config.password_env_var)
    if not password:
        return CredentialRelease(
            target=target,
            refusal=(
                f"environment variable ${config.password_env_var} is not "
                f"set. Set it with: export "
                f"{config.password_env_var}='your_password'"
            ),
        )
    return CredentialRelease(target=target, method="environment", password=password)


def _release_config_field(
    target: CredentialTarget, config: Optional[ClusterConfig]
) -> Optional[CredentialRelease]:
    """``config.key_file`` and ``config.password`` -- route 10, with a gate.

    These are fields of a ``ClusterConfig``, and a ``ClusterConfig`` is
    routinely built out of a file clustrix *found*: the automatic search
    reads ``./clustrix.yml``, and ``key_file`` is an ordinary declared
    field. So a cloned repository shipping

        cluster_host: attacker.example
        key_file: ~/.ssh/id_rsa

    had the victim's private key offered to a host the repository chose --
    without the gate being consulted at all, because both connection paths
    tested these two fields *before* asking it. The order was the bug and
    the ordering is the fix: the fields are a branch here now, checked by
    the same rule as everything else.

    Rule 2 is the applicable one. Neither field names a host, so the host
    has to come from a source the user chose.
    """
    if config is None:
        return None
    if not config.key_file and not config.password:
        return None

    refusal = stored_credential_is_for_config(config, {}, hostname=target.hostname)
    if refusal:
        field = "key_file" if config.key_file else "password"
        return CredentialRelease(
            target=target,
            refusal=(
                f"config.{field} was not offered: {refusal}. The field names "
                f"no host, so it is only used for a cluster_host you chose."
            ),
        )

    if config.key_file:
        return CredentialRelease(
            target=target, method="config-key", key_path=config.key_file
        )
    return CredentialRelease(
        target=target, method="config-password", password=config.password
    )


def hostless_secret_refusal(
    target: CredentialTarget, config: Optional[ClusterConfig]
) -> Optional[str]:
    """Why a secret that names no host may not go to ``target``. Rule 2.

    The public name for the one rule, so that a caller holding a secret this
    module does not store -- a Colab userdata entry, say -- can ask the same
    question rather than inventing a second answer to it.

    **A key in ``~/.ssh`` and an identity in the ssh-agent are exactly that
    kind of secret**, which is why route 13 is this rule and not a new one.
    :attr:`CredentialRelease.local_identities` is this answer as a boolean,
    carried on every release so the connection paths cannot forget it;
    ``validation.validate_ssh_key_auth`` and ``ssh_utils.setup_ssh_keys``
    ask here directly, because neither is asking for a credential -- they
    are about to offer identities the user already has -- and both were
    doing it with no provenance check anywhere in the path.
    """
    return stored_credential_is_for_config(config, {}, hostname=target.hostname)


def environment_password_variable(template: str, hostname: object) -> str:
    """The variable name ``template`` produces for ``hostname``.

    One definition, because the gate and any caller listing what a user
    might set must agree on the spelling exactly.
    """
    return template.format(host=normalize_hostname(hostname).upper().replace(".", "_"))


def _release_fallback_environment(
    target: CredentialTarget, config: Optional[ClusterConfig]
) -> Optional[CredentialRelease]:
    """Route 9: the variables the SSH-key fallback path scans.

    ``get_cluster_password`` read all five with no host check and no
    provenance check, and ``setup_auth_with_fallback`` passed it
    ``config.cluster_host``. The reviewer's attacker server logged
    ``$CLUSTRIX_DEFAULT_PASSWORD`` while ``release_credential`` was refusing
    the same host in the same process, which is the definition of an
    unconverted call site: it was never an argument that this secret was
    different, only that nobody had routed it here.

    The two kinds are not the same rule. A variable that names the host is
    the user authorising that host, exactly as ``SSH_HOST`` is, and needs no
    provenance. A variable that names none is rule 2.
    """
    for template in HOST_NAMED_PASSWORD_VARIABLES:
        name = environment_password_variable(template, target.hostname)
        password = os.environ.get(name)
        if password:
            return CredentialRelease(
                target=target, method="fallback-environment", password=password
            )

    for name in HOSTLESS_PASSWORD_VARIABLES:
        if not os.environ.get(name):
            continue
        refusal = hostless_secret_refusal(target, config)
        if refusal:
            return CredentialRelease(
                target=target,
                refusal=(
                    f"${name} was not offered: {refusal}. The variable names "
                    f"no host, so it is only used for a cluster_host you "
                    f"chose. To name this host, set "
                    f"{environment_password_variable(HOST_NAMED_PASSWORD_VARIABLES[0], target.hostname)}"
                    f" instead."
                ),
            )
        return CredentialRelease(
            target=target,
            method="fallback-environment",
            password=os.environ[name],
        )
    return None


def release_credential(
    target: CredentialTarget,
    *,
    provider: str = "ssh",
    config: Optional[ClusterConfig] = None,
    sources: Sequence[str] = DEFAULT_RELEASE_SOURCES,
) -> CredentialRelease:
    """The only place in clustrix where a stored secret is handed out.

    ``target`` is the recipient and comes first, because that is the whole
    point: a caller cannot ask for a secret without saying who is about to
    receive it and who chose them. ``config`` supplies the
    ``key_file`` / ``password`` / ``password_env_var`` branches and must be
    the config ``target`` was built from.

    Three branches:

    1. ``"stored-credential"`` -- ``~/.clustrix/.env``, the environment, or
       GitHub Actions, gated by :func:`_stored_ssh_is_for_target`.
    2. ``"environment"`` -- ``config.password_env_var``, gated the same way.
    3. ``"config-field"`` -- ``config.key_file`` and ``config.password``,
       gated the same way, and **not** in
       :data:`DEFAULT_RELEASE_SOURCES`.
    4. ``"fallback-environment"`` -- the variables the SSH-key fallback path
       scans (route 9), also opt-in.

    **Deviation 2, and why it was wrong.** These last two used to be
    deliberately outside the gate, on the argument that they are fields of
    the caller's own configuration object which no file it did not name can
    set. That premise is false. ``save_to_file`` omitting them is about
    *writing*; nothing stops a file being *read* into them, ``key_file`` is
    an ordinary declared field, and the automatic search reads
    ``./clustrix.yml``. Worse, both connection paths tested
    ``config.key_file`` and ``config.password`` **before** asking the gate,
    so a cloned repository naming ``key_file`` in a working-directory
    ``clustrix.yml`` bypassed it entirely and had the victim's private key
    offered to a host the repository chose. A deviation justified by a
    false premise is worse than no deviation. What survives of it is only
    the *default*: routing them through here does not mean the auth chain
    should start answering with ``config.password``, so ``"config-field"``
    is opt-in, and the two connection paths opt in by naming it first --
    which is also how they keep the precedence they always had.

    ``sources`` narrows which branches may answer and fixes the order they
    are tried in. Narrowing is all it can do: every branch applies the same
    host and provenance checks, so a caller passing a shorter tuple can
    only be offered *less*. A member that is not in
    :data:`RELEASE_SOURCES` raises rather than being ignored -- a typo
    that silently widened nothing would be indistinguishable from one that
    silently narrowed everything. The auth chain uses it to keep one method
    per source, which is what makes its per-method messages
    ("$SSH_PASSWORD was not offered: ...") true.

    Returns a :class:`CredentialRelease`, which is a secret or a reason and
    never both or neither.
    """
    for source in sources:
        if source not in RELEASE_SOURCES:
            raise ValueError(
                f"Unknown release source: {source!r}. "
                f"Known sources are {list(RELEASE_SOURCES)}."
            )

    branches = {
        "stored-credential": lambda: _release_stored(target, provider, config),
        "environment": lambda: _release_environment(target, config),
        "config-field": lambda: _release_config_field(target, config),
        "fallback-environment": lambda: _release_fallback_environment(target, config),
    }
    # Decided once, here, and carried on every answer -- including the
    # refusals. Route 13 was that a refusal left paramiko's own key and
    # agent search running, so the connection authenticated anyway with
    # ``~/.ssh/id_rsa``; see ``CredentialRelease.local_identities``.
    local_identities = hostless_secret_refusal(target, config) is None

    for source in sources:
        released = branches[source]()
        if released is not None:
            return replace(released, local_identities=local_identities)

    return CredentialRelease(
        target=target,
        local_identities=local_identities,
        refusal=(
            f"no {provider} credential is available for {target.described_as}. "
            f"Add one with 'clustrix credentials setup' or by editing "
            f"~/.clustrix/.env. A stored credential is only offered to the "
            f"host it names, so SSH_HOST must match {target.hostname!r} "
            f"exactly, or the host must come from a configuration source you "
            f"chose."
        ),
    )


def assert_called_from(module: str, allowed: Sequence[str] = ()) -> None:
    """Raise unless the frame two up is one of ``allowed`` in ``module``.

    Lock 3. **Always on**, in production, with no reference to tests and no
    different behaviour under pytest -- it is a fact about which module may
    obtain a secret, not test-awareness, so it does not violate the mocking
    policy's rule 4.

    ``module`` is a parameter rather than a constant because two different
    surfaces need the same rule: the gate's own ``_stored_credential``, and
    the store's ``_sources``. One implementation, so the two cannot drift
    into disagreeing about what "called from inside" means.

    ``sys._getframe`` rather than ``inspect.stack()``: the latter reads
    source files off disk for every frame, and this runs on the connection
    path. Frame 0 is this function, frame 1 is the function that wants its
    own caller judged, and frame 2 is that caller -- the one being judged.

    ``allowed`` names the functions *of this module* that may make the call,
    and it is not decoration. A module-name check alone passes **by
    construction** for anything reached from inside this file: an outsider
    who imports ``_stored_credential`` and calls it is judged one frame too
    late, at ``_ensure_credential_unchecked``, where the caller is
    ``_stored_credential`` and the module is therefore this one. What
    distinguishes the gate calling its own helper from somebody importing
    that helper is *which function* is calling, so that is what is checked.
    Its honest limit is unchanged: a caller that rebinds ``__name__``, or
    that runs code compiled into a frame of its choosing, defeats it -- and
    a caller that hostile already has the interpreter.
    """
    try:
        frame = sys._getframe(2)
    except ValueError:  # pragma: no cover - not enough frames to judge
        frame = None
    caller = frame.f_globals.get("__name__") if frame is not None else None
    function = frame.f_code.co_name if frame is not None else None
    if caller != module or (allowed and function not in allowed):
        raise RuntimeError(
            "Stored credentials are released only through "
            "clustrix.credential_release.release_credential(target), which "
            "requires the host that is about to receive them. "
            f"{caller!r}.{function} called the store directly. See issue "
            "#167 and the module docstring of clustrix.credential_release."
        )
