"""
Enhanced interactive widget for managing Clustrix configurations.

This module contains the main EnhancedClusterConfigWidget class that provides
a comprehensive interface for configuring and managing cluster settings in
Jupyter notebooks.
"""

import copy
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from .notebook_magic_config import (
    CONFIG_SOURCES_KEY,
    DEFAULT_CONFIGS,
    config_name_from_document,
    config_source_for_detected_file,
    config_source_for_saved_entry,
    detect_config_files,
    load_config_from_file,
    recorded_config_source,
    validate_ip_address,
    validate_hostname,
)

try:
    from IPython.display import display, HTML
    import ipywidgets as widgets  # type: ignore

    IPYTHON_AVAILABLE = True
except ImportError:
    IPYTHON_AVAILABLE = False
    from .notebook_magic_fallback import display, HTML, widgets

from .config import (
    UNTRUSTED_CONFIG_SOURCES,
    configure,
    get_config,
    get_config_dir,
    normalize_hostname,
    set_config_source,
    strip_secret_fields,
    write_text_securely,
)

# The one implementation of "create each level 0700"; see its docstring for
# why ``mkdir(parents=True, mode=0o700)`` is not the same thing. Imported
# rather than copied so the widget and the profile store cannot drift.
from .profile_manager import _mkdir_private

logger = logging.getLogger(__name__)


def _dropped_keys(before: Dict[str, Any], after: Dict[str, Any]) -> set:
    """Names present in ``before`` that ``strip_secret_fields`` removed.

    Every dropped key is named, whatever the reason it was dropped -- a
    declared credential field, a field whose values clustrix cannot
    classify, or a key the configuration file format does not define. The
    widget's ``self.configs`` holds whatever a previously saved file
    contained, so the third case is not hypothetical, and a key silently
    vanishing from a file the user just saved is the surprise this notice
    exists to prevent.
    """
    return {key for key in before if key not in after}


class EnhancedClusterConfigWidget:
    """Enhanced interactive widget for managing Clustrix configurations."""

    def __init__(self, auto_display: bool = False):
        if not IPYTHON_AVAILABLE:
            raise ImportError(
                "IPython and ipywidgets are required for the widget interface"
            )
        self.configs: Dict[str, Dict[str, Any]] = {}
        self.current_config_name: Optional[str] = None
        self.config_files: List[Path] = []
        self.config_file_map: Dict[str, Path] = (
            {}
        )  # Maps config names to their source files
        self.auto_display = auto_display
        self.has_unsaved_changes = False
        # Said once per widget, not once per click: the save button is the
        # kind of thing a user presses repeatedly, and a repeated notice is
        # one that stops being read.
        self._announced_dropped_secrets = False
        # Initialize configurations
        self._initialize_configs()
        # Create widget components
        self._create_widgets()

    def _initialize_configs(self):
        """Initialize configurations from defaults and detected files.

        ``detect_config_files`` globs the *working directory* as well as the
        configuration directories, and for two names -- ``config.yml`` and
        ``config.yaml`` -- that the automatic search does not look at at all.
        So a file a cloned repository ships is picked up here, tainted by
        nothing and announced by nothing, and lands in ``self.configs`` as a
        plain dict. Where each one was found is recorded alongside it so that
        Apply can say so; without that the dict reached ``configure()``
        indistinguishable from something the user typed, and the cluster
        password went to whoever the repository named.

        Where it was found is not the whole answer, though, because Save
        moves files: a configuration this widget wrote into the
        configuration directory says so in the file itself, and that record
        may only ever lower the verdict this computes from the location. See
        :data:`~clustrix.notebook_magic_config.CONFIG_SOURCES_KEY` for route
        12, which is what happens when it does not.
        """
        # Start with default configurations. A *deep* copy: ``.copy()`` is
        # shallow, so the inner dicts were the module-level templates
        # themselves and every edit reached through them. Renaming a
        # built-in configuration wrote ``name`` into
        # ``notebook_magic_config.DEFAULT_CONFIGS``, and every widget
        # created afterwards in the same kernel started from the mutated
        # template -- with a name that reloaded a different configuration
        # over the user's edits. Found by two of this module's own tests
        # interfering with each other.
        self.configs = copy.deepcopy(DEFAULT_CONFIGS)
        # Where each file-derived config was found. Only written here: a
        # config the user builds or saves during the session is their own.
        self.config_source_map: Dict[str, str] = {}
        # And *which hostname* that file named, normalised. The source alone
        # is not enough to condemn what Apply is holding: see
        # ``_discovered_source_for``.
        self.config_source_host_map: Dict[str, str] = {}
        # Detect and load configuration files
        self.config_files = detect_config_files()
        for config_file in self.config_files:
            source = config_source_for_detected_file(config_file)
            # ``discovered``: nobody named this file, the scan above found it.
            # An unreadable one must not take the widget down, and must not
            # pass for an empty one either -- see ``load_config_from_file``.
            file_configs = load_config_from_file(config_file, discovered=True)
            if isinstance(file_configs, dict):
                # Provenance the file carries about its own entries, removed
                # before anything else looks at the mapping: it is clustrix's
                # record, never a configuration, and leaving it in would put
                # a configuration called ``config_sources`` in the dropdown.
                recorded = file_configs.pop(CONFIG_SOURCES_KEY, None)
                # Handle both single config and multiple configs in file
                if "cluster_type" in file_configs:
                    # Single config - use filename as config name
                    config_name = config_file.stem
                    self.configs[config_name] = file_configs
                    self.config_file_map[config_name] = config_file
                    self.config_source_map[config_name] = config_source_for_saved_entry(
                        source, recorded_config_source(recorded, config_name)
                    )
                    self.config_source_host_map[config_name] = normalize_hostname(
                        file_configs.get("cluster_host")
                    )
                else:
                    # Multiple configs
                    for raw_name, config in file_configs.items():
                        # A YAML key is not always a string, and a name that
                        # is not one cannot be sorted against the others. See
                        # ``config_name_from_document``.
                        name = config_name_from_document(raw_name)
                        if isinstance(config, dict):
                            self.configs[name] = config
                            self.config_file_map[name] = config_file
                            self.config_source_map[name] = (
                                config_source_for_saved_entry(
                                    source, recorded_config_source(recorded, name)
                                )
                            )
                            self.config_source_host_map[name] = normalize_hostname(
                                config.get("cluster_host")
                            )

    def _create_widgets(self):
        """Create the enhanced widget interface."""
        # Styles and layouts
        style = {"description_width": "120px"}
        full_layout = widgets.Layout(width="100%")
        # Configuration selector with add button
        self.config_dropdown = widgets.Dropdown(
            options=[],  # Will be populated by _update_config_dropdown
            value=None,
            description="Active Config:",
            style=style,
            layout=widgets.Layout(width="70%"),
        )
        self.config_dropdown.observe(self._on_config_select, names="value")
        self.add_config_btn = widgets.Button(
            description="+",
            tooltip="Add new configuration",
            layout=widgets.Layout(width="40px"),
            button_style="success",
        )
        self.add_config_btn.on_click(self._on_add_config)
        # Cluster type dropdown
        self.cluster_type = widgets.Dropdown(
            options=[
                "local",
                "ssh",
                "slurm",
                "huggingface",
            ],
            description="Cluster Type:",
            tooltip=(
                "Choose where to run your jobs: local machine, remote servers "
                "(SSH/SLURM), or HuggingFace Jobs"
            ),
            style=style,
            layout=full_layout,
        )
        self.cluster_type.observe(self._on_cluster_type_change, names="value")
        # Dynamic fields container
        self._create_dynamic_fields()
        # Configuration name field
        self.config_name = widgets.Text(
            description="Config Name:",
            placeholder="Enter configuration name",
            tooltip=(
                "Give this configuration a descriptive name "
                "(e.g., 'GPU Server', 'Local Testing', 'HPC Cluster')"
            ),
            style=style,
            layout=full_layout,
        )
        self.config_name.observe(self._on_config_name_change, names="value")
        # Core fields
        self.cores_field = widgets.IntText(
            description="CPU Cores:",
            value=1,
            tooltip="Number of CPU cores (-1 for all available)",
            style=style,
            layout=widgets.Layout(width="48%"),
        )
        self.memory_field = widgets.Text(
            description="Memory:",
            value="16GB",
            placeholder="e.g., 16GB, 32GB, 64GB",
            tooltip="Memory limit (e.g., 16GB, 32GB, 64GB)",
            style=style,
            layout=widgets.Layout(width="48%"),
        )
        # Time limit field (for cluster types that support it)
        self.time_field = widgets.Text(
            description="Time Limit:",
            value="01:00:00",
            placeholder="HH:MM:SS format",
            tooltip="Maximum job runtime (HH:MM:SS format)",
            style=style,
            layout=widgets.Layout(width="48%"),
        )
        # Remote work directory
        self.work_dir_field = widgets.Text(
            description="Work Directory:",
            value="~/.clustrix/jobs",
            placeholder="e.g., /scratch/username/clustrix",
            tooltip="Directory on remote cluster for job files",
            style=style,
            layout=full_layout,
        )
        # Advanced options
        self._create_advanced_options()
        # Create sections
        self._create_section_containers()
        # Create save/load section
        self._create_save_section()
        # Status output area
        self.status_output = widgets.Output()
        # Main control buttons
        self.delete_config_btn = widgets.Button(
            description="Delete Config",
            tooltip="Delete the current configuration",
            button_style="danger",
            layout=widgets.Layout(width="140px"),
        )
        self.delete_config_btn.on_click(self._on_delete_config)
        self.apply_btn = widgets.Button(
            description="Apply Config",
            tooltip="Apply this configuration as the active Clustrix config",
            button_style="primary",
            layout=widgets.Layout(width="140px"),
        )
        self.apply_btn.on_click(self._on_apply_config)
        self.test_btn = widgets.Button(
            description="Test Connection",
            tooltip="Test connectivity to the cluster",
            button_style="warning",
            layout=widgets.Layout(width="140px"),
        )
        self.test_btn.on_click(self._on_test_config)
        # Update dropdown after all widgets are created
        self._update_config_dropdown()
        # Set up change tracking after all widgets are created
        self._setup_change_tracking()

    def _update_config_dropdown(self):
        """Update the configuration dropdown with current config names.

        Rebuilding `options` makes ipywidgets re-fire the selection observer,
        which calls `_load_config_to_widgets` and overwrites whatever the user
        has typed since the last load. Renaming a configuration therefore
        discarded every edit made before the rename *and* left the widget
        showing a different profile. The rebuild is guarded so the observer
        only responds to a selection the user actually made.
        """
        self._rebuilding_dropdown = True
        try:
            self._rebuild_config_dropdown()
        finally:
            self._rebuilding_dropdown = False

    def _rebuild_config_dropdown(self):
        """The actual rebuild; always called with the observer suppressed."""
        if not self.configs:
            self.config_dropdown.options = []
            self.config_dropdown.value = None
            return
        # Sort configs: defaults first, then others alphabetically
        default_keys = sorted([k for k in self.configs.keys() if k in DEFAULT_CONFIGS])
        custom_keys = sorted(
            [k for k in self.configs.keys() if k not in DEFAULT_CONFIGS]
        )
        # Create options with visual separators
        options = []
        if default_keys:
            # Add default configs
            options.extend(default_keys)
        if custom_keys:
            # Add custom configs
            if default_keys:  # Add separator if we have both types
                pass  # ipywidgets doesn't support separators in dropdown
            options.extend(custom_keys)
        self.config_dropdown.options = options
        # Set initial selection
        if self.current_config_name and self.current_config_name in options:
            self.config_dropdown.value = self.current_config_name
        elif options:
            # Select first option and load it
            self.config_dropdown.value = options[0]
            self._load_config_to_widgets(options[0])

    #: Every mapping keyed by configuration *name*. A rename moves the
    #: configuration between keys, so each of these has to move with it or
    #: it describes a name that no longer exists -- and, worse, stops
    #: describing the configuration it was about. Named once because the
    #: cost of the list going stale is a security hole: see
    #: ``_rename_config_metadata``.
    _NAME_KEYED_MAPS = (
        "config_file_map",
        "config_source_map",
        "config_source_host_map",
    )

    def _rename_config_metadata(self, old_name: str, new_name: str) -> None:
        """Move the sidecars for ``old_name`` onto ``new_name``.

        ``_on_config_name_change`` re-keyed ``self.configs`` and nothing
        else, so renaming a configuration the widget had found on disk left
        ``config_source_map`` describing a name that no longer existed.
        ``_discovered_source_for`` looks that name up and got nothing, so
        Apply stamped no provenance and ``configure()``'s ``runtime`` stood:
        selecting a repository's ``./config.yml`` and typing a name into the
        name box -- without touching the host -- was enough to have the
        cluster password sent to the host that file named. Renaming is not
        choosing a hostname.

        An absent entry is *removed* from ``new_name`` rather than left
        alone, so the sidecars track ``self.configs`` exactly in both
        directions. Renaming a configuration the user built onto the name of
        one that came off a disk must not leave the disk's provenance
        attached to it either.
        """
        for attr in self._NAME_KEYED_MAPS:
            mapping = getattr(self, attr)
            if old_name in mapping:
                mapping[new_name] = mapping.pop(old_name)
            else:
                mapping.pop(new_name, None)

    def _carry_config_provenance(
        self,
        name: str,
        source: Optional[str],
        config_data: Dict[str, Any],
    ) -> None:
        """Attach ``source`` to ``name``, or make sure nothing is attached.

        ``source`` is what :meth:`_discovered_source_for` said about the
        fields in ``config_data`` *before* they were copied under a new name.
        A non-``None`` answer already means the host in those fields is the
        host the file named, so the host recorded here is that same host.

        The ``else`` branch is what the user typing their own hostname
        reaches, and it *clears* rather than leaves alone -- the same
        both-directions rule as :meth:`_rename_config_metadata`, so the two
        maps track ``self.configs`` exactly however this is called and a name
        can never end up carrying a file's provenance over fields that file
        never described.
        """
        if source:
            self.config_source_map[name] = source
            self.config_source_host_map[name] = normalize_hostname(
                config_data.get("cluster_host")
            )
        else:
            self.config_source_map.pop(name, None)
            self.config_source_host_map.pop(name, None)

    def _forget_config_metadata(self, name: str) -> None:
        """Drop the sidecars for a configuration that is going away."""
        for attr in self._NAME_KEYED_MAPS:
            getattr(self, attr).pop(name, None)

    def _on_config_name_change(self, change):
        """Handle changes to the config name field.

        **A rename onto a name another configuration holds is refused.** That
        is decided, not defaulted -- issue #171 offered refuse, ask and
        auto-suffix, and the two rejected options lose to how this handler is
        actually reached. It is a ``Text`` observer, so it fires on the
        keystream: a modal question has nowhere to appear and would arrive
        once per character, and auto-suffixing would silently name a
        configuration something the user never typed, which is the same
        "accepted the instruction, did something else, reported success"
        shape as the overwrite it replaces. Refusing invents nothing and
        destroys nothing.

        The refusal deliberately leaves the box holding what was typed and
        ``current_config_name`` where it was, rather than resetting the
        field. Resetting it would fight the keystream -- a user typing
        "SSH Remote Server 2" passes through the taken name on the way -- and
        because the selection does not move, the next keystroke that reaches a
        free name still renames the configuration they were editing.

        Nothing keyed by the name moves on the refused path either: the
        sidecars ``_rename_config_metadata`` maintains describe
        ``self.configs``, which is exactly what a refusal leaves alone.
        """
        new_name = change["new"].strip()
        if not new_name:
            return
        # Update the configuration if we have one loaded
        if (
            self.current_config_name
            and self.current_config_name in self.configs
            and new_name != self.current_config_name
        ):
            if new_name in self.configs:
                # Overwriting here destroyed the occupant in silence, and a
                # profile is the only place its ``password`` and ``hf_token``
                # live -- ``save_to_file`` omits both -- so there was no way
                # back from it.
                with self.status_output:
                    self.status_output.clear_output()
                    print(
                        f"❌ Cannot rename '{self.current_config_name}' to "
                        f"'{new_name}': another configuration already has "
                        "that name. Choose a different name, or delete "
                        f"'{new_name}' first."
                    )
                return
            # Rename the configuration, and everything else keyed by its
            # name along with it -- see ``_rename_config_metadata``.
            old_config = self.configs.pop(self.current_config_name)
            old_config["name"] = new_name
            self.configs[new_name] = old_config
            self._rename_config_metadata(self.current_config_name, new_name)
            self.current_config_name = new_name
            self._update_config_dropdown()

    def _create_dynamic_fields(self):
        """Create dynamic fields that change based on cluster type."""
        style = {"description_width": "120px"}
        full_layout = widgets.Layout(width="100%")
        half_layout = widgets.Layout(width="48%")
        # Host/Address field with validation
        self.host_field = widgets.Text(
            description="Host/Address:",
            placeholder="e.g., login.hpc.edu, 192.168.1.100",
            tooltip="Hostname or IP address of the cluster",
            style=style,
            layout=full_layout,
        )
        self.host_field.observe(self._validate_host, names="value")
        # Username field
        self.username_field = widgets.Text(
            description="Username:",
            placeholder="Your username on the cluster",
            tooltip="Your username for SSH/cluster login",
            style=style,
            layout=half_layout,
        )
        # Password field (if needed)
        self.password_field = widgets.Text(
            description="Password:",
            placeholder="Optional if using SSH keys",
            tooltip="Password for SSH login (leave empty to use SSH keys)",
            style=style,
            layout=half_layout,
        )
        # Port field
        self.port_field = widgets.IntText(
            description="SSH Port:",
            value=22,
            tooltip="SSH port (usually 22)",
            style=style,
            layout=half_layout,
        )
        # HuggingFace Jobs fields
        self.hf_token_field = widgets.Text(
            description="HF Token:",
            placeholder="Your HuggingFace access token",
            tooltip="HuggingFace access token for authentication",
            style=style,
            layout=full_layout,
        )
        self.hf_hardware_field = widgets.Dropdown(
            options=[
                "cpu-basic",
                "cpu-upgrade",
                "t4-small",
                "t4-medium",
                "a10g-small",
                "a10g-large",
                "a100-large",
            ],
            value="cpu-basic",
            description="Hardware:",
            tooltip="HuggingFace Jobs hardware flavor",
            style=style,
            layout=half_layout,
        )

    def _create_advanced_options(self):
        """Create advanced options accordion."""
        style = {"description_width": "120px"}
        full_layout = widgets.Layout(width="100%")
        # Package manager selection
        self.package_manager = widgets.Dropdown(
            options=["pip", "conda"],
            value="pip",
            description="Package Manager:",
            tooltip="Choose between pip and conda for dependency management",
            style=style,
            layout=widgets.Layout(width="48%"),
        )
        # Environment variables
        self.env_vars_field = widgets.Textarea(
            description="Environment Vars:",
            placeholder='{"VAR1": "value1", "VAR2": "value2"}',
            tooltip="Environment variables as JSON (optional)",
            rows=3,
            style=style,
            layout=full_layout,
        )
        # Module loads and pre-execution commands.
        #
        # Both are real ClusterConfig fields that utils.py emits into every job
        # script, and neither had a widget after the #80 refactor -- so saving
        # a profile through this widget silently erased a user's `module load`
        # lines, which on an HPC cluster is the difference between a job that
        # runs and one that cannot find its compiler.
        self.module_loads_field = widgets.Textarea(
            description="Module Loads:",
            placeholder="python/3.12\ncuda/12.1",
            tooltip="Environment modules to load, one per line",
            rows=3,
            style=style,
            layout=full_layout,
        )
        self.pre_exec_commands_field = widgets.Textarea(
            description="Pre-exec Commands:",
            placeholder="source /path/to/setup.sh",
            tooltip="Shell commands to run before the job, one per line",
            rows=3,
            style=style,
            layout=full_layout,
        )
        # Job queue/partition
        self.queue_field = widgets.Text(
            description="Queue/Partition:",
            placeholder="e.g., gpu, compute, high-mem",
            tooltip="Job queue or partition name (cluster-specific)",
            style=style,
            layout=widgets.Layout(width="48%"),
        )
        # SSH key path
        self.ssh_key_field = widgets.Text(
            description="SSH Key Path:",
            placeholder="~/.ssh/id_rsa",
            tooltip="Path to SSH private key file",
            style=style,
            layout=full_layout,
        )
        # SSH key setup button
        self.ssh_key_setup_btn = widgets.Button(
            description="Setup SSH Keys",
            tooltip="Generate and configure SSH keys for this cluster",
            button_style="info",
            layout=widgets.Layout(width="150px"),
        )
        self.ssh_key_setup_btn.on_click(self._on_setup_ssh_keys)

    def _create_save_section(self):
        """Create save configuration section."""
        style = {"description_width": "120px"}

        # Custom filename input
        self.save_filename_input = widgets.Text(
            description="Filename:",
            placeholder="Leave empty to use config name",
            tooltip="Custom filename for saving (optional)",
            style=style,
            layout=widgets.Layout(width="70%"),
        )
        # Existing files dropdown for overwriting
        self.save_file_select = widgets.Dropdown(
            options=[],
            description="Or select file:",
            tooltip="Select existing file to overwrite",
            style=style,
            layout=widgets.Layout(width="70%"),
        )
        self.save_file_select.observe(self._on_save_file_select, names="value")
        # Update the existing files list
        self._update_existing_files()
        # Save button
        self.save_btn = widgets.Button(
            description="Save Config",
            tooltip="Save configuration to file",
            button_style="success",
            layout=widgets.Layout(width="120px"),
        )
        self.save_btn.on_click(self._on_save_config)
        # Load from file section
        self.load_config_text = widgets.Textarea(
            description="Paste Config:",
            placeholder="Paste YAML or JSON configuration here to load",
            tooltip="Paste configuration content to load from clipboard",
            rows=8,
            style=style,
            layout=widgets.Layout(width="100%"),
        )
        self.load_btn = widgets.Button(
            description="Load Config",
            tooltip="Load configuration from pasted content",
            button_style="primary",
            layout=widgets.Layout(width="120px"),
        )
        self.load_btn.on_click(self._on_load_config)

    def _mark_unsaved_changes(self, change=None):
        """Mark that there are unsaved changes."""
        self.has_unsaved_changes = True

    def _clear_unsaved_changes(self):
        """Clear the unsaved changes flag."""
        self.has_unsaved_changes = False

    def _setup_change_tracking(self):
        """Set up observers to track unsaved changes."""
        # Note: config_name already has an observer, but we need to track changes too
        # We'll add a second observer for change tracking
        fields_to_track = [
            self.cluster_type,
            self.cores_field,
            self.memory_field,
            self.time_field,
            self.work_dir_field,
            self.host_field,
            self.username_field,
            self.password_field,
            self.port_field,
            self.package_manager,
            self.env_vars_field,
            self.module_loads_field,
            self.pre_exec_commands_field,
            self.queue_field,
            self.ssh_key_field,
        ]
        for field in fields_to_track:
            field.observe(self._mark_unsaved_changes, names="value")

    def _on_save_file_select(self, change):
        """Handle selection from existing files dropdown."""
        selected = change["new"]
        if selected.startswith("Overwrite: "):
            # Extract filename from "Overwrite: /path/to/file"
            file_path = selected.replace("Overwrite: ", "")
            self.save_filename_input.value = file_path

    def _create_section_containers(self):
        """Create the main UI section containers."""
        # Connection fields (dynamically shown/hidden)
        self.connection_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Connection Settings</h5>"),
                self.host_field,
                widgets.HBox([self.username_field, self.password_field]),
                widgets.HBox(
                    [self.port_field, widgets.HTML("")],
                    layout=widgets.Layout(width="100%"),
                ),
                self.ssh_key_field,
                self.ssh_key_setup_btn,
            ],
            layout=widgets.Layout(
                border="1px solid #ddd",
                padding="10px",
                margin="10px 0px",
                display="none",
            ),
        )
        self.hf_fields = widgets.VBox(
            [
                widgets.HTML("<h5>HuggingFace Jobs Settings</h5>"),
                self.hf_token_field,
                self.hf_hardware_field,
            ],
            layout=widgets.Layout(
                border="1px solid #ddd",
                padding="10px",
                margin="10px 0px",
                display="none",
            ),
        )

    def _validate_host(self, change):
        """Validate host field input."""
        value = change["new"]
        if value and not (validate_ip_address(value) or validate_hostname(value)):
            # Visual feedback for invalid input
            self.host_field.layout.border = "2px solid red"
        else:
            self.host_field.layout.border = ""

    def _on_cluster_type_change(self, change):
        """Handle cluster type change to show/hide relevant sections."""
        cluster_type = change["new"]

        # Hide all sections first
        self.connection_fields.layout.display = "none"
        self.hf_fields.layout.display = "none"

        # Show relevant sections based on cluster type
        if cluster_type in ["ssh", "slurm"]:
            self.connection_fields.layout.display = ""
        elif cluster_type == "huggingface":
            self.hf_fields.layout.display = ""

        # Update time field visibility (only for cluster schedulers)
        if cluster_type == "slurm":
            self.time_field.layout.display = ""
        else:
            self.time_field.layout.display = "none"

        # Update work directory field visibility (hide for local)
        if cluster_type == "local":
            self.work_dir_field.layout.display = "none"
        else:
            self.work_dir_field.layout.display = ""

        # Mark as changed
        self._mark_unsaved_changes()

    @staticmethod
    def _set_choice(field, value):
        """Select a value in a dropdown, widening the options if need be.

        Every one of these assignments used to be bare `field.value = ...`, so
        loading a configuration whose region or instance type was not in the
        hardcoded ten-item list raised

            TraitError: Invalid selection: value not found

        and broke the widget outright. New hardware flavors appear faster than
        the hardcoded list, so this was reachable with an ordinary config file.

        The saved configuration is authoritative -- a list baked into the UI
        should not be able to veto it -- so an unrecognised value is added to
        the options rather than discarded.
        """
        if value in (None, ""):
            return
        if value not in field.options:
            field.options = list(field.options) + [value]
        field.value = value

    def _load_config_to_widgets(self, config_name: str):
        """Load a configuration into the widgets."""
        if config_name not in self.configs:
            return
        config = self.configs[config_name]
        self.current_config_name = config_name

        # Basic fields
        self.config_name.value = config.get("name", config_name)
        self.cluster_type.value = config.get("cluster_type", "local")
        self.cores_field.value = config.get("default_cores", 1)
        self.memory_field.value = config.get("default_memory", "16GB")
        self.time_field.value = config.get("default_time", "01:00:00")
        self.work_dir_field.value = config.get("remote_work_dir", "~/.clustrix/jobs")

        # Connection fields
        self.host_field.value = config.get("cluster_host", "")
        self.username_field.value = config.get("username", "")
        self.password_field.value = config.get("password", "")
        self.port_field.value = config.get("cluster_port", 22)

        # HuggingFace Jobs fields
        self.hf_token_field.value = config.get("hf_token", "")
        self._set_choice(self.hf_hardware_field, config.get("hf_hardware", "cpu-basic"))

        # Advanced options
        self.package_manager.value = config.get("package_manager", "pip")

        # Environment variables
        env_vars = config.get("environment_variables", {})
        if env_vars:
            import json

            self.env_vars_field.value = json.dumps(env_vars, indent=2)
        else:
            self.env_vars_field.value = ""

        self.module_loads_field.value = "\n".join(config.get("module_loads", []) or [])
        self.pre_exec_commands_field.value = "\n".join(
            config.get("pre_execution_commands", []) or []
        )

        self.queue_field.value = config.get("queue", "")
        self.ssh_key_field.value = config.get("ssh_key_path", "")

        # Trigger cluster type change to show/hide relevant fields
        self._on_cluster_type_change({"new": self.cluster_type.value})

        # Clear unsaved changes flag since we just loaded
        self._clear_unsaved_changes()

    def _save_config_from_widgets(self) -> Dict[str, Any]:
        """Save current widget values to a configuration dict."""
        config = {
            "name": self.config_name.value,
            "cluster_type": self.cluster_type.value,
            "default_cores": self.cores_field.value,
            "default_memory": self.memory_field.value,
            "default_time": self.time_field.value,
            "remote_work_dir": self.work_dir_field.value,
            "cluster_host": self.host_field.value,
            "username": self.username_field.value,
            "cluster_port": self.port_field.value,
            "package_manager": self.package_manager.value,
            "queue": self.queue_field.value,
            "ssh_key_path": self.ssh_key_field.value,
        }

        # Include password only if provided
        if self.password_field.value:
            config["password"] = self.password_field.value

        # HuggingFace Jobs specific fields
        if self.cluster_type.value == "huggingface":
            config.update(
                {
                    "hf_hardware": self.hf_hardware_field.value,
                }
            )
            if self.hf_token_field.value:
                config["hf_token"] = self.hf_token_field.value

        # Environment variables
        if self.env_vars_field.value.strip():
            try:
                import json

                env_vars = json.loads(self.env_vars_field.value)
                if env_vars:
                    config["environment_variables"] = env_vars
            except json.JSONDecodeError:
                pass  # Ignore invalid JSON

        # One entry per line, blanks dropped.
        for field, key in (
            (self.module_loads_field, "module_loads"),
            (self.pre_exec_commands_field, "pre_execution_commands"),
        ):
            entries = [
                line.strip() for line in field.value.splitlines() if line.strip()
            ]
            if entries:
                config[key] = entries

        # Remove empty string values
        config = {k: v for k, v in config.items() if v != ""}
        return config

    def _on_config_select(self, change):
        """Handle configuration selection from dropdown."""
        if getattr(self, "_rebuilding_dropdown", False):
            # Fired by _update_config_dropdown rebuilding options, not by the
            # user picking something. Reloading here would discard their edits.
            return
        config_name = change["new"]
        if config_name:
            self._load_config_to_widgets(config_name)

    def _on_add_config(self, button):
        """Add a new configuration based on current values."""
        with self.status_output:
            self.status_output.clear_output()
            # Generate unique name
            base_name = "New Configuration"
            config_name = base_name
            counter = 1
            while config_name in self.configs:
                config_name = f"{base_name} {counter}"
                counter += 1

            # Save current widget state as new config.
            #
            # The provenance of those *live fields* has to be read while
            # ``current_config_name`` still names the configuration they came
            # from -- ``_discovered_source_for`` keys off it -- and then
            # carried onto the new name. Copying moved the fields and left
            # the sidecars behind, so ``_discovered_source_for`` returned
            # ``None`` under the new name and Apply's ``configure()`` stamped
            # ``runtime``: selecting a repository's ``./config.yml`` and
            # pressing "+" was enough to have the cluster password sent to
            # the host that file named. Measured on the wire. Copying a
            # configuration is not choosing a hostname, exactly as renaming
            # one is not (see ``_rename_config_metadata``).
            #
            # ``config_file_map`` deliberately does *not* come along,
            # because the copy is a new configuration that no file holds.
            #
            # The earlier reason given here -- that the map "decides which
            # entries a save writes back, not who may receive a credential"
            # -- was wrong, and route 12 is what falsifies it: what a save
            # writes, and under what name, *is* a credential decision one
            # restart later. So the exclusion rests on the fact rather than
            # on the category. It is inert today as well, since the names
            # generated here can never collide with ``DEFAULT_CONFIGS`` and
            # that collision is the only thing the map decides -- but
            # "inert today" is how routes 11 and 12 both started, so it is
            # pinned by a test rather than by this comment.
            config_data = self._save_config_from_widgets()
            discovered_source = self._discovered_source_for(config_data)
            config_data["name"] = config_name
            self.configs[config_name] = config_data
            self.current_config_name = config_name
            self._carry_config_provenance(config_name, discovered_source, config_data)

            # Update UI
            self.config_name.value = config_name
            self._update_config_dropdown()
            print(f"✅ Added new configuration: '{config_name}'")

    def _on_delete_config(self, button):
        """Delete the current configuration."""
        with self.status_output:
            self.status_output.clear_output()
            if self.current_config_name in DEFAULT_CONFIGS:
                print("❌ Cannot delete default configurations")
                return
            if len(self.configs) <= 1:
                print("❌ Cannot delete the last configuration")
                return
            if self.current_config_name and self.current_config_name in self.configs:
                deleted_name = self.current_config_name
                del self.configs[self.current_config_name]
                # And everything keyed by its name: leaving the provenance
                # behind would attach a deleted file's source to whatever is
                # created under that name next.
                self._forget_config_metadata(self.current_config_name)
                # Select a different configuration
                remaining_configs = list(self.configs.keys())
                if remaining_configs:
                    self._load_config_to_widgets(remaining_configs[0])
                self._update_config_dropdown()
                print(f"✅ Deleted configuration: '{deleted_name}'")

    def _discovered_source_for(self, config_data: Dict[str, Any]) -> Optional[str]:
        """The provenance Apply may stamp on ``config_data``, or ``None``.

        ``config_source_map`` is keyed by configuration *name*, but Apply
        stamps ``_save_config_from_widgets()`` -- the **live** fields. Those
        stop being the same thing the moment the user edits one, and the
        difference is not cosmetic: with an attacker's ``./config.yml``
        present, a user who selected it, typed *their own* hostname over the
        host field and pressed Apply had their own cluster recorded in
        ``clustrix.config._HOSTS_NAMED_BY_UNTRUSTED_SOURCES``. That record has
        no way back -- it is deliberately proof against ``configure()``, since
        Apply *is* a ``configure()`` call -- so one keystroke cost them their
        cluster for the life of the kernel.

        The rule the false-refusal work established is the fix: **a hostname
        is only condemned by a source that actually named it.** So the
        discovered source applies only while the host in the widget is still
        the host that file gave, compared with the one normaliser
        (:func:`clustrix.config.normalize_hostname`) so that case and a
        trailing dot cannot be used to slip past it.

        Editing any *other* field -- cores, memory, the working directory --
        leaves the hostname untouched and so leaves the refusal in place,
        which is right: it is the host that receives the credential.
        """
        return self._source_still_naming_host(
            self.current_config_name or "", config_data
        )

    def _source_still_naming_host(
        self, name: str, config_data: Dict[str, Any]
    ) -> Optional[str]:
        """``name``'s discovered source, while it still names *this* host.

        The rule :meth:`_discovered_source_for` documents, stated once so
        that it can be asked about a configuration other than the selected
        one. ``_record_discovered_sources`` needs exactly that: a save writes
        every entry in the dropdown, and each of them has to be judged
        against the host it is *being written with*, not against the host it
        arrived with.
        """
        source = self.config_source_map.get(name)
        if not source:
            return None
        if normalize_hostname(
            config_data.get("cluster_host")
        ) != self.config_source_host_map.get(name, ""):
            return None
        return source

    def _on_apply_config(self, button):
        """Apply the current configuration."""
        with self.status_output:
            self.status_output.clear_output()
            try:
                # Save current state
                config_data = self._save_config_from_widgets()
                # Update the config in our dictionary
                if self.current_config_name:
                    self.configs[self.current_config_name] = config_data
                # Apply to Clustrix. ``configure`` means "the user typed
                # this" and records ``runtime``, which is true of anything
                # built in this session and false of a file the widget found
                # by globbing. For those, the provenance recorded at
                # discovery replaces it -- the hostname came off a disk, and
                # a round trip through a function call is not evidence that
                # anybody chose it.
                configure(**config_data)
                discovered_source = self._discovered_source_for(config_data)
                if discovered_source:
                    set_config_source(get_config(), discovered_source)
                print("✅ Configuration applied successfully!")

                # Show current config summary
                print("\n📋 Active configuration:")
                print(f"   • Name: {config_data.get('name', 'Unnamed')}")
                print(f"   • Type: {config_data.get('cluster_type', 'local')}")
                print(f"   • Cores: {config_data.get('default_cores', 1)}")
                print(f"   • Memory: {config_data.get('default_memory', '16GB')}")
                if config_data.get("cluster_host"):
                    print(f"   • Host: {config_data.get('cluster_host')}")
                print("\n💡 You can now use @cluster decorator in your code!")

                # Clear unsaved changes
                self._clear_unsaved_changes()

            except Exception as e:
                print(f"❌ Error applying configuration: {str(e)}")

    def _redact_for_save(
        self, save_data: Dict[str, Any], single_config: bool
    ) -> Dict[str, Any]:
        """Return ``save_data`` with every credential removed, saying so once.

        The same decision ``ProfileManager`` makes, for the same three
        reasons, so that clustrix has one answer rather than two: the save
        fires from a button press in the middle of ordinary editing rather
        than from a user asking to persist a secret; one file holds every
        configuration in the dropdown, so a single leak is N credentials;
        and ``password_env_var`` is the supported way to supply a password
        without writing it down. There is deliberately no ``include_secrets``
        opt-in here, because the widget offers no place to ask for one.

        The user is told what was withheld -- once per widget -- because
        silently discarding a password they just typed, and then failing to
        connect after a restart, is its own surprise.
        """
        if single_config:
            redacted = strip_secret_fields(save_data)
            dropped = _dropped_keys(save_data, redacted)
        else:
            redacted = {}
            dropped = set()
            for config_name, entry in save_data.items():
                redacted[config_name] = strip_secret_fields(entry)
                dropped |= _dropped_keys(entry, redacted[config_name])

        if dropped and not self._announced_dropped_secrets:
            self._announced_dropped_secrets = True
            print(
                "⚠️  Configuration files are not a credential store: "
                f"{', '.join(sorted(dropped))} were not written to disk and "
                "will not survive a restart. They still work for the rest of "
                "this session. To supply a password without writing it to "
                "disk, set password_env_var to the name of an environment "
                "variable holding it. environment_variables is withheld for "
                "the same reason: its names and values are yours, so clustrix "
                "cannot tell a setting from a token and does not guess -- set "
                "them in the shell that starts the notebook instead."
            )
        return redacted

    def _record_discovered_sources(
        self, save_data: Dict[str, Any], single_config: bool
    ) -> Dict[str, Any]:
        """Write the untrusted sources into the file, or leave it unchanged.

        Route 12. Save writes into :func:`get_config_dir`, and
        :func:`detect_config_files` infers trust from that directory, so
        pressing Save promoted a configuration a repository shipped to one
        the user chose -- not in this session, where every sidecar stayed
        correct, but in the next one, where the only evidence left was where
        the file now sat. See
        :data:`~clustrix.notebook_magic_config.CONFIG_SOURCES_KEY`.

        Only untrusted sources are written. A trusted one would be re-derived
        identically from the file's own location, and a record that could
        raise trust is the laundering route this is here to close -- so the
        key never carries one, and
        :func:`~clustrix.notebook_magic_config.config_source_for_saved_entry`
        would ignore it if it did.

        And only while the source still names the host being written, which
        is :meth:`_source_still_naming_host` -- the same rule Apply applies
        in :meth:`_discovered_source_for`. Keying off ``config_source_map``
        alone made the two disagree across the restart: typing your own
        hostname over a found configuration applied as ``runtime`` in the
        session and came back ``working-directory`` in the next one, because
        the write side condemned a host the file had never named. That errs
        safe, so it was a wrong answer rather than a leak; a hostname is only
        condemned by a source that actually named it, on both sides of the
        process boundary or on neither.

        The single-configuration shape has no room for a sibling key without
        the record becoming a configuration field, so a save that has
        something to record uses the nested shape instead. That is the
        widget's own other shape and it reads both; the flat one is reserved
        for files that record nothing, which is every file a user writes by
        hand.
        """
        if single_config and self.current_config_name:
            entries = {self.current_config_name: save_data}
        else:
            entries = dict(save_data)
        recorded = {}
        for name in sorted(entries):
            source = self._source_still_naming_host(name, entries[name])
            if source in UNTRUSTED_CONFIG_SOURCES:
                recorded[name] = source
        if not recorded:
            return save_data
        if single_config and self.current_config_name:
            save_data = {self.current_config_name: save_data}
        return {**save_data, CONFIG_SOURCES_KEY: recorded}

    def _on_save_config(self, button):
        """Save configuration to file."""
        with self.status_output:
            self.status_output.clear_output()
            try:
                # Save current widget state
                config_data = self._save_config_from_widgets()
                if self.current_config_name:
                    self.configs[self.current_config_name] = config_data

                # Determine filename
                if self.save_filename_input.value:
                    filename = self.save_filename_input.value
                else:
                    # Use config name as filename
                    safe_name = (
                        config_data.get("name", "config").replace(" ", "_").lower()
                    )
                    filename = f"{safe_name}.yml"

                # Ensure .yml extension
                if not filename.endswith((".yml", ".yaml")):
                    filename += ".yml"

                # Determine save directory. Every level is created 0700:
                # ``mkdir(exist_ok=True)`` left ~/.clustrix at 0755, and
                # traversing it is enough to reach a file inside by name.
                save_dir = get_config_dir()
                _mkdir_private(save_dir)
                file_path = save_dir / filename

                # Prepare data to save
                single_config = len(self.configs) == 1 and self.current_config_name
                if single_config:
                    # Single config - save just the config data
                    save_data = config_data
                else:
                    # Multiple configs - save all but only include non-default or modified defaults
                    save_data = {}
                    # Include all configurations from the widget dropdown
                    for config_name, config_data in self.configs.items():
                        # Skip unmodified default configs unless they came from a file
                        if config_name in DEFAULT_CONFIGS:
                            # Include if it came from a file (in config_file_map)
                            if config_name in self.config_file_map:
                                save_data[config_name] = config_data
                            else:
                                # Check if the default config has been modified
                                default_config = DEFAULT_CONFIGS[config_name]
                                if config_data != default_config:
                                    save_data[config_name] = config_data
                                # Skip unmodified defaults
                        else:
                            # Always include non-default configurations
                            save_data[config_name] = config_data

                # Save to file, without the credentials and 0600 from the
                # instant the file exists.
                import yaml

                save_data = self._redact_for_save(save_data, bool(single_config))
                save_data = self._record_discovered_sources(
                    save_data, bool(single_config)
                )
                write_text_securely(
                    file_path,
                    yaml.dump(save_data, default_flow_style=False, sort_keys=False),
                )

                print(f"✅ Configuration saved to: {file_path}")

                # Update file mapping
                if self.current_config_name:
                    self.config_file_map[self.current_config_name] = file_path

                # Update existing files dropdown
                self._update_existing_files()

                # Clear unsaved changes
                self._clear_unsaved_changes()

            except Exception as e:
                print(f"❌ Error saving configuration: {str(e)}")

    def _update_existing_files(self):
        """Update the existing files dropdown."""
        try:
            config_dirs = [get_config_dir(), Path(".")]
            existing_files = []
            for config_dir in config_dirs:
                if config_dir.exists():
                    for ext in ["*.yml", "*.yaml"]:
                        existing_files.extend(config_dir.glob(ext))

            if existing_files:
                # Create options with "Overwrite: " prefix
                file_options = [f"Overwrite: {str(f)}" for f in existing_files]
                self.save_file_select.options = [""] + file_options
            else:
                self.save_file_select.options = [""]
        except Exception:
            self.save_file_select.options = [""]

    def _on_load_config(self, button):
        """Load configuration from pasted content."""
        with self.status_output:
            self.status_output.clear_output()

            # Check if there's content pasted
            content = self.load_config_text.value.strip()
            if not content:
                print("❌ Please paste configuration content first")
                return

            try:
                # Try to parse as YAML first, then JSON
                import yaml
                import json

                try:
                    data = yaml.safe_load(content)
                except yaml.YAMLError:
                    try:
                        data = json.loads(content)
                    except json.JSONDecodeError:
                        print("❌ Invalid YAML or JSON format")
                        return

                if not isinstance(data, dict):
                    print("❌ Configuration must be a dictionary/object")
                    return

                # Check if this is a single config or multiple configs
                if "cluster_type" in data:
                    # Single configuration
                    config_name = config_name_from_document(
                        data.get("name", "Loaded Configuration")
                    )
                    self.configs[config_name] = data
                    self.current_config_name = config_name
                    self._load_config_to_widgets(config_name)
                    self._update_config_dropdown()
                    print(f"✅ Loaded configuration: '{config_name}'")
                else:
                    # Multiple configurations. The first *loaded* one is
                    # selected, not the first key of the pasted document:
                    # a document whose first entry is not a configuration --
                    # a comment key, a version marker, a typo -- left
                    # ``current_config_name`` naming something that is not in
                    # ``self.configs`` at all. ``_load_config_to_widgets``
                    # returns early on a name it does not know, so nothing
                    # corrected it until ``_update_config_dropdown`` happened
                    # to select something else. That self-heal is a
                    # coincidence of ordering, and every leak in this file so
                    # far has been a name and the thing keyed by it
                    # disagreeing.
                    loaded_count = 0
                    first_config = None
                    for raw_name, config in data.items():
                        name = config_name_from_document(raw_name)
                        if isinstance(config, dict) and "cluster_type" in config:
                            config["name"] = name
                            self.configs[name] = config
                            loaded_count += 1
                            if first_config is None:
                                first_config = name

                    if first_config is not None:
                        self.current_config_name = first_config
                        self._load_config_to_widgets(first_config)
                        self._update_config_dropdown()
                        print(f"✅ Loaded {loaded_count} configurations")
                    else:
                        print("❌ No valid configurations found")

                # Clear the text area
                self.load_config_text.value = ""

            except Exception as e:
                print(f"❌ Error loading configuration: {str(e)}")

    def _test_remote_connectivity(self, host, port, timeout=5):
        """Test basic network connectivity to a remote host."""
        import socket

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _test_ssh_connectivity(self, config, timeout=10):
        """Test SSH connectivity with provided credentials."""
        try:
            import paramiko

            from .ssh_security import configure_host_key_policy

            ssh_client = paramiko.SSHClient()
            configure_host_key_policy(ssh_client, config)

            # Connection parameters
            connect_params = {
                "hostname": config.get("cluster_host"),
                "username": config.get("username"),
                "port": config.get("cluster_port", 22),
                "timeout": timeout,
            }

            # Add authentication
            if config.get("password"):
                connect_params["password"] = config["password"]
            elif config.get("ssh_key_path"):
                key_path = Path(config["ssh_key_path"]).expanduser()
                if key_path.exists():
                    connect_params["key_filename"] = str(key_path)

            ssh_client.connect(**connect_params)

            # Test basic command
            stdin, stdout, stderr = ssh_client.exec_command("echo 'test'")
            result = stdout.read().decode().strip()

            ssh_client.close()
            return result == "test"

        except ImportError:
            return False, "paramiko not installed"
        except Exception as e:
            return False, str(e)

    def _test_huggingface_connectivity(self, config):
        """Test HuggingFace Jobs API connectivity."""
        try:
            import requests

            token = config.get("hf_token")
            if not token:
                return False, "Missing required fields: hf_token"

            headers = {"Authorization": f"Bearer {token}"}

            # Test API connectivity by getting user info
            response = requests.get(
                "https://huggingface.co/api/whoami", headers=headers
            )

            if response.status_code == 200:
                user_info = response.json()
                username = user_info.get("name", "Unknown")
                return True, f"HuggingFace connectivity successful (user: {username})"
            else:
                return False, f"HuggingFace API error: {response.status_code}"

        except ImportError:
            return False, "requests not installed. Run: pip install requests"
        except Exception as e:
            return False, f"HuggingFace connectivity failed: {str(e)}"

    def _on_test_config(self, button):
        """Test the current configuration."""
        with self.status_output:
            self.status_output.clear_output()

            try:
                config_data = self._save_config_from_widgets()
                cluster_type = config_data.get("cluster_type", "local")

                print(f"🔍 Testing {cluster_type} configuration...")

                if cluster_type == "local":
                    print("✅ Local configuration - no connectivity test needed")
                    print("💡 Tip: Use cores=-1 to use all available CPU cores")

                elif cluster_type in ["ssh", "slurm"]:
                    # Test SSH-based clusters
                    host = config_data.get("cluster_host")
                    port = config_data.get("cluster_port", 22)

                    if not host:
                        print("❌ Host/address is required for SSH-based clusters")
                        return

                    print(f"🌐 Testing network connectivity to {host}:{port}...")
                    if not self._test_remote_connectivity(host, port):
                        print(f"❌ Cannot reach {host}:{port}")
                        print("💡 Check if the hostname/IP is correct and accessible")
                        return

                    print("✅ Network connectivity successful")

                    # Test SSH if credentials provided
                    username = config_data.get("username")
                    if username:
                        print(f"🔐 Testing SSH login as {username}...")
                        ssh_result = self._test_ssh_connectivity(config_data)

                        if isinstance(ssh_result, tuple):
                            success, error = ssh_result
                            if success:
                                print("✅ SSH connectivity successful")
                            else:
                                print(f"❌ SSH failed: {error}")
                                print(
                                    "💡 Check username/password or SSH key configuration"
                                )
                        else:
                            if ssh_result:
                                print("✅ SSH connectivity successful")
                            else:
                                print("❌ SSH connectivity failed")
                                print(
                                    "💡 Check username/password or SSH key configuration"
                                )
                    else:
                        print("⚠️  Username not provided - skipping SSH test")

                elif cluster_type == "huggingface":
                    # Test HuggingFace Jobs API connectivity
                    print("☁️  Testing HuggingFace Jobs API connectivity...")
                    result = self._test_huggingface_connectivity(config_data)

                    if isinstance(result, tuple):
                        success, message = result
                        if success:
                            print(f"✅ {message}")
                        else:
                            print(f"❌ {message}")
                    else:
                        if result:
                            print(f"✅ {cluster_type.upper()} connectivity successful")
                        else:
                            print(f"❌ {cluster_type.upper()} connectivity failed")

                else:
                    print(
                        f"❓ Testing not implemented for cluster type: {cluster_type}"
                    )

                print("\n🎉 Configuration test completed!")

            except Exception as e:
                print(f"❌ Test failed with error: {str(e)}")

    def _on_setup_ssh_keys(self, button):
        """Set up SSH keys for the current configuration."""
        with self.status_output:
            self.status_output.clear_output()

            try:
                config_data = self._save_config_from_widgets()
                cluster_type = config_data.get("cluster_type", "local")

                if cluster_type not in ["ssh", "slurm"]:
                    print(f"❌ SSH key setup not applicable for {cluster_type}")
                    return

                host = config_data.get("cluster_host")
                username = config_data.get("username")

                if not host or not username:
                    print("❌ Host and username are required for SSH key setup")
                    return

                print("🔐 SSH Key Setup Guide")
                print("=" * 50)
                print()
                print("1. Generate SSH key pair (if you don't have one):")
                print("   ssh-keygen -t rsa -b 4096 -C 'your_email@example.com'")
                print("   (Press Enter to accept default location ~/.ssh/id_rsa)")
                print()
                print("2. Copy your public key to the cluster:")
                print(f"   ssh-copy-id {username}@{host}")
                print()
                print("   OR manually copy the key:")
                print("   cat ~/.ssh/id_rsa.pub")
                print(
                    "   (Copy the output and paste it in ~/.ssh/authorized_keys on the remote server)"
                )
                print()
                print("3. Test the connection:")
                print(f"   ssh {username}@{host}")
                print()
                print("4. Update the SSH key path in this configuration:")
                print("   Default: ~/.ssh/id_rsa")
                print("   Custom: specify full path to your private key")
                print()
                print("💡 Tips:")
                print("   • Use ssh-agent to avoid entering passphrases repeatedly")
                print("   • Consider using ~/.ssh/config for complex setups")
                print("   • Some clusters may require specific key algorithms")

                # Auto-populate SSH key path if empty
                if not self.ssh_key_field.value:
                    self.ssh_key_field.value = "~/.ssh/id_rsa"
                    print()
                    print("✅ SSH key path set to default: ~/.ssh/id_rsa")

            except Exception as e:
                print(f"❌ Error setting up SSH keys: {str(e)}")

    def display(self):
        """Display the enhanced widget interface."""
        # Title
        title_text = "Clustrix Configuration Manager"
        display(HTML(f"<h3>{title_text}</h3>"))
        # Configuration selector section
        config_section = widgets.VBox(
            [
                widgets.HTML("<h4>🔧 Configuration Management</h4>"),
                widgets.HBox([self.config_dropdown, self.add_config_btn]),
                widgets.HBox([self.config_name]),
            ]
        )
        # Basic settings section
        basic_section = widgets.VBox(
            [
                widgets.HTML("<h4>⚙️ Basic Settings</h4>"),
                self.cluster_type,
                widgets.HBox([self.cores_field, self.memory_field]),
                widgets.HBox([self.time_field, widgets.HTML("")]),
                self.work_dir_field,
            ]
        )
        # Dynamic sections (shown/hidden based on cluster type)
        dynamic_sections = widgets.VBox(
            [
                self.connection_fields,
                self.hf_fields,
            ]
        )
        # Advanced options accordion
        advanced_content = widgets.VBox(
            [
                widgets.HBox([self.package_manager, widgets.HTML("")]),
                self.env_vars_field,
                self.module_loads_field,
                self.pre_exec_commands_field,
                widgets.HBox([self.queue_field, widgets.HTML("")]),
            ]
        )
        advanced_accordion = widgets.Accordion([advanced_content])
        advanced_accordion.set_title(0, "🚀 Advanced Options")
        # Save/Load section
        save_load_content = widgets.VBox(
            [
                widgets.HTML("<h5>💾 Save Configuration</h5>"),
                self.save_filename_input,
                self.save_file_select,
                widgets.HBox([self.save_btn, widgets.HTML("")]),
                widgets.HTML("<h5>📂 Load from Clipboard</h5>"),
                self.load_config_text,
                widgets.HBox([self.load_btn, widgets.HTML("")]),
            ]
        )
        save_load_accordion = widgets.Accordion([save_load_content])
        save_load_accordion.set_title(0, "💾 Save & Load")
        # Control buttons
        control_section = widgets.VBox(
            [
                widgets.HTML("<h4>🎮 Actions</h4>"),
                widgets.HBox(
                    [
                        self.apply_btn,
                        self.test_btn,
                        self.delete_config_btn,
                    ]
                ),
            ]
        )
        # Status output
        status_section = widgets.VBox(
            [
                widgets.HTML("<h4>📋 Status & Output</h4>"),
                self.status_output,
            ]
        )
        # Main layout
        main_layout = widgets.VBox(
            [
                config_section,
                basic_section,
                dynamic_sections,
                advanced_accordion,
                save_load_accordion,
                control_section,
                status_section,
            ]
        )
        display(main_layout)
        # Auto-load first config if available
        if self.configs and not self.current_config_name:
            first_config = next(iter(self.configs.keys()))
            self._load_config_to_widgets(first_config)
