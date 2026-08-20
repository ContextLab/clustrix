"""
Configuration management for notebook magic functionality.

This module contains default configurations and configuration-related utilities
for the notebook magic interface.
"""

import ipaddress
import json
import os
import yaml
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Union

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


#: Top-level key in a widget-saved configuration file recording, per
#: configuration name, the source that configuration carried when it was
#: written.
#:
#: Route 12. ``_on_save_config`` writes into :func:`get_config_dir`, and
#: :func:`detect_config_files` infers trust from exactly that directory. So
#: pressing Save on a configuration the widget had *found* in a cloned
#: repository copied it to ``~/.clustrix/config.yml``, and the next session's
#: widget re-derived the source from where the file now was --
#: ``user-config-dir``, trusted -- and released the credential. The in-memory
#: invariant held the whole time: every sidecar was still valid when Save
#: returned, and the laundering happened on disk, one restart later.
#:
#: The precondition is attacker-controlled, because the filename comes from
#: the configuration's own ``name``: ``""`` and ``Config`` both save as
#: ``config.yml`` and ``clustrix`` saves as ``clustrix.yml``, all three of
#: which :func:`detect_config_files` looks for. And a save writes *every*
#: configuration in the dropdown, including ones the user never selected and
#: never looked at, verbatim -- so a repository shipping a second entry rides
#: along with the one the user meant to keep.
#:
#: This is the same defect ``profile_manager.PROFILE_SOURCES_KEY`` closes for
#: the profile store, and it takes the same answer for the same reason:
#: persist the source rather than refuse to persist the configuration, so a
#: user who deliberately keeps a project-local configuration keeps it -- and
#: keeps the refusal that goes with it. Preserving the configuration and *why
#: it is refused* is the honest pair.
CONFIG_SOURCES_KEY = "config_sources"


def recorded_config_source(recorded: Any, name: str) -> Any:
    """The source ``name``'s entry claims, out of a whole file's record.

    The record is written by clustrix and read back from a file anybody may
    have edited, so its *shape* is untrusted too. A record that is not a
    mapping is not a record about ``name`` in particular; it is an
    unrecognised value, and every entry in the file inherits it so that
    :func:`config_source_for_saved_entry` can fail it closed. Returning
    ``None`` there would let a one-character edit -- ``config_sources: x`` --
    erase the record for every configuration in the file.
    """
    if recorded is None:
        return None
    if isinstance(recorded, dict):
        return recorded.get(name)
    return recorded


def config_source_for_saved_entry(file_source: str, recorded: Any) -> str:
    """The source a saved configuration gets: ``file_source``, or worse.

    A persisted source may only ever *downgrade*, exactly as in
    :func:`clustrix.profile_manager._restored_profile_source`. That asymmetry
    is the whole security property and it is what makes writing the source
    down safe at all: if a file could raise its own trust by saying so, this
    key would be the laundering route it exists to close.

    So:

    * an untrusted recorded source is believed, whatever the file's location
      says. A configuration that came out of a working directory stays from a
      working directory after Save copies it into ``~/.clustrix``.
    * a *trusted* recorded source is ignored and the file's own location
      stands, so a repository cannot promote a configuration it ships by
      writing ``explicit-file`` beside it.
    * anything clustrix does not recognise -- a truncated file, a hand-edited
      one, an attacker's invention -- is treated as a redirect rather than
      raised on. Refusing to load would cost the user every configuration in
      the file; refusing to *trust* costs one credential release.

    **Absence is trusted here, and that is the deliberate difference from the
    profile store.** A profile store is only ever written by clustrix, so
    silence there means a version that did not record and has to fail closed.
    A configuration file is a file *users write by hand* -- moving settings
    into ``~/.clustrix/config.yml`` is the remedy ``_load_default_config``'s
    own warning names -- so silence here means "a human put this here", which
    is the trusted case and must stay trusted. That is also what keeps an
    explicit adoption available: the widget records the source when *it*
    moves a configuration, and a user who moves one themselves records
    nothing and is believed.
    """
    from .config import (
        CONFIG_SOURCES,
        CONFIG_SOURCE_REDIRECTED_CONFIG_DIR,
        UNTRUSTED_CONFIG_SOURCES,
    )

    if recorded is None:
        return file_source
    if not isinstance(recorded, str) or recorded not in CONFIG_SOURCES:
        return CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    if recorded in UNTRUSTED_CONFIG_SOURCES:
        return recorded
    return file_source


def _as_mapping(value: Any) -> Dict[str, Any]:
    """Coerce a parsed document to a mapping, discarding anything else."""
    return value if isinstance(value, dict) else {}


def load_config_from_file(file_path: Union[Path, str]) -> Dict[str, Any]:
    """Load configuration from a YAML or JSON file, tolerating a bad one.

    Returns an empty mapping for anything it cannot read or parse. That is a
    deliberate contract -- four tests pin it -- because this is the widget's
    "Load" path, where a raised exception would escape into a notebook cell
    rather than the widget's own output area. The caller reports the empty
    result to the user.

    Use `clustrix.config.load_config` when a bad file should be an error: it
    raises, and it names the offending settings.

    Always returns a *mapping*. YAML happily parses a file of prose into a bare
    string, so an unrecognised extension used to return a `str` from a function
    annotated `-> Dict[str, Any]`; every caller then had to guess.
    """
    try:
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
    except Exception:
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
