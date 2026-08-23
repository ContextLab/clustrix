"""Profile management system for cluster configurations."""

import json
import stat
import warnings
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import asdict, fields as dataclass_fields

from .config import (
    CONFIG_SOURCE_EXPLICIT_FILE,
    CONFIG_SOURCE_REDIRECTED_CONFIG_DIR,
    CONFIG_SOURCE_UNRECORDED_PROVENANCE,
    CONFIG_SOURCES,
    UNTRUSTED_CONFIG_SOURCES,
    ClusterConfig,
    config_document,
    config_source_for_discovered_path,
    get_config_dir,
    get_config_source,
    set_config_source,
    strip_secret_fields,
    write_config_file_securely,
)

#: The widest a clustrix-owned directory may be: the owner, nobody else.
#: Traversal alone is enough to reach a file inside by name even when the
#: directory cannot be listed, so the group and other bits all have to go.
PRIVATE_DIR_MODE = 0o700


def _warn_if_too_wide(path: Path) -> None:
    """Report an *existing* directory that other local users can enter.

    Reports; it does not change it. That is a reversal of the previous
    behaviour, which chmod-ed any clustrix-owned ancestor down to 0700, and
    the reversal is deliberate:

    * ``_clustrix_owned`` stopped at the configuration directory, so with
      ``CLUSTRIX_CONFIG_DIR=$HOME`` -- an entirely supported setting, and the
      documented answer for containers and CI images -- the directory it
      narrowed to 0700 was ``$HOME`` itself.
    * A configuration directory deliberately shared with a group, mode 0770
      on a shared research machine, was silently forced to 0700 and the
      group locked out of a directory the user had set up for them.

    Both are the same overreach. A directory clustrix created is clustrix's
    to mode; a directory that was already there belongs to whoever made it,
    and re-moding it is a decision only they can take. So the user is handed
    the exact command instead. Nothing is lost that they cannot get back in
    one line, and the files clustrix writes are 0600 in their own right --
    the directory mode is defence in depth, not the guarantee.

    ``$HOME`` and everything above it are not even mentioned: see
    :func:`_clustrix_owned`, which no longer yields them, because a warning
    that fires on every ordinary machine is one nobody reads.
    """
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return
    if not mode & ~PRIVATE_DIR_MODE:
        return
    warnings.warn(
        f"{path} is mode {oct(mode)}, which lets other local users reach the "
        f"files inside it by name. Configuration files, profiles and the "
        f".env credential file all live here. clustrix will not change the "
        f"mode of a directory it did not create -- run: "
        f"chmod {oct(PRIVATE_DIR_MODE)[2:]} {path}",
        stacklevel=3,
    )


def _mkdir_private(directory: Path) -> None:
    """Create ``directory`` and any missing parent, each mode 0700.

    ``Path.mkdir(parents=True, mode=0o700)`` applies the mode to the leaf
    only -- the parents are created with the default permissions, so
    ``~/.clustrix`` ended up 0755 while ``~/.clustrix/profiles`` under it
    was 0700. Profiles are the user's cluster coordinates and usernames;
    nothing under the clustrix config directory is other people's
    business. Each level is therefore created explicitly, and then
    ``chmod``-ed to exactly 0700 -- ``mkdir``'s mode argument is masked by
    the umask, so it guarantees nothing on its own. Under ``umask 0022`` a
    0700 request lands as 0755, and under ``umask 0200`` it lands as 0500,
    a directory clustrix cannot then write into: creating the parent that
    way made the very next ``mkdir`` fail with ``PermissionError``. The
    ``chmod`` has to happen level by level, before the child is attempted.

    Directories that already exist are reported rather than re-moded --
    they are not clustrix's to change. See :func:`_warn_if_too_wide`.
    """
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent

    created = set()
    for path in reversed(missing):
        # The literal is deliberate and must stay one: the static guard in
        # tests/unit/test_credential_file_permissions.py cannot verify a
        # mode it has to resolve a name to reach, so it rejects one. It is
        # the same value as PRIVATE_DIR_MODE below.
        path.mkdir(mode=0o700, exist_ok=True)
        # Ours, brand new, and empty: set the mode outright rather than
        # narrowing, and without a warning about a mode the umask chose.
        path.chmod(PRIVATE_DIR_MODE)
        created.add(path)

    for path in _clustrix_owned(directory):
        if path not in created:
            _warn_if_too_wide(path)


def _clustrix_owned(directory: Path):
    """``directory`` and the ancestors of it that are clustrix's concern.

    Ownership stops at the configuration directory: creating
    ``~/.clustrix/profiles`` is a reason to look at ``~/.clustrix``, and
    never a reason to look at ``$HOME``. A ``directory`` outside the
    configuration directory entirely -- ``ProfileManager(config_dir=...)``
    with somewhere of the caller's choosing -- yields only itself, since
    clustrix asked for that one leaf and nothing above it.

    ``$HOME`` and everything above it are excluded outright, and that is not
    the same rule as "stop at the configuration directory". The two coincide
    only while the configuration directory is *inside* ``$HOME``.
    ``CLUSTRIX_CONFIG_DIR=$HOME`` is supported and documented, and it made
    the configuration directory ``$HOME``: ``ProfileManager()`` then asked
    for ``$HOME/profiles``, this yielded ``$HOME``, and the caller chmod-ed
    the user's home directory from 0755 to 0700. A home directory is the
    user's, whatever any environment variable makes it also mean.
    """
    forbidden: set = set()
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        # No determinable home directory (a service account, a scrubbed
        # environment). Nothing can then be shown to be at or above it, so
        # the configuration-directory rule below is all there is.
        pass
    else:
        forbidden = {home, *home.parents}

    if directory in forbidden:
        return
    yield directory
    try:
        config_dir = get_config_dir().resolve()
        resolved = directory.resolve()
    except OSError:
        return
    if resolved in forbidden:
        return
    if resolved == config_dir or config_dir not in resolved.parents:
        return
    for parent in resolved.parents:
        if parent in forbidden:
            return
        yield parent
        if parent == config_dir:
            return


#: Top-level key in the profile store recording, per profile, the
#: configuration source the profile carried when it was written.
#:
#: Provenance used to stop at the process boundary. ``_persist()`` fires from
#: seven mutators, and ``save_to_file`` wrote only the declared *fields* --
#: which the source deliberately is not, because a field is something a
#: hostile file could set. So a profile read out of a repository's
#: ``profiles.yml`` (``redirected-config-dir``, refused, hostname tainted) was
#: copied by the next mutator into ``<config_dir>/profiles/profiles.yml``, and
#: the next process's ``_restore`` re-derived the source from where the file
#: now *was* -- ``user-config-dir``, trusted -- and released the credential.
#: The credential gate was not bypassed: it was asked a question whose answer
#: had already been destroyed.
PROFILE_SOURCES_KEY = "profile_sources"


def _restored_profile_source(file_source: str, recorded: Any) -> str:
    """The source a restored profile gets: ``file_source``, or worse.

    A persisted source may only ever *downgrade*. That asymmetry is the whole
    security property, and it is what makes writing the source down safe at
    all: if a file could raise its own trust by saying so, this key would be
    the laundering route it is meant to close -- exactly why
    ``_clustrix_config_source`` is set with ``setattr`` rather than declared
    as a dataclass field.

    So:

    * an untrusted recorded source is believed, whatever the file's own
      provenance says. A profile that came from a working directory stays
      from a working directory after it is copied into ``~/.clustrix``.
    * a *trusted* recorded source is ignored, and the file's own provenance
      stands. A bundle a repository ships cannot promote its profiles by
      writing ``explicit-file`` next to them.
    * anything clustrix does not recognise -- a truncated file, a hand-edited
      one, an attacker's invention -- is treated as a redirect rather than
      raised on. Refusing to restore would cost the user every profile they
      have; refusing to *trust* costs one credential release the message
      explains.
    * **nothing recorded at all is not an answer, and must not be resolved
      into one.** See below.

    Persisting the source rather than refusing to persist an untrusted
    profile at all is the deliberate choice. A user may legitimately want to
    keep a project-local profile -- the hostname, the partition, the working
    directory are all still useful -- and dropping it on save would delete
    something they can see in the widget without their asking. Keeping it and
    keeping *why it is refused* preserves the profile and the refusal
    together, which is the honest pair.

    **Absence fails closed.** Every store written before
    :data:`PROFILE_SOURCES_KEY` existed records nothing, and resolving that
    silence to ``file_source`` meant the store a pre-fix clustrix had
    *already* laundered came back ``user-config-dir``, trusted, credential
    released -- so the fix protected nobody who was already affected. It also
    put two opposite defaults in one subsystem:
    :func:`clustrix.config.get_config_source` reads a missing record as
    *untrusted*, and this read it as trusted.

    A store version key was the other candidate and does no work here. An
    unversioned store would have to fail closed anyway -- absence of the
    version key is exactly as forgeable as absence of the source -- so the
    key would only restate what absence already says, in a second mechanism
    that can disagree with the first. Re-deriving provenance from where the
    file now sits, the third candidate, *is* the defect written down.

    What the silence resolves to depends on one thing: whether anybody named
    this file.

    * ``file_source`` is already untrusted -- the store was discovered in a
      working directory or a redirected config directory. That is not
      silence, it is knowledge about the file, and it is the answer.
    * ``explicit-file`` -- a caller passed this path. That is the user saying
      "these profiles are mine" about a file they identified, which is the
      same act ``load_from_file``'s default already treats as authorisation
      for a bundle carrying no sources at all. It is also the only way back:
      see :data:`clustrix.config.CONFIG_SOURCE_UNRECORDED_PROVENANCE`.
    * otherwise -- ``user-config-dir``, the store ``_restore`` found by
      itself. Trusted, but *discovered*: nobody named it, and route 8's whole
      point is that a profile arrives in that directory by being copied
      there. Unknown, and it says so.

    A recorded ``unrecorded-provenance`` re-enters the same branch rather
    than being believed as an untrusted verdict, so re-persisting a legacy
    store does not turn "we do not know" into "we know it is bad" -- which
    would be permanent, since a recorded untrusted source may not be
    upgraded.
    """
    if recorded is None or recorded == CONFIG_SOURCE_UNRECORDED_PROVENANCE:
        if file_source in UNTRUSTED_CONFIG_SOURCES:
            return file_source
        if file_source == CONFIG_SOURCE_EXPLICIT_FILE:
            return file_source
        return CONFIG_SOURCE_UNRECORDED_PROVENANCE
    if not isinstance(recorded, str) or recorded not in CONFIG_SOURCES:
        return CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    if recorded in UNTRUSTED_CONFIG_SOURCES:
        return recorded
    return file_source


def adopt_profile_store(store_path: Optional[str] = None) -> List[str]:
    """Say, once, that the profiles in a store are yours. Returns their names.

    The way back from :data:`~clustrix.config.CONFIG_SOURCE_UNRECORDED_PROVENANCE`.
    A store written before clustrix recorded provenance says nothing about
    where its profiles came from, and silence fails closed -- see
    :func:`_restored_profile_source`. This is the user supplying the answer
    that is missing, for a file they name, after looking at what is in it.

    It works on the **file**, not on a loaded ``ProfileManager``, and that is
    the point rather than a convenience. Restoring a store marks its
    hostnames untrusted process-wide, and that record deliberately has no way
    back through any function call -- a widget's Apply button is a
    ``configure()`` call, so a rule that let one clear it would reopen the
    laundering route. Rewriting the file records the answer before anything
    reads it, so the next process starts from a store that knows.

    It is not a way to grant trust, only to stop withholding it. An entry
    that already records a source is left exactly as it is, so a profile a
    repository shipped -- ``working-directory``, ``redirected-config-dir`` --
    stays refused however often this is run, and a store adopted from a
    redirected configuration directory still loads untrusted, because the
    directory it sits in is what decides that. The most this can say is
    ``explicit-file``, which is what naming a path to
    :meth:`ProfileManager.load_from_file` already means.

    Args:
        store_path: the store to adopt. Defaults to the one
            :class:`ProfileManager` uses, under the clustrix configuration
            directory.

    Returns:
        The profiles whose provenance this recorded, in file order. An empty
        list means every profile already had an answer and nothing changed.
    """
    if store_path is None:
        path = get_config_dir() / "profiles" / ProfileManager.STORE_FILENAME
    else:
        path = Path(store_path).expanduser()

    if not path.exists():
        raise FileNotFoundError(f"No profile store at {path}")

    with open(path, encoding="utf-8") as handle:
        if path.suffix.lower() == ".json":
            data = json.load(handle)
        else:
            data = yaml.safe_load(handle)

    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, dict):
        raise ValueError(f"{path} does not contain a profile bundle")

    recorded = data.get(PROFILE_SOURCES_KEY)
    if not isinstance(recorded, dict):
        recorded = {}

    adopted = []
    for name in profiles:
        existing = recorded.get(name)
        if existing is None or existing == CONFIG_SOURCE_UNRECORDED_PROVENANCE:
            recorded[name] = CONFIG_SOURCE_EXPLICIT_FILE
            adopted.append(name)

    data[PROFILE_SOURCES_KEY] = recorded
    write_config_file_securely(path, data)
    return adopted


class ProfileManager:
    """Manages cluster configuration profiles with save/load functionality."""

    def __init__(self, config_dir: Optional[str] = None):
        """Initialize ProfileManager with default or custom config directory.

        When ``config_dir`` is omitted, this defers to
        ``clustrix.config.get_config_dir()`` (a ``profiles`` subdirectory of
        it) rather than hardcoding ``~/.clustrix/profiles``. That keeps
        ``CLUSTRIX_CONFIG_DIR`` in effect for callers -- including the
        widget's default ``ProfileManager()`` -- so tests and containers
        never read or write a real user's ``~/.clustrix``.
        """
        if config_dir is None:
            self.config_dir = get_config_dir() / "profiles"
        else:
            self.config_dir = Path(config_dir).expanduser()
        try:
            _mkdir_private(self.config_dir)
        except OSError as e:
            # An unwritable config directory must not stop the widget from
            # opening. Profiles then live for the session only, and _persist
            # reports the same problem when it tries to save.
            warnings.warn(f"Cannot create profile directory {self.config_dir}: {e}")

        self.profiles: Dict[str, ClusterConfig] = {}
        self.active_profile: Optional[str] = None
        self._announced_dropped_secrets = False
        self._load_default_profiles()
        self._restore()

    #: Starting points for each backend clustrix supports, so the dropdown is
    #: something to choose from rather than a single entry to edit. Each is a
    #: usable shape with sensible resources; the fields only the user can know
    #: -- host, username, namespace -- are deliberately blank, and the widget's
    #: validation names them before anything is submitted.
    #:
    #: These are templates: clone one, fill it in, and save.
    BUILTIN_PROFILES: Dict[str, Dict[str, Any]] = {
        "Local single-core": {
            "cluster_type": "local",
            "default_cores": 1,
            "default_memory": "16.25GB",
            "default_time": "01:00:00",
        },
        "Local all cores": {
            # -1 means "every core on this machine".
            "cluster_type": "local",
            "default_cores": -1,
            "default_memory": "16.25GB",
            "default_time": "01:00:00",
        },
        "SLURM cluster": {
            "cluster_type": "slurm",
            "default_cores": 8,
            "default_memory": "32GB",
            "default_time": "02:00:00",
            "remote_work_dir": "~/.clustrix/jobs",
        },
        "SSH remote machine": {
            "cluster_type": "ssh",
            "default_cores": 4,
            "default_memory": "16GB",
            "default_time": "01:00:00",
            "remote_work_dir": "~/.clustrix/jobs",
        },
        "HuggingFace Jobs (CPU)": {
            "cluster_type": "huggingface",
            "hf_flavor": "cpu-basic",
            "default_cores": 2,
            "default_memory": "8GB",
            "default_time": "01:00:00",
        },
        "HuggingFace Jobs (GPU)": {
            # hf_allow_gpu_flavors stays False: GPU flavors bill by the second,
            # so running one is an explicit decision, not a side effect of
            # picking a profile. Submitting without it fails with a message
            # saying exactly that.
            "cluster_type": "huggingface",
            "hf_flavor": "a10g-small",
            "hf_allow_gpu_flavors": False,
            "default_cores": 4,
            "default_memory": "16GB",
            "default_time": "01:00:00",
        },
    }

    #: Selected when the widget opens and nothing else is configured.
    DEFAULT_PROFILE = "Local single-core"

    def _load_default_profiles(self) -> None:
        """Install the built-in templates if no profiles exist yet."""
        if self.profiles:
            return
        for name, settings in self.BUILTIN_PROFILES.items():
            self.profiles[name] = ClusterConfig(**settings)
        self.active_profile = self.DEFAULT_PROFILE

    #: Where profiles live between sessions.
    STORE_FILENAME = "profiles.yml"

    @property
    def store_path(self) -> Path:
        return self.config_dir / self.STORE_FILENAME

    def _restore(self) -> None:
        """Reload profiles saved by an earlier session.

        Without this the profile system forgot everything on kernel restart:
        the built-in templates were re-seeded and anything the user had built
        was gone, which made the whole profile row feel like scratch space.
        A store that cannot be read must not stop the widget from opening, so
        the built-ins stand and the problem is reported rather than raised.

        **Nobody named this file.** It is discovered from ``config_dir``, so
        it carries the provenance of where it was found rather than
        ``explicit-file``: inside ``~/.clustrix`` the user put it there, and
        anywhere else -- a directory ``CLUSTRIX_CONFIG_DIR`` named, a
        directory a caller passed to ``ProfileManager`` -- it is ambient. A
        repository shipping an ``.envrc`` that sets ``CLUSTRIX_CONFIG_DIR``
        plus a ``profiles/profiles.yml`` under it needed no ``config.yml`` at
        all to choose ``cluster_host``, and until this said so the config came
        back marked ``runtime`` and the victim's exported ``SSH_PASSWORD``
        reached the repository's host.
        """
        if not self.store_path.exists():
            return
        try:
            self.load_from_file(
                str(self.store_path),
                source=config_source_for_discovered_path(self.store_path),
            )
        except Exception as e:  # noqa: BLE001
            import warnings

            warnings.warn(f"Could not read saved profiles from {self.store_path}: {e}")

    def _persist(self) -> None:
        """Write profiles out so the next session starts where this one left off."""
        try:
            self.save_to_file(str(self.store_path))
        except Exception as e:  # noqa: BLE001
            import warnings

            warnings.warn(f"Could not save profiles to {self.store_path}: {e}")

    def create_profile(self, name: str, config: ClusterConfig) -> None:
        """Create a new configuration profile."""
        if name in self.profiles:
            raise ValueError(f"Profile '{name}' already exists")

        self.profiles[name] = config
        self.active_profile = name

        self._persist()

    def clone_profile(self, original_name: str, new_name: Optional[str] = None) -> str:
        """Clone an existing profile with a new name."""
        if original_name not in self.profiles:
            raise ValueError(f"Profile '{original_name}' does not exist")

        if new_name is None:
            # Auto-generate name
            base_name = f"{original_name} (copy)"
            counter = 1
            new_name = base_name
            while new_name in self.profiles:
                new_name = f"{base_name} {counter}"
                counter += 1

        if new_name in self.profiles:
            raise ValueError(f"Profile '{new_name}' already exists")

        # Create a copy of the config
        original_config = self.profiles[original_name]
        # Create new config with same values
        new_config = ClusterConfig(**asdict(original_config))

        self.profiles[new_name] = new_config
        self.active_profile = new_name
        self._persist()
        return new_name

    def remove_profile(self, name: str) -> None:
        """Remove a profile. Cannot remove if it's the only profile."""
        if len(self.profiles) <= 1:
            raise ValueError("Cannot remove the last remaining profile")

        if name not in self.profiles:
            raise ValueError(f"Profile '{name}' does not exist")

        del self.profiles[name]

        # Update active profile if necessary
        if self.active_profile == name:
            self.active_profile = next(iter(self.profiles.keys()))

        self._persist()

    def load_profile(self, name: str) -> ClusterConfig:
        """Return a profile by name.

        Reading a profile does not select it. It used to: `load_profile` set
        `active_profile` as a side effect, so merely inspecting profiles in a
        loop left the last one inspected marked active. Use
        `set_active_profile` to switch.
        """
        if name not in self.profiles:
            raise ValueError(f"Profile '{name}' does not exist")

        return self.profiles[name]

    def save_profile(self, name: str, config: ClusterConfig) -> None:
        """Save/update a profile configuration."""
        self.profiles[name] = config
        if self.active_profile is None:
            self.active_profile = name

        self._persist()

    def rename_profile(self, old_name: str, new_name: str) -> None:
        """Rename an existing profile."""
        if old_name not in self.profiles:
            raise ValueError(f"Profile '{old_name}' does not exist")

        if new_name in self.profiles:
            raise ValueError(f"Profile '{new_name}' already exists")

        config = self.profiles.pop(old_name)
        self.profiles[new_name] = config

        if self.active_profile == old_name:
            self.active_profile = new_name

        self._persist()

    def get_profile_names(self) -> List[str]:
        """Get list of all profile names."""
        return list(self.profiles.keys())

    def get_active_profile(self) -> Optional[ClusterConfig]:
        """Get the currently active profile configuration."""
        if self.active_profile and self.active_profile in self.profiles:
            return self.profiles[self.active_profile]
        return None

    def set_active_profile(self, name: str) -> ClusterConfig:
        """Set the active profile and return its configuration."""
        if name not in self.profiles:
            raise ValueError(f"Profile '{name}' does not exist")

        self.active_profile = name
        self._persist()
        return self.profiles[name]

    def _announce_dropped_secrets(self, persisted: Dict[str, Any]) -> None:
        """Say once that credentials were left out of the file.

        Compares what is about to be written against what is held in
        memory. Warning once per manager rather than per save is the point:
        ``_persist()`` fires from seven mutators, so a per-save warning
        would be noise and would be filtered out, which is the same as not
        warning at all.

        A field is reported only when it holds something. An empty
        ``environment_variables`` mapping is dropped by
        ``strip_secret_fields`` like a populated one, and warning about the
        loss of nothing would fire for every profile ever saved -- a notice
        that always fires is one nobody reads.
        """
        if self._announced_dropped_secrets:
            return
        dropped = {
            field
            for name, config in self.profiles.items()
            for field, value in asdict(config).items()
            if value and field not in persisted.get(name, {})
        }
        if not dropped:
            return
        self._announced_dropped_secrets = True

        warnings.warn(
            "Profiles are not a credential store: "
            f"{', '.join(sorted(dropped))} were not written to disk and will "
            "not survive a restart. They still work for the rest of this "
            "session. To supply a password without writing it to disk, set "
            "password_env_var to the name of an environment variable holding "
            "it. environment_variables, gpu_requirements and venv_info are "
            "withheld for the same reason: their keys and values are yours, "
            "so clustrix cannot tell a setting from a token and does not "
            "guess.",
            stacklevel=3,
        )

    def save_to_file(self, filepath: str) -> None:
        """Save all profiles to a configuration file, owner-readable only.

        Two properties this deliberately shares with
        :meth:`clustrix.config.ClusterConfig.save_to_file`, because it used
        to have neither:

        **Mode.** The write goes through ``write_text_securely``, so the
        file is 0600 from the instant it exists. It used to be a plain
        ``open(..., "w")``, which under the default umask leaves the file
        0644 -- readable by every other local user (issue #111).

        **Secrets.** Passwords, tokens and API keys are dropped, with no
        ``include_secrets`` escape hatch, so a profile bundle can never
        hold a credential in plaintext. ``asdict(config)`` used to route
        straight around the filtering that ``ClusterConfig.save_to_file``
        applies, and wrote them.

        Dropping rather than offering an opt-in is the deliberate call,
        for three reasons:

        1. **Nobody asked for this write.** ``_persist()`` fires
           automatically from seven mutators -- creating, cloning,
           renaming, removing, saving, importing a profile, or merely
           switching the active one. ``include_secrets=True`` on
           ``ClusterConfig.save_to_file`` is a considered act by a caller
           who named a path; there is no equivalent moment here at which
           a user could consent to their password being written out.
        2. **One file, every profile.** ``profiles.yml`` is a bulk store,
           so a single leak is as many credentials as the user has hosts.
        3. **There is a supported channel.** ``password_env_var`` (kept:
           it names a variable, it is not itself a secret) is how a
           credential is meant to reach clustrix without being written to
           disk -- see ``CLAUDE.md``.

        The cost is bounded and in-memory only: a secret set on a profile
        stays usable for the rest of the session, it simply does not
        survive a restart. That is the intended trade, and it is said out
        loud once per session rather than happening silently -- discarding
        something the user typed without telling them would be its own
        surprise.
        """
        data: Dict[str, Any] = {
            "active_profile": self.active_profile,
            "profiles": {},
            PROFILE_SOURCES_KEY: {},
        }

        for name, config in self.profiles.items():
            data["profiles"][name] = strip_secret_fields(asdict(config))
            # Written *outside* the profile mapping, because everything
            # inside it is a declared field and gets passed to
            # ``ClusterConfig(**config_dict)``. See PROFILE_SOURCES_KEY for
            # why this has to be written at all, and
            # ``_restored_profile_source`` for why writing it cannot be used
            # to claim trust.
            data[PROFILE_SOURCES_KEY][name] = get_config_source(config)

        self._announce_dropped_secrets(data["profiles"])

        write_config_file_securely(Path(filepath), data)

    def load_from_file(
        self, filepath: str, source: str = CONFIG_SOURCE_EXPLICIT_FILE
    ) -> None:
        """Replace the current profiles with those in `filepath`.

        Built either way or not at all. This used to clear self.profiles and
        then populate it entry by entry, so a single unreadable profile left
        the manager holding a partial set with the built-in templates gone and
        active_profile naming something that no longer existed.

        ``source`` is where the file came from, for the credential layer's
        benefit: ``explicit-file`` by default, because a caller passing a
        path has named it, and every profile in the bundle is stamped with it
        rather than with ``__post_init__``'s ``runtime``. ``_restore`` passes
        the provenance of the directory it found the store in, since nobody
        named that one.
        """
        filepath_obj = Path(filepath)
        if not filepath_obj.exists():
            raise FileNotFoundError(f"Configuration file not found: {filepath}")

        with open(filepath_obj, encoding="utf-8") as f:
            if filepath_obj.suffix.lower() == ".json":
                data = json.load(f)
            else:
                data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"{filepath} does not contain a profile bundle")

        recorded_sources = data.get(PROFILE_SOURCES_KEY)
        if not isinstance(recorded_sources, dict):
            recorded_sources = {}

        loaded: Dict[str, ClusterConfig] = {}
        for name, config_dict in (data.get("profiles") or {}).items():
            if not isinstance(config_dict, dict):
                raise ValueError(f"Profile {name!r} in {filepath} is not a mapping")
            known = {f.name for f in dataclass_fields(ClusterConfig)}
            unknown = set(config_dict) - known
            if unknown:
                raise ValueError(
                    f"Profile {name!r} in {filepath} has unknown setting(s): "
                    f"{', '.join(sorted(unknown))}"
                )
            # ``from_file_content`` takes the source as an argument, so this
            # loader cannot forget to say where the bytes came from -- which
            # is exactly what it used to do. It also makes the permanent
            # claim on the hostname: this loader opened the file, unlike
            # ``__post_init__``, which infers a source and may therefore
            # only mark one object. A later rebuild (Apply's
            # ``configure(**asdict(cfg))``, ``dataclasses.replace``) cannot
            # launder it back to ``runtime``.
            loaded[name] = ClusterConfig.from_file_content(
                config_dict, source, origin=f"Profile {name!r} in {filepath}"
            )

        # The store's own per-profile record is applied on top of ``source``,
        # and may only *lower* it -- see ``_restored_profile_source``.
        # Without it, provenance died at the process boundary and a profile a
        # repository shipped came back trusted merely because a mutator had
        # since copied it into the user's own configuration directory.
        unrecorded = []
        for name, config in loaded.items():
            restored = _restored_profile_source(source, recorded_sources.get(name))
            if restored == CONFIG_SOURCE_UNRECORDED_PROVENANCE and config.cluster_host:
                # Only the ones that name a host. Provenance decides who may
                # receive a credential, so a profile naming nobody has
                # nothing at stake, and listing the built-in templates --
                # which a pre-fix ``_persist`` also copied into the store --
                # would bury the one entry the user has to look at.
                unrecorded.append(name)
            # ``record_host`` is left at its default even for
            # ``unrecorded-provenance``, and that is the load-bearing part of
            # closing route 9a rather than merely labelling it. Marking the
            # *object* untrusted stops a direct use of the profile and
            # nothing else: the ordinary way to use a profile is to apply it,
            # and both widgets' Apply is ``configure(**...)`` from the
            # profile's own fields, which builds a fresh object whose own
            # source is ``runtime``. Measured: with the hostname left
            # unrecorded, a pre-fix store still authenticated the sentinel to
            # the host a repository's bundle named. The hostname is what
            # receives the credential, so the hostname is what has to carry
            # the doubt.
            #
            # The cost is that the doubt then outlives every rebuild in the
            # process, which is why it has to be undoable at all: that is
            # ``adopt_profile_store``, which works on the file and so does
            # not have to argue with a record that has no way back.
            set_config_source(config, restored)

        if unrecorded:
            warnings.warn(
                f"{len(unrecorded)} profile(s) in {filepath} predate clustrix "
                f"recording where each profile came from ("
                f"{', '.join(sorted(unrecorded))}), so clustrix does not know "
                f"whether you created them or something else wrote them "
                f"there. They still load and every other way of connecting "
                f"still works; what is refused is releasing a stored "
                f"credential that names no host to their cluster_host. Check "
                f"that you recognise all of them, then run "
                f"clustrix.adopt_profile_store() once and start a new "
                f"process."
            )

        if not loaded:
            raise ValueError(f"{filepath} contains no profiles")

        # Swap in only once everything parsed.
        self.profiles = loaded
        active = data.get("active_profile")
        # An active profile naming something absent would make
        # get_active_profile() return None for the rest of the session.
        self.active_profile = (
            active if active in self.profiles else next(iter(self.profiles))
        )

    def export_profile(self, profile_name: str, filepath: str) -> None:
        """Export a single profile to a file, owner-readable only.

        Same two rules as :meth:`save_to_file`, and for a stronger reason:
        an exported profile is a file made to be sent to a colleague or
        committed to a repository, which is the last place a password
        should be.

        Both rules come from :func:`clustrix.config.config_document`, which
        is also where the provenance record comes from -- an export lands
        wherever the caller says, ``~/.clustrix/config.yml`` included, and a
        profile a repository supplied must not become the user's own by
        being copied there. :meth:`import_profile` reads the record back.
        """
        if profile_name not in self.profiles:
            raise ValueError(f"Profile '{profile_name}' does not exist")

        config_data = config_document(self.profiles[profile_name])
        self._announce_dropped_secrets({profile_name: config_data})
        write_config_file_securely(Path(filepath), config_data)

    def import_profile(self, filepath: str, profile_name: Optional[str] = None) -> str:
        """Import a single profile from a file."""
        filepath_obj = Path(filepath)

        if not filepath_obj.exists():
            raise FileNotFoundError(f"Configuration file '{filepath}' not found")

        # Load configuration
        config_dict: Any
        if filepath_obj.suffix.lower() == ".json":
            with open(filepath_obj, "r", encoding="utf-8") as f:
                config_dict = json.load(f)
        else:  # Assume YAML
            with open(filepath_obj, "r", encoding="utf-8") as f:
                config_dict = yaml.safe_load(f)

        # Create config object. The caller named this path, so it is
        # ``explicit-file`` -- but it is still a file, so the source is
        # passed rather than left to ``__post_init__``'s ``runtime`` default.
        # A record the file carries may lower it: from_file_content handles
        # CONFIG_SOURCES_KEY itself.
        config = ClusterConfig.from_file_content(
            config_dict, CONFIG_SOURCE_EXPLICIT_FILE, origin=str(filepath_obj)
        )

        # Generate profile name if not provided
        if profile_name is None:
            profile_name = filepath_obj.stem

        # Ensure unique name
        original_name = profile_name
        counter = 1
        while profile_name in self.profiles:
            profile_name = f"{original_name} ({counter})"
            counter += 1

        # Add profile
        self.profiles[profile_name] = config
        self.active_profile = profile_name

        self._persist()
        return profile_name
