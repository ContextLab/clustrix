"""
IPython magic command and widget for Clustrix configuration management.

This module provides a %%clusterfy magic command that creates an interactive
widget for managing cluster configurations in Jupyter notebooks.
"""

import json
import yaml
from pathlib import Path

# Type hints would be used if adding type annotations in the future
import logging

try:
    from IPython.core.magic import Magics, magics_class, cell_magic
    from IPython.display import display as _display, HTML as _HTML
    import ipywidgets as _widgets  # type: ignore

    IPYTHON_AVAILABLE = True

    # Make functions available at module level for testing
    display = _display
    HTML = _HTML
    widgets = _widgets

except ImportError:
    IPYTHON_AVAILABLE = False

    # Create placeholder classes for non-notebook environments
    class Magics:  # type: ignore
        pass

    def magics_class(cls):
        return cls

    def cell_magic(name):
        def decorator(func):
            # When IPython isn't available, return the original function unchanged
            return func

        return decorator

    def display(*args, **kwargs):
        """Placeholder display function."""
        pass

    class HTML:  # type: ignore
        """Placeholder HTML class."""

        def __init__(self, *args, **kwargs):
            pass

    # Mock widgets module
    class widgets:  # type: ignore
        class Dropdown:
            def __init__(self, *args, **kwargs):
                pass

        class Button:
            def __init__(self, *args, **kwargs):
                pass

        class Text:
            def __init__(self, *args, **kwargs):
                pass

        class IntText:
            def __init__(self, *args, **kwargs):
                pass

        class Textarea:
            def __init__(self, *args, **kwargs):
                pass

        class Output:
            def __init__(self, *args, **kwargs):
                pass

            def clear_output(self, *args, **kwargs):
                pass

        class VBox:
            def __init__(self, *args, **kwargs):
                pass

        class HBox:
            def __init__(self, *args, **kwargs):
                pass

        class HTML:
            def __init__(self, *args, **kwargs):
                pass

        class Layout:
            def __init__(self, *args, **kwargs):
                pass


from .config import configure, get_config

logger = logging.getLogger(__name__)


# Default cluster configurations
DEFAULT_CONFIGS = {
    "local_dev": {
        "name": "Local Development",
        "cluster_type": "local",
        "default_cores": 4,
        "default_memory": "8GB",
        "description": "Local machine for development and testing",
    },
    "aws_gpu_small": {
        "name": "AWS GPU Small",
        "cluster_type": "ssh",
        "cluster_host": "aws-instance-ip",
        "username": "ubuntu",
        "key_file": "~/.ssh/aws_key.pem",
        "default_cores": 8,
        "default_memory": "60GB",
        "description": "AWS p3.2xlarge instance (1 V100 GPU)",
    },
    "aws_gpu_large": {
        "name": "AWS GPU Large",
        "cluster_type": "ssh",
        "cluster_host": "aws-instance-ip",
        "username": "ubuntu",
        "key_file": "~/.ssh/aws_key.pem",
        "default_cores": 32,
        "default_memory": "244GB",
        "description": "AWS p3.8xlarge instance (4 V100 GPUs)",
    },
    "gcp_cpu": {
        "name": "GCP CPU Instance",
        "cluster_type": "ssh",
        "cluster_host": "gcp-instance-ip",
        "username": "clustrix",
        "key_file": "~/.ssh/gcp_key",
        "default_cores": 16,
        "default_memory": "64GB",
        "description": "GCP n2-standard-16 instance",
    },
    "azure_gpu": {
        "name": "Azure GPU",
        "cluster_type": "ssh",
        "cluster_host": "azure-instance-ip",
        "username": "azureuser",
        "key_file": "~/.ssh/azure_key",
        "default_cores": 12,
        "default_memory": "224GB",
        "description": "Azure NC12s_v3 instance (2 V100 GPUs)",
    },
    "slurm_hpc": {
        "name": "SLURM HPC Cluster",
        "cluster_type": "slurm",
        "cluster_host": "hpc.university.edu",
        "username": "username",
        "key_file": "~/.ssh/id_rsa",
        "remote_work_dir": "/scratch/$USER/clustrix",
        "default_cores": 16,
        "default_memory": "64GB",
        "default_time": "04:00:00",
        "description": "University HPC cluster with SLURM",
    },
    "kubernetes": {
        "name": "Kubernetes Cluster",
        "cluster_type": "kubernetes",
        "kube_namespace": "default",
        "default_cores": 4,
        "default_memory": "16GB",
        "description": "Kubernetes cluster for containerized workloads",
    },
}


class ClusterConfigWidget:
    """Interactive widget for managing Clustrix configurations."""

    def __init__(self):
        if not IPYTHON_AVAILABLE:
            raise ImportError(
                "IPython and ipywidgets are required for the widget interface"
            )

        self.configs = {}
        self.current_config_name = None

        # Load default configurations
        self._load_defaults()

        # Create widget components
        self._create_widgets()

    def _load_defaults(self):
        """Load default configurations."""
        self.configs = DEFAULT_CONFIGS.copy()

    def _create_widgets(self):
        """Create the widget interface."""
        # Style
        style = {"description_width": "120px"}
        layout = widgets.Layout(width="100%")

        # Configuration selector
        self.config_selector = widgets.Dropdown(
            options=list(self.configs.keys()),
            value=list(self.configs.keys())[0] if self.configs else None,
            description="Configuration:",
            style=style,
            layout=layout,
        )
        self.config_selector.observe(self._on_config_select, names="value")

        # Buttons
        self.new_button = widgets.Button(
            description="New Config", button_style="success", icon="plus"
        )
        self.new_button.on_click(self._on_new_config)

        self.delete_button = widgets.Button(
            description="Delete Config", button_style="danger", icon="trash"
        )
        self.delete_button.on_click(self._on_delete_config)

        self.apply_button = widgets.Button(
            description="Apply Config", button_style="primary", icon="check"
        )
        self.apply_button.on_click(self._on_apply_config)

        # Configuration fields
        self.name_input = widgets.Text(description="Name:", style=style, layout=layout)

        self.cluster_type = widgets.Dropdown(
            options=["local", "ssh", "slurm", "pbs", "sge", "kubernetes"],
            description="Cluster Type:",
            style=style,
            layout=layout,
        )
        self.cluster_type.observe(self._on_cluster_type_change, names="value")

        self.cluster_host = widgets.Text(
            description="Host:",
            placeholder="hostname or IP address",
            style=style,
            layout=layout,
        )

        self.username = widgets.Text(
            description="Username:", style=style, layout=layout
        )

        self.key_file = widgets.Text(
            description="SSH Key:",
            placeholder="~/.ssh/id_rsa",
            style=style,
            layout=layout,
        )

        self.remote_work_dir = widgets.Text(
            description="Work Dir:",
            placeholder="/tmp/clustrix",
            style=style,
            layout=layout,
        )

        self.default_cores = widgets.IntText(
            value=4, description="Default Cores:", style=style, layout=layout
        )

        self.default_memory = widgets.Text(
            value="8GB", description="Default Memory:", style=style, layout=layout
        )

        self.default_time = widgets.Text(
            value="01:00:00", description="Default Time:", style=style, layout=layout
        )

        self.kube_namespace = widgets.Text(
            value="default", description="K8s Namespace:", style=style, layout=layout
        )

        self.description = widgets.Textarea(
            description="Description:", rows=3, style=style, layout=layout
        )

        # Save/Load section
        self.file_path = widgets.Text(
            value="cluster_configs.yaml",
            description="File Path:",
            style=style,
            layout=widgets.Layout(width="70%"),
        )

        self.save_button = widgets.Button(
            description="Save Configs", button_style="info", icon="save"
        )
        self.save_button.on_click(self._on_save_configs)

        self.load_button = widgets.Button(
            description="Load Configs", button_style="info", icon="folder-open"
        )
        self.load_button.on_click(self._on_load_configs)

        # Status output
        self.status_output = widgets.Output()

        # Load initial configuration
        if self.configs:
            self._load_config_to_widgets(list(self.configs.keys())[0])

    def _on_cluster_type_change(self, change):
        """Handle cluster type change to show/hide relevant fields."""
        cluster_type = change["new"]

        # Show/hide fields based on cluster type
        if cluster_type == "local":
            self.cluster_host.layout.visibility = "hidden"
            self.username.layout.visibility = "hidden"
            self.key_file.layout.visibility = "hidden"
            self.remote_work_dir.layout.visibility = "hidden"
            self.kube_namespace.layout.visibility = "hidden"
        elif cluster_type == "kubernetes":
            self.cluster_host.layout.visibility = "visible"
            self.username.layout.visibility = "hidden"
            self.key_file.layout.visibility = "hidden"
            self.remote_work_dir.layout.visibility = "visible"
            self.kube_namespace.layout.visibility = "visible"
        else:  # ssh, slurm, pbs, sge
            self.cluster_host.layout.visibility = "visible"
            self.username.layout.visibility = "visible"
            self.key_file.layout.visibility = "visible"
            self.remote_work_dir.layout.visibility = "visible"
            self.kube_namespace.layout.visibility = "hidden"

    def _load_config_to_widgets(self, config_name: str):
        """Load a configuration into the widgets."""
        if config_name not in self.configs:
            return

        config = self.configs[config_name]
        self.current_config_name = config_name

        # Set widget values
        self.name_input.value = config.get("name", config_name)
        self.cluster_type.value = config.get("cluster_type", "local")
        self.cluster_host.value = config.get("cluster_host", "")
        self.username.value = config.get("username", "")
        self.key_file.value = config.get("key_file", "")
        self.remote_work_dir.value = config.get("remote_work_dir", "/tmp/clustrix")
        self.default_cores.value = config.get("default_cores", 4)
        self.default_memory.value = config.get("default_memory", "8GB")
        self.default_time.value = config.get("default_time", "01:00:00")
        self.kube_namespace.value = config.get("kube_namespace", "default")
        self.description.value = config.get("description", "")

        # Trigger visibility update
        self._on_cluster_type_change({"new": self.cluster_type.value})

    def _save_config_from_widgets(self, config_name: str):
        """Save current widget values to a configuration."""
        config = {
            "name": self.name_input.value,
            "cluster_type": self.cluster_type.value,
            "default_cores": self.default_cores.value,
            "default_memory": self.default_memory.value,
            "default_time": self.default_time.value,
            "description": self.description.value,
        }

        # Add fields based on cluster type
        if self.cluster_type.value != "local":
            if self.cluster_type.value == "kubernetes":
                config["cluster_host"] = self.cluster_host.value
                config["remote_work_dir"] = self.remote_work_dir.value
                config["kube_namespace"] = self.kube_namespace.value
            else:
                config["cluster_host"] = self.cluster_host.value
                config["username"] = self.username.value
                config["key_file"] = self.key_file.value
                config["remote_work_dir"] = self.remote_work_dir.value

        self.configs[config_name] = config

    def _on_config_select(self, change):
        """Handle configuration selection."""
        config_name = change["new"]
        if config_name:
            self._load_config_to_widgets(config_name)

    def _on_new_config(self, button):
        """Create a new configuration."""
        with self.status_output:
            self.status_output.clear_output()

            # Generate unique name
            base_name = "new_config"
            name = base_name
            counter = 1
            while name in self.configs:
                name = f"{base_name}_{counter}"
                counter += 1

            # Create new config with defaults
            self.configs[name] = {
                "name": f"New Configuration {counter}",
                "cluster_type": "local",
                "default_cores": 4,
                "default_memory": "8GB",
                "default_time": "01:00:00",
                "description": "New cluster configuration",
            }

            # Update selector
            self.config_selector.options = list(self.configs.keys())
            self.config_selector.value = name

            print(f"✅ Created new configuration: {name}")

    def _on_delete_config(self, button):
        """Delete the current configuration."""
        with self.status_output:
            self.status_output.clear_output()

            if self.current_config_name and self.current_config_name in self.configs:
                if len(self.configs) <= 1:
                    print("❌ Cannot delete the last configuration")
                    return

                del self.configs[self.current_config_name]

                # Update selector
                self.config_selector.options = list(self.configs.keys())
                if self.configs:
                    self.config_selector.value = list(self.configs.keys())[0]

                print(f"✅ Deleted configuration: {self.current_config_name}")

    def _on_apply_config(self, button):
        """Apply the current configuration."""
        with self.status_output:
            self.status_output.clear_output()

            # Save current widget state
            if self.current_config_name:
                self._save_config_from_widgets(self.current_config_name)

                # Get config without UI-only fields
                config = self.configs[self.current_config_name].copy()
                config.pop("name", None)
                config.pop("description", None)

                # Apply configuration
                try:
                    configure(**config)
                    print(f"✅ Applied configuration: {self.current_config_name}")

                    # Show current config
                    current = get_config()
                    print("\nCurrent configuration:")
                    print(f"  Cluster Type: {current.cluster_type}")
                    if current.cluster_type != "local":
                        print(f"  Host: {current.cluster_host}")
                    print(f"  Default Cores: {current.default_cores}")
                    print(f"  Default Memory: {current.default_memory}")
                except Exception as e:
                    print(f"❌ Error applying configuration: {str(e)}")

    def _on_save_configs(self, button):
        """Save configurations to file."""
        with self.status_output:
            self.status_output.clear_output()

            # Save current widget state
            if self.current_config_name:
                self._save_config_from_widgets(self.current_config_name)

            try:
                file_path = Path(self.file_path.value)

                # Determine format from extension
                if file_path.suffix.lower() in [".yaml", ".yml"]:
                    with open(file_path, "w") as f:
                        yaml.dump(self.configs, f, default_flow_style=False)
                else:
                    with open(file_path, "w") as f:
                        json.dump(self.configs, f, indent=2)

                print(f"✅ Saved {len(self.configs)} configurations to {file_path}")
            except Exception as e:
                print(f"❌ Error saving configurations: {str(e)}")

    def _on_load_configs(self, button):
        """Load configurations from file."""
        with self.status_output:
            self.status_output.clear_output()

            try:
                file_path = Path(self.file_path.value)

                if not file_path.exists():
                    print(f"❌ File not found: {file_path}")
                    return

                # Load based on extension
                with open(file_path, "r") as f:
                    if file_path.suffix.lower() in [".yaml", ".yml"]:
                        loaded_configs = yaml.safe_load(f)
                    else:
                        loaded_configs = json.load(f)

                if not isinstance(loaded_configs, dict):
                    print("❌ Invalid configuration file format")
                    return

                self.configs = loaded_configs

                # Update selector
                self.config_selector.options = list(self.configs.keys())
                if self.configs:
                    self.config_selector.value = list(self.configs.keys())[0]

                print(f"✅ Loaded {len(self.configs)} configurations from {file_path}")
            except Exception as e:
                print(f"❌ Error loading configurations: {str(e)}")

    def display(self):
        """Display the widget interface."""
        # Title
        display(HTML("<h3>🚀 Clustrix Configuration Manager</h3>"))

        # Configuration section
        config_section = widgets.VBox(
            [
                widgets.HBox(
                    [
                        self.config_selector,
                        self.new_button,
                        self.delete_button,
                        self.apply_button,
                    ]
                ),
                widgets.HTML("<hr>"),
                self.name_input,
                self.description,
                widgets.HTML("<h4>Cluster Settings</h4>"),
                self.cluster_type,
                self.cluster_host,
                self.username,
                self.key_file,
                self.remote_work_dir,
                self.kube_namespace,
                widgets.HTML("<h4>Resource Defaults</h4>"),
                self.default_cores,
                self.default_memory,
                self.default_time,
            ]
        )

        # Save/Load section
        save_load_section = widgets.VBox(
            [
                widgets.HTML("<h4>Save/Load Configurations</h4>"),
                widgets.HBox([self.file_path, self.save_button, self.load_button]),
            ]
        )

        # Main layout
        main_layout = widgets.VBox(
            [
                config_section,
                widgets.HTML("<hr>"),
                save_load_section,
                widgets.HTML("<hr>"),
                self.status_output,
            ]
        )

        display(main_layout)


@magics_class
class ClusterfyMagics(Magics):
    """IPython magic commands for Clustrix."""

    @cell_magic
    def clusterfy(self, line, cell):
        """
        Create an interactive widget for managing Clustrix configurations.

        Usage:
            %%clusterfy

        This creates a widget interface that allows you to:
        - Define and name cluster configurations
        - Select between different configurations
        - Apply configurations to the current session
        - Save configurations to YAML/JSON files
        - Load configurations from files
        """
        if not IPYTHON_AVAILABLE:
            print("❌ This magic command requires IPython and ipywidgets")
            print("Install with: pip install ipywidgets")
            return

        # Create and display the widget
        widget = ClusterConfigWidget()
        widget.display()

        # Execute any code in the cell (if provided)
        if cell.strip():
            self.shell.run_cell(cell)


def load_ipython_extension(ipython):
    """Load the extension in IPython."""
    if IPYTHON_AVAILABLE:
        ipython.register_magic_function(
            ClusterfyMagics(ipython).clusterfy, "cell", "clusterfy"
        )
        print(
            "Clustrix notebook magic loaded. Use %%clusterfy to manage configurations."
        )
