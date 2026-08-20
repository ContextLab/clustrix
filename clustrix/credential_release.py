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
   ``FlexibleCredentialManager._ensure_credential_unchecked``, and the
   module-level convenience function that used to sit beside it is gone. A
   developer who wants a password and greps for one finds one name, and it
   demands a target.
2. **A target that names nobody cannot be constructed.**
   :meth:`CredentialTarget.__post_init__` refuses a hostname that does not
   normalise, so route 1 -- "a credential with no host, offered to a host
   with no name" -- is not a comparison that can go wrong, it is an object
   that cannot exist. :class:`CredentialRelease` refuses to carry both a
   secret and a refusal, or neither, so "empty means configured" cannot come
   back in a new costume.
3. **The store checks its caller's module.**
   ``_ensure_credential_unchecked`` raises unless the frame above it belongs
   to this module. That check is **always on**: it makes no reference to
   tests and behaves identically whether or not pytest is running, so it is
   not the test-awareness ``CLAUDE.md`` forbids. An eighth route written the
   old way raises on its first run rather than at review. Its honest limit is
   that a caller which rebinds ``__name__`` defeats it -- a caller that
   hostile already has the interpreter.

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
all. The comparison it uses is :func:`_hostname_matches` from this module, so
there is still exactly one definition of "same host".
"""

import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .config import (
    CONFIG_SOURCES,
    CONFIG_SOURCE_RUNTIME,
    TRUSTED_CONFIG_SOURCES,
    ClusterConfig,
    config_source_is_trusted,
    get_config_source,
    normalize_hostname,
)

logger = logging.getLogger(__name__)

#: The module name ``_ensure_credential_unchecked`` will accept as a caller.
#: Written once so the guard and its error message cannot drift apart.
GATE_MODULE = __name__

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
    ("clustrix.config", "ClusterConfig.password"),
    ("clustrix.config", "ClusterConfig.key_file"),
    ("clustrix.config", "ClusterConfig.password_env_var"),
)


def _hostname_matches(target: object, credential_host: object) -> bool:
    """Whether a credential stored for ``credential_host`` is for ``target``.

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


def stored_credential_is_for_config(
    config: ClusterConfig, credentials: Dict[str, Any]
) -> Optional[str]:
    """Why a stored SSH credential may not be used for ``config``, or ``None``.

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
       after normalisation -- :func:`_hostname_matches`, the same comparison
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
    credential_host = credentials.get("host", "")
    if normalize_hostname(credential_host):
        if _hostname_matches(config.cluster_host, credential_host):
            return None
        return (
            f"the stored credential is for {credential_host!r} and this "
            f"connection is to {config.cluster_host!r}"
        )

    if config_source_is_trusted(config):
        return None

    return (
        f"the stored credential names no host, and cluster_host="
        f"{config.cluster_host!r} came from {get_config_source(config)} -- "
        f"a file chosen by where the process runs or by an inherited "
        f"environment variable, not by you. That is settled for the life of "
        f"this process: passing "
        f"the same hostname to configure(cluster_host=...) or "
        f"load_config(path) does not clear it, because a value handed back "
        f"through a function call is not evidence that anyone chose it. "
        f"Either set SSH_HOST={config.cluster_host!r} in the credential file, "
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
    and :func:`_hostname_matches` can never satisfy, so it must not be
    possible to ask for a release to one.
    """

    #: Where the secret would go. Non-empty and normalisable, guaranteed.
    hostname: str
    #: Who it would authenticate as. May be ``""`` -- not every provider has
    #: one, and the ``.env`` that holds only ``SSH_PASSWORD`` names none.
    username: str
    #: A ``clustrix.config.CONFIG_SOURCE_*`` value: who chose ``hostname``.
    provenance: str
    #: How to name this target in a refusal, e.g.
    #: ``"cluster_host from ./clustrix.yml"``.
    described_as: str

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
        if self.provenance not in CONFIG_SOURCES:
            raise ValueError(
                f"Unknown provenance: {self.provenance!r}. "
                f"Known sources are {sorted(CONFIG_SOURCES)}."
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
        parameters that need not be ``config.cluster_host``. The provenance
        is the config's either way, because provenance is a fact about the
        *hostname* rather than about the object
        (:func:`clustrix.config.get_config_source`), and an override that
        renamed the host without renaming where it came from would be the
        laundering route with extra steps.
        """
        host = hostname if hostname is not None else config.cluster_host
        user = username if username is not None else config.username
        source = get_config_source(config)
        return cls(
            hostname=host if isinstance(host, str) else str(host),
            username=user or "",
            provenance=source,
            described_as=f"cluster_host={host!r} (from {source})",
        )

    @classmethod
    def fixed_service(cls, hostname: str, *, why: str) -> "CredentialTarget":
        """A recipient compiled into clustrix rather than read from anywhere.

        ``huggingface.co`` is the only kind: no configuration file can move
        it, so nothing untrusted can have chosen it, and its provenance is
        ``runtime`` for the same reason a ``ClusterConfig(...)`` typed in a
        script is.
        """
        return cls(
            hostname=hostname,
            username="",
            provenance=CONFIG_SOURCE_RUNTIME,
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
    #: Why nothing was released, in the user's terms, naming a remedy that
    #: works.
    refusal: Optional[str] = None

    def __post_init__(self) -> None:
        has_secret = bool(self.password) or bool(self.key_path)
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

    Separated only so that the one call to the private store is a single,
    greppable line inside this module.
    """
    from .credential_manager import get_credential_manager

    return get_credential_manager().ensure_credential(provider)


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
    return None


def _stored_ssh_is_for_target(
    target: CredentialTarget,
    credentials: Mapping[str, Any],
    config: Optional[ClusterConfig],
) -> Optional[str]:
    """Why a stored SSH credential may not go to ``target``, or ``None``.

    The two rules of :func:`stored_credential_is_for_config`, expressed
    against the target rather than against a config, so that a caller
    connecting somewhere other than ``config.cluster_host`` gets the check
    for the host it is actually connecting to. When a config is available
    the wording of rule 2's refusal comes from that function unchanged --
    there is one refusal text, not a second one that drifts.
    """
    credential_host = credentials.get("host", "")
    if normalize_hostname(credential_host):
        if _hostname_matches(target.hostname, credential_host):
            return None
        return (
            f"the stored credential is for {credential_host!r} and this "
            f"connection is to {target.hostname!r}"
        )

    if target.provenance in TRUSTED_CONFIG_SOURCES:
        return None
    if config is not None:
        return stored_credential_is_for_config(config, dict(credentials))
    return (
        f"the stored credential names no host, and {target.described_as} "
        f"was not chosen by you. Set SSH_HOST={target.hostname!r} in the "
        f"credential file, which is you naming the host that may receive "
        f"the secret."
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
    refusal = stored_credential_is_for_config(config, {})
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
    if not _hostname_matches(target.hostname, config.cluster_host):
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


def release_credential(
    target: CredentialTarget,
    *,
    provider: str = "ssh",
    config: Optional[ClusterConfig] = None,
) -> CredentialRelease:
    """The only place in clustrix where a stored secret is handed out.

    ``target`` is the recipient and comes first, because that is the whole
    point: a caller cannot ask for a secret without saying who is about to
    receive it and who chose them. ``config`` supplies the
    ``key_file`` / ``password`` / ``password_env_var`` branches and must be
    the config ``target`` was built from.

    Branch order, highest first, and it is the order
    ``ConnectionManager.setup_ssh_connection`` already used:

    1. ``config.key_file`` -- an explicit key path in the configuration.
    2. ``config.password`` -- an explicit password in the configuration.
    3. the stored credential from ``~/.clustrix/.env``, the environment, or
       GitHub Actions, gated by :func:`_stored_ssh_is_for_target`.
    4. ``config.password_env_var``, gated the same way.

    The first three are the user's own configuration object, so they carry
    their own authorisation: a ``ClusterConfig`` with a ``password`` field
    set was either typed in Python or read from a file the user named --
    and a file the user did *not* name cannot set it either, because
    ``save_to_file`` omits secret-bearing fields and the loaders refuse
    unknown keys rather than inventing them. They are still routed through
    here so that one function can answer "where would the secret for this
    host come from", which is what ``describe_credential`` reports.

    Returns a :class:`CredentialRelease`, which is a secret or a reason and
    never both or neither.
    """
    if config is not None:
        if config.key_file:
            return CredentialRelease(
                target=target, method="config-key", key_path=config.key_file
            )
        if config.password:
            return CredentialRelease(
                target=target, method="config-password", password=config.password
            )

    released = _release_stored(target, provider, config)
    if released is not None:
        return released

    released = _release_environment(target, config)
    if released is not None:
        return released

    return CredentialRelease(
        target=target,
        refusal=(
            f"no {provider} credential is available for {target.described_as}. "
            f"Add one with 'clustrix credentials setup' or by editing "
            f"~/.clustrix/.env. A stored credential is only offered to the "
            f"host it names, so SSH_HOST must match {target.hostname!r} "
            f"exactly, or the host must come from a configuration source you "
            f"chose."
        ),
    )


def _caller_module() -> Optional[str]:
    """The ``__name__`` of the frame that called our caller.

    ``sys._getframe`` rather than ``inspect.stack()``: the latter reads
    source files off disk for every frame, and this runs on the connection
    path.
    """
    try:
        return sys._getframe(2).f_globals.get("__name__")
    except ValueError:  # pragma: no cover - no such frame
        return None


def assert_called_from_the_gate() -> None:
    """Raise unless the caller's caller is this module.

    Lock 3. **Always on**, in production, with no reference to tests and no
    different behaviour under pytest -- it is a fact about which module may
    obtain a secret, not test-awareness, so it does not violate the mocking
    policy's rule 4.
    """
    caller = _caller_module()
    if caller != GATE_MODULE:
        raise RuntimeError(
            "Stored credentials are released only through "
            "clustrix.credential_release.release_credential(target), which "
            "requires the host that is about to receive them. "
            f"{caller!r} called the store directly. See issue #167 and the "
            "module docstring of clustrix.credential_release."
        )
