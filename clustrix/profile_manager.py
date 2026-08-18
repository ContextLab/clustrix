"""Profile management system for cluster configurations."""

import json
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import asdict

from .config import ClusterConfig


class ProfileManager:
    """Manages cluster configuration profiles with save/load functionality."""

    def __init__(self, config_dir: str = "~/.clustrix/profiles"):
        """Initialize ProfileManager with default or custom config directory."""
        self.config_dir = Path(config_dir).expanduser()
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.profiles: Dict[str, ClusterConfig] = {}
        self.active_profile: Optional[str] = None
        self._load_default_profiles()

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

    def create_profile(self, name: str, config: ClusterConfig) -> None:
        """Create a new configuration profile."""
        if name in self.profiles:
            raise ValueError(f"Profile '{name}' already exists")

        self.profiles[name] = config
        self.active_profile = name

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
            with open(filepath_obj, "w") as f:
                json.dump(data, f, indent=2)
        else:  # Default to YAML (.yml, .yaml, or no extension)
            with open(filepath_obj, "w") as f:
                yaml.dump(data, f, default_flow_style=False, indent=2)

    def load_from_file(self, filepath: str) -> None:
        """Load profiles from a configuration file, replacing current profiles."""
        filepath_obj = Path(filepath)

        if not filepath_obj.exists():
            raise FileNotFoundError(f"Configuration file '{filepath}' not found")

        # Load based on file extension
        data: Any
        if filepath_obj.suffix.lower() == ".json":
            with open(filepath_obj, "r") as f:
                data = json.load(f)
        else:  # Assume YAML
            with open(filepath_obj, "r") as f:
                data = yaml.safe_load(f)

        if not isinstance(data, dict) or "profiles" not in data:
            raise ValueError("Invalid configuration file format")

        # Clear current profiles
        self.profiles.clear()

        # Load profiles
        for name, config_dict in data["profiles"].items():
            config = ClusterConfig(**config_dict)
            self.profiles[name] = config

        # Set active profile
        self.active_profile = data.get("active_profile")
        if self.active_profile not in self.profiles and self.profiles:
            self.active_profile = next(iter(self.profiles.keys()))

        # Ensure we have at least one profile
        if not self.profiles:
            self._load_default_profiles()

    def export_profile(self, profile_name: str, filepath: str) -> None:
        """Export a single profile to a file."""
        if profile_name not in self.profiles:
            raise ValueError(f"Profile '{profile_name}' does not exist")

        config = self.profiles[profile_name]
        filepath_obj = Path(filepath)

        # Save based on file extension
        if filepath_obj.suffix.lower() == ".json":
            with open(filepath_obj, "w") as f:
                json.dump(asdict(config), f, indent=2)
        else:  # Default to YAML
            with open(filepath_obj, "w") as f:
                yaml.dump(asdict(config), f, default_flow_style=False, indent=2)

    def import_profile(self, filepath: str, profile_name: Optional[str] = None) -> str:
        """Import a single profile from a file."""
        filepath_obj = Path(filepath)

        if not filepath_obj.exists():
            raise FileNotFoundError(f"Configuration file '{filepath}' not found")

        # Load configuration
        config_dict: Any
        if filepath_obj.suffix.lower() == ".json":
            with open(filepath_obj, "r") as f:
                config_dict = json.load(f)
        else:  # Assume YAML
            with open(filepath_obj, "r") as f:
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

        return profile_name
