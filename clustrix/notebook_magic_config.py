"""
Configuration management for notebook magic functionality.

This module contains default configurations and configuration-related utilities
for the notebook magic interface.
"""

import ipaddress
import json
import logging
import os
import yaml
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Union

# The provenance record a saved configuration file carries, and the rule for
# reading one back. Both live in :mod:`clustrix.config` because they are a
# property of *writing a configuration file*, not of the widget's Save
# button: ``ClusterConfig.save_to_file`` writes the same key, and a second
# spelling of the downgrade rule is how the two writers would come to
# disagree. Re-exported here because this module is where the widget's
# provenance helpers live and every existing caller imports them from it.
from .config import (  # noqa: F401
    CONFIG_SOURCES_KEY,
    config_name_from_document,
    config_source_for_saved_entry,
    recorded_config_source,
)

logger = logging.getLogger(__name__)

#: Default cluster configurations available in the widget.
#:
#: This dictionary contains pre-configured cluster templates for common use cases.
#: Each configuration is a dictionary with cluster-specific settings.
DEFAULT_CONFIGS = {
    "Local Single-core": {
        "cluster_type": "local",
        "default_cores": 1,
        "default_memory": "16GB",
    },
    "Local Multi-core": {
        "cluster_type": "local",
        "default_cores": -1,  # Use all available cores
        "default_memory": "16GB",
    },
    "University SLURM Cluster": {
        "cluster_type": "slurm",
        "cluster_host": "login.hpc.university.edu",
        "username": "your_username",
        "default_cores": 16,
        "default_memory": "64GB",
        "default_time": "01:00:00",
        "remote_work_dir": "/scratch/your_username/clustrix",
        "package_manager": "conda",
    },
    "SSH Remote Server": {
        "cluster_type": "ssh",
        "cluster_host": "remote.server.com",
        "username": "user",
        "cluster_port": 22,
        "default_cores": 4,
        "default_memory": "16GB",
        "remote_work_dir": "~/.clustrix/jobs",
        "package_manager": "pip",
    },
    "HuggingFace Jobs": {
        "cluster_type": "huggingface",
        "hf_hardware": "cpu-basic",
        "default_cores": 2,
        "default_memory": "16GB",
    },
}


def detect_config_files(search_dirs: Optional[List[str]] = None) -> List[Path]:
    """Detect configuration files in standard locations."""
    if search_dirs is None:
        search_dirs = [
            ".",  # Current directory
            "~/.clustrix",  # User config directory
            "/etc/clustrix",  # System config directory
        ]
    config_files = []
    config_names = ["clustrix.yml", "clustrix.yaml", "config.yml", "config.yaml"]
    for dir_path in search_dirs:
        path_obj = Path(dir_path).expanduser()
        if path_obj.exists() and path_obj.is_dir():
            for config_name in config_names:
                config_path = path_obj / config_name
                if config_path.exists() and config_path.is_file():
                    config_files.append(config_path)
    return config_files


def config_source_for_detected_file(path: Union[Path, str]) -> str:
    """The provenance of a file :func:`detect_config_files` turned up.

    Nobody named these files; the widget globbed for them. So a config built
    from one carries where it was *found*, exactly as ``ProfileManager``
    does for its store -- and until it did, ``./config.yml`` reached
    ``configure()`` as a raw dict with no provenance at all and was applied
    as though the user had typed it.

    ``.`` is the working directory, and that is a different claim from "a
    configuration directory somewhere else": ``git clone`` followed by ``cd``
    is the whole of what it takes for a repository to supply the file, and
    the message the user is shown has to name that rather than an environment
    variable they never set. Both are untrusted, so this changes what is
    said, not what is allowed. ``~/.clustrix`` and everywhere else are
    classified by :func:`clustrix.config.config_source_for_discovered_path`,
    the one comparison that knows about symlinked configuration directories.
    """
    from .config import (
        CONFIG_SOURCE_WORKING_DIRECTORY,
        config_source_for_discovered_path,
    )

    try:
        found_in = Path(os.path.realpath(Path(path).parent))
        cwd = Path(os.path.realpath(Path.cwd()))
    except (OSError, RuntimeError, ValueError):
        # Unable to establish that it is *not* the working directory is not
        # the same as having established that it is somewhere the user chose.
        return CONFIG_SOURCE_WORKING_DIRECTORY
    if found_in == cwd:
        return CONFIG_SOURCE_WORKING_DIRECTORY
    return config_source_for_discovered_path(path)


def _as_mapping(value: Any) -> Dict[str, Any]:
    """Coerce a parsed document to a mapping, discarding anything else."""
    return value if isinstance(value, dict) else {}


def _read_config_document(file_path: Union[Path, str]) -> Dict[str, Any]:
    """Read and parse one configuration file, raising whatever goes wrong.

    Always returns a *mapping*. YAML happily parses a file of prose into a
    bare string, so an unrecognised extension used to return a `str` from a
    function annotated `-> Dict[str, Any]`; every caller then had to guess.
    """
    path = Path(file_path) if isinstance(file_path, str) else file_path
    content = path.read_text()
    suffix = path.suffix.lower()

    if suffix in (".yml", ".yaml"):
        return _as_mapping(yaml.safe_load(content))
    if suffix == ".json":
        return _as_mapping(json.loads(content))

    # Unknown extension: try both.
    try:
        return _as_mapping(yaml.safe_load(content))
    except yaml.YAMLError:
        return _as_mapping(json.loads(content))


def load_config_from_file(
    file_path: Union[Path, str], *, discovered: bool = False
) -> Dict[str, Any]:
    """Load configuration from a YAML or JSON file.

    Who chose the path decides what a failure means, which is the same
    distinction `clustrix.config` draws and the reason there is not a third
    policy here:

    * **Named** (`discovered=False`, the default). The caller picked this
      file, so failing to read it is an error and it raises --
      `FileNotFoundError`, `PermissionError`, `yaml.YAMLError`,
      `json.JSONDecodeError` -- exactly as `clustrix.config.load_config`
      does for the same file. This used to answer `{}`, which is also the
      answer for a file that genuinely holds no configurations, so a path
      typo, a permissions problem and malformed YAML all presented to the
      user as "this file has nothing in it" and the widget offered the
      result as a valid, blank profile (issue #168).
    * **Discovered** (`discovered=True`). Nobody named it; the widget globbed
      the standard locations for it. Best effort, so an unreadable one is
      skipped rather than taking the widget down -- but the reason is
      *logged* rather than discarded, because "I could not read it" and "it
      holds nothing" are different answers and only one of them deserves
      silence.

    Always returns a *mapping*; see `_read_config_document`.
    """
    if not discovered:
        return _read_config_document(file_path)

    try:
        return _read_config_document(file_path)
    except Exception as exc:
        # Absolute, because the search covers ``.``, the configuration
        # directory and ``/etc/clustrix``, and "clustrix.yml" alone does not
        # tell the user which of them to go and look at.
        try:
            named = os.path.abspath(str(file_path))
        except OSError:  # pragma: no cover - cwd removed under us
            named = str(file_path)
        logger.warning(
            "clustrix found the configuration file %s while searching the "
            "standard locations but could not read it, so none of its "
            "configurations are offered: %s: %s. This is not the same as the "
            "file holding no configurations.",
            named,
            type(exc).__name__,
            exc,
        )
        return {}


def validate_ip_address(ip: str) -> bool:
    """Validate an IP address, v4 or v6.

    This was hand-rolled IPv4-only parsing, so a cluster reachable at an IPv6
    address was reported invalid by the widget's host validation. The stdlib
    already knows the grammar for both.
    """
    if not ip:
        return False
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def validate_hostname(hostname: str) -> bool:
    """Validate hostname format."""
    if not hostname or len(hostname) > 255:
        return False
    # Hostname regex
    hostname_pattern = re.compile(
        r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
        r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
    )
    return bool(hostname_pattern.match(hostname))
