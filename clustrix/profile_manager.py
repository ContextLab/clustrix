"""Profile management system for cluster configurations."""

import json
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import asdict, fields as dataclass_fields

from .config import ClusterConfig


class ProfileManager:
    """Manages cluster configuration profiles with save/load functionality."""

    def __init__(self, config_dir: str = "~/.clustrix/profiles"):
        """Initialize ProfileManager with default or custom config directory."""
        self.config_dir = Path(config_dir).expanduser()
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # An unwritable config directory must not stop the widget from
            # opening. Profiles then live for the session only, and _persist
            # reports the same problem when it tries to save.
            import warnings

            warnings.warn(f"Cannot create profile directory {self.config_dir}: {e}")

        self.profiles: Dict[str, ClusterConfig] = {}
        self.active_profile: Optional[str] = None
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
        "PBS cluster": {
            "cluster_type": "pbs",
            "default_cores": 8,
            "default_memory": "32GB",
            "default_time": "02:00:00",
            "remote_work_dir": "~/.clustrix/jobs",
        },
        "SGE cluster": {
            "cluster_type": "sge",
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
        "Kubernetes": {
            "cluster_type": "kubernetes",
            "default_cores": 4,
            "default_memory": "8GB",
            "default_time": "01:00:00",
            "k8s_namespace": "default",
            "k8s_image": "python:3.12-slim",
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

    def save_to_file(self, filepath: str) -> None:
        """Save all profiles to a configuration file."""
        filepath_obj = Path(filepath)

        # Prepare data for saving
        data: Dict[str, Any] = {"active_profile": self.active_profile, "profiles": {}}

        for name, config in self.profiles.items():
            data["profiles"][name] = asdict(config)

        # Save based on file extension
        if filepath_obj.suffix.lower() == ".json":
            with open(filepath_obj, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        else:  # Default to YAML (.yml, .yaml, or no extension)
            with open(filepath_obj, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, indent=2)

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
        """Export a single profile to a file."""
        if profile_name not in self.profiles:
            raise ValueError(f"Profile '{profile_name}' does not exist")

        config = self.profiles[profile_name]
        filepath_obj = Path(filepath)

        # Save based on file extension
        if filepath_obj.suffix.lower() == ".json":
            with open(filepath_obj, "w", encoding="utf-8") as f:
                json.dump(asdict(config), f, indent=2)
        else:  # Default to YAML
            with open(filepath_obj, "w", encoding="utf-8") as f:
                yaml.dump(asdict(config), f, default_flow_style=False, indent=2)

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
