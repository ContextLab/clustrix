"""Profile management system for cluster configurations."""

import json
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import asdict, fields as dataclass_fields

from .config import (
    ClusterConfig,
    get_config_dir,
    strip_secret_fields,
    write_config_file_securely,
)


def _mkdir_private(directory: Path) -> None:
    """Create ``directory`` and any missing parent, each mode 0700.

    ``Path.mkdir(parents=True, mode=0o700)`` applies the mode to the leaf
    only -- the parents are created with the default permissions, so
    ``~/.clustrix`` ended up 0755 while ``~/.clustrix/profiles`` under it
    was 0700. Profiles are the user's cluster coordinates and usernames;
    nothing under the clustrix config directory is other people's
    business. Each level is therefore created explicitly.

    An existing directory is left exactly as the user set it up: widening
    is the bug, and silently re-moding a directory somebody else created
    is the same overreach that ``write_text_securely`` refuses.
    """
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for path in reversed(missing):
        path.mkdir(mode=0o700, exist_ok=True)


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
            import warnings

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
        """
        if not self.store_path.exists():
            return
        try:
            self.load_from_file(str(self.store_path))
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
        """
        if self._announced_dropped_secrets:
            return
        dropped = {
            field
            for name, config in self.profiles.items()
            for field, value in asdict(config).items()
            if value not in (None, "") and field not in persisted.get(name, {})
        }
        if not dropped:
            return
        self._announced_dropped_secrets = True

        import warnings

        warnings.warn(
            "Profiles are not a credential store: "
            f"{', '.join(sorted(dropped))} were not written to disk and will "
            "not survive a restart. They still work for the rest of this "
            "session. To supply a password without writing it to disk, set "
            "password_env_var to the name of an environment variable holding "
            "it.",
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
        data: Dict[str, Any] = {"active_profile": self.active_profile, "profiles": {}}

        for name, config in self.profiles.items():
            data["profiles"][name] = strip_secret_fields(asdict(config))

        self._announce_dropped_secrets(data["profiles"])

        write_config_file_securely(Path(filepath), data)

    def load_from_file(self, filepath: str) -> None:
        """Replace the current profiles with those in `filepath`.

        Built either way or not at all. This used to clear self.profiles and
        then populate it entry by entry, so a single unreadable profile left
        the manager holding a partial set with the built-in templates gone and
        active_profile naming something that no longer existed.
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
            loaded[name] = ClusterConfig(**config_dict)

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
        """
        if profile_name not in self.profiles:
            raise ValueError(f"Profile '{profile_name}' does not exist")

        config_data = strip_secret_fields(asdict(self.profiles[profile_name]))
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

        # Create config object
        config = ClusterConfig(**config_dict)

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
