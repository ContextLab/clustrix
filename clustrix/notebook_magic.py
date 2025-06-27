"""
IPython magic command and widget for Clustrix configuration management.
This module provides a %%clusterfy magic command that creates an interactive
widget for managing cluster configurations in Jupyter notebooks. The widget
also displays automatically when clustrix is imported in a notebook environment.
"""

import json
import yaml
import re
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

try:
    from IPython.core.magic import Magics, magics_class, cell_magic
    from IPython.display import display as _display, HTML as _HTML
    import ipywidgets as _widgets
    from IPython import get_ipython

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
        def decorator(*args, **kwargs):
            # If this is being used as a decorator (first call with just the function)
            if len(args) == 1 and callable(args[0]) and len(kwargs) == 0:
                func = args[0]

                # Return a wrapper that can handle method calls
                def method_wrapper(self, line="", cell=""):
                    return func(self, line, cell)

                method_wrapper.__name__ = getattr(func, "__name__", "clusterfy")
                method_wrapper.__doc__ = getattr(func, "__doc__", "")
                method_wrapper._original = func
                return method_wrapper
            # If this is being called as a method (self, line, cell)
            else:
                # This means the decorator was bound as a method and is being called
                # In this case, we need to find the original function and call it
                # But since we can't access it here, we'll just simulate the behavior
                if not IPYTHON_AVAILABLE:
                    print("❌ This magic command requires IPython and ipywidgets")
                    print("Install with: pip install ipywidgets")
                    return None
                # If IPython is available, this shouldn't happen
                return None

        return decorator

    def display(*args, **kwargs):
        """Placeholder display function."""
        pass

    def get_ipython():
        return None

    class HTML:  # type: ignore
        """Placeholder HTML class."""

        def __init__(self, *args, **kwargs):
            pass

    # Mock widgets module
    class widgets:  # type: ignore
        class Layout:
            def __init__(self, *args, **kwargs):
                self.display = ""
                self.border = ""
                for key, value in kwargs.items():
                    setattr(self, key, value)

        class Dropdown:
            def __init__(self, *args, **kwargs):
                self.value = kwargs.get("value")
                self.options = kwargs.get("options", [])
                self.layout = widgets.Layout()

            def observe(self, *args, **kwargs):
                pass

        class Button:
            def __init__(self, *args, **kwargs):
                self.layout = widgets.Layout()

            def on_click(self, *args, **kwargs):
                pass

        class Text:
            def __init__(self, *args, **kwargs):
                self.value = kwargs.get("value", "")
                self.layout = widgets.Layout()

            def observe(self, *args, **kwargs):
                pass

        class IntText:
            def __init__(self, *args, **kwargs):
                self.value = kwargs.get("value", 0)
                self.layout = widgets.Layout()

            def observe(self, *args, **kwargs):
                pass

        class Textarea:
            def __init__(self, *args, **kwargs):
                self.value = kwargs.get("value", "")
                self.layout = widgets.Layout()

            def observe(self, *args, **kwargs):
                pass

        class Output:
            def __init__(self, *args, **kwargs):
                self.layout = widgets.Layout()

            def clear_output(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        class VBox:
            def __init__(self, *args, **kwargs):
                self.children = args[0] if args else []
                self.layout = widgets.Layout()

        class HBox:
            def __init__(self, *args, **kwargs):
                self.children = args[0] if args else []
                self.layout = widgets.Layout()

        class HTML:
            def __init__(self, *args, **kwargs):
                self.value = args[0] if args else ""
                self.layout = widgets.Layout()

        class Accordion:
            def __init__(self, *args, **kwargs):
                self.children = args[0] if args else []
                self.selected_index = None
                self.layout = widgets.Layout()

            def set_title(self, *args, **kwargs):
                pass


from .config import configure, get_config

logger = logging.getLogger(__name__)
# Default cluster configurations
DEFAULT_CONFIGS = {
    "local": {
        "name": "Local Development",
        "cluster_type": "local",
        "default_cores": 4,
        "default_memory": "8GB",
        "description": "Local machine for development and testing",
    },
    "local_multicore": {
        "name": "Local Multi-core",
        "cluster_type": "local",
        "default_cores": -1,  # Use all available cores
        "default_memory": "16GB",
        "description": "Local machine using all available cores",
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
        dir_path = Path(dir_path).expanduser()
        if dir_path.exists() and dir_path.is_dir():
            for config_name in config_names:
                config_path = dir_path / config_name
                if config_path.exists() and config_path.is_file():
                    config_files.append(config_path)
    return config_files


def load_config_from_file(file_path: Path) -> Dict[str, Any]:
    """Load configuration from a YAML or JSON file."""
    try:
        with open(file_path, "r") as f:
            if file_path.suffix.lower() in [".yml", ".yaml"]:
                return yaml.safe_load(f) or {}
            else:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load config from {file_path}: {e}")
        return {}


def validate_ip_address(ip: str) -> bool:
    """Validate IP address format."""
    if not ip:
        return False
    # IPv4 validation
    parts = ip.split(".")
    if len(parts) == 4:
        try:
            for part in parts:
                num = int(part)
                if not (0 <= num <= 255):
                    return False
            return True
        except ValueError:
            return False
    # Simple IPv6 pattern check
    ipv6_pattern = re.compile(r"^([0-9a-fA-F]{0,4}:){7}[0-9a-fA-F]{0,4}$")
    return bool(ipv6_pattern.match(ip))


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


class EnhancedClusterConfigWidget:
    """Enhanced interactive widget for managing Clustrix configurations."""

    def __init__(self, auto_display: bool = False):
        if not IPYTHON_AVAILABLE:
            raise ImportError(
                "IPython and ipywidgets are required for the widget interface"
            )
        self.configs = {}
        self.current_config_name = None
        self.config_files = []
        self.config_file_map = {}  # Maps config names to their source files
        self.auto_display = auto_display
        # Initialize configurations
        self._initialize_configs()
        # Create widget components
        self._create_widgets()

    def _initialize_configs(self):
        """Initialize configurations from defaults and detected files."""
        # Start with default configurations
        self.configs = DEFAULT_CONFIGS.copy()
        # Detect and load configuration files
        self.config_files = detect_config_files()
        for config_file in self.config_files:
            file_configs = load_config_from_file(config_file)
            if isinstance(file_configs, dict):
                # Handle both single config and multiple configs in file
                if "cluster_type" in file_configs:
                    # Single config - use filename as config name
                    config_name = config_file.stem
                    self.configs[config_name] = file_configs
                    self.config_file_map[config_name] = config_file
                else:
                    # Multiple configs
                    for name, config in file_configs.items():
                        if isinstance(config, dict):
                            self.configs[name] = config
                            self.config_file_map[name] = config_file

    def _create_widgets(self):
        """Create the enhanced widget interface."""
        # Styles and layouts
        style = {"description_width": "120px"}
        full_layout = widgets.Layout(width="100%")
        # Configuration selector with add button
        self.config_dropdown = widgets.Dropdown(
            options=list(self.configs.keys()),
            value=list(self.configs.keys())[0] if self.configs else None,
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
            options=["local", "ssh", "slurm", "pbs", "sge", "kubernetes"],
            description="Cluster Type:",
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
            style=style,
            layout=full_layout,
        )
        # Advanced options
        self._create_advanced_options()
        # Save configuration section
        self._create_save_section()
        # Action buttons
        self.apply_btn = widgets.Button(
            description="Apply Configuration",
            button_style="primary",
            icon="check",
            layout=widgets.Layout(width="auto"),
        )
        self.apply_btn.on_click(self._on_apply_config)
        self.delete_btn = widgets.Button(
            description="Delete",
            button_style="danger",
            icon="trash",
            layout=widgets.Layout(width="auto"),
        )
        self.delete_btn.on_click(self._on_delete_config)
        # Status output
        self.status_output = widgets.Output()
        # Load initial configuration
        if self.configs:
            self._load_config_to_widgets(list(self.configs.keys())[0])

    def _create_dynamic_fields(self):
        """Create dynamic fields that change based on cluster type."""
        style = {"description_width": "120px"}
        full_layout = widgets.Layout(width="100%")
        half_layout = widgets.Layout(width="48%")
        # Host/Address field with validation
        self.host_field = widgets.Text(
            description="Host/Address:",
            placeholder="hostname or IP address",
            style=style,
            layout=full_layout,
        )
        self.host_field.observe(self._validate_host, names="value")
        # Username field
        self.username_field = widgets.Text(
            description="Username:",
            placeholder="remote username",
            style=style,
            layout=half_layout,
        )
        # SSH Key field
        self.ssh_key_field = widgets.Text(
            description="SSH Key:",
            placeholder="~/.ssh/id_rsa",
            style=style,
            layout=half_layout,
        )
        # Port field
        self.port_field = widgets.IntText(
            value=22,
            description="Port:",
            style=style,
            layout=widgets.Layout(width="200px"),
        )
        # Resource fields
        self.cores_field = widgets.IntText(
            value=4,
            description="CPUs:",
            style=style,
            layout=widgets.Layout(width="200px"),
        )
        self.memory_field = widgets.Text(
            value="8GB",
            description="Memory:",
            placeholder="e.g., 8GB, 16GB",
            style=style,
            layout=widgets.Layout(width="200px"),
        )
        self.time_field = widgets.Text(
            value="01:00:00",
            description="Time Limit:",
            placeholder="HH:MM:SS",
            style=style,
            layout=widgets.Layout(width="200px"),
        )
        # Kubernetes-specific fields
        self.k8s_namespace = widgets.Text(
            value="default",
            description="Namespace:",
            style=style,
            layout=half_layout,
        )
        self.k8s_image = widgets.Text(
            value="python:3.11-slim",
            description="Docker Image:",
            style=style,
            layout=half_layout,
        )
        # Remote work directory
        self.work_dir_field = widgets.Text(
            value="/tmp/clustrix",
            description="Work Directory:",
            style=style,
            layout=full_layout,
        )

    def _create_advanced_options(self):
        """Create advanced options accordion."""
        style = {"description_width": "120px"}
        full_layout = widgets.Layout(width="100%")
        # Package manager selection
        self.package_manager = widgets.Dropdown(
            options=["auto", "pip", "conda", "uv"],
            value="auto",
            description="Package Manager:",
            style=style,
            layout=full_layout,
        )
        # Python version
        self.python_version = widgets.Text(
            value="python",
            description="Python Executable:",
            placeholder="python, python3, python3.11",
            style=style,
            layout=full_layout,
        )
        # Environment variables
        self.env_vars = widgets.Textarea(
            value="",
            placeholder="KEY1=value1\nKEY2=value2",
            description="Env Variables:",
            rows=3,
            style=style,
            layout=full_layout,
        )
        # Module loads
        self.module_loads = widgets.Textarea(
            value="",
            placeholder="module1\nmodule2",
            description="Module Loads:",
            rows=3,
            style=style,
            layout=full_layout,
        )
        # Pre-execution commands
        self.pre_exec_commands = widgets.Textarea(
            value="",
            placeholder="source /path/to/setup.sh\nexport PATH=/custom/path:$PATH",
            description="Pre-exec Commands:",
            rows=3,
            style=style,
            layout=full_layout,
        )
        # Advanced options container
        self.advanced_container = widgets.VBox(
            [
                widgets.HTML("<h5>Environment Settings</h5>"),
                self.package_manager,
                self.python_version,
                widgets.HTML("<h5>Additional Configuration</h5>"),
                self.env_vars,
                self.module_loads,
                self.pre_exec_commands,
            ]
        )
        # Accordion for collapsible advanced options
        self.advanced_accordion = widgets.Accordion(children=[self.advanced_container])
        self.advanced_accordion.set_title(0, "Advanced Options")
        self.advanced_accordion.selected_index = None  # Start collapsed

    def _create_save_section(self):
        """Create save configuration section."""
        style = {"description_width": "120px"}
        # File selection dropdown
        file_options = ["New file: clustrix.yml"]
        for config_file in self.config_files:
            file_options.append(f"Existing: {config_file}")
        self.save_file_dropdown = widgets.Dropdown(
            options=file_options,
            value=file_options[0],
            description="Save to:",
            style=style,
            layout=widgets.Layout(width="70%"),
        )
        # Save button
        self.save_btn = widgets.Button(
            description="Save Configuration",
            button_style="info",
            icon="save",
            layout=widgets.Layout(width="auto"),
        )
        self.save_btn.on_click(self._on_save_config)

    def _validate_host(self, change):
        """Validate host field input."""
        value = change["new"]
        if value and not (validate_ip_address(value) or validate_hostname(value)):
            # Visual feedback for invalid input
            self.host_field.layout.border = "2px solid red"
        else:
            self.host_field.layout.border = ""

    def _on_cluster_type_change(self, change):
        """Handle cluster type change to show/hide relevant fields."""
        cluster_type = change["new"]
        # Update field visibility based on cluster type
        if cluster_type == "local":
            # Hide remote-specific fields
            self.host_field.layout.display = "none"
            self.username_field.layout.display = "none"
            self.ssh_key_field.layout.display = "none"
            self.port_field.layout.display = "none"
            self.work_dir_field.layout.display = "none"
            self.k8s_namespace.layout.display = "none"
            self.k8s_image.layout.display = "none"
        elif cluster_type == "kubernetes":
            # Show Kubernetes-specific fields
            self.host_field.layout.display = ""
            self.username_field.layout.display = "none"
            self.ssh_key_field.layout.display = "none"
            self.port_field.layout.display = ""
            self.work_dir_field.layout.display = ""
            self.k8s_namespace.layout.display = ""
            self.k8s_image.layout.display = ""
        else:  # ssh, slurm, pbs, sge
            # Show SSH-based fields
            self.host_field.layout.display = ""
            self.username_field.layout.display = ""
            self.ssh_key_field.layout.display = ""
            self.port_field.layout.display = ""
            self.work_dir_field.layout.display = ""
            self.k8s_namespace.layout.display = "none"
            self.k8s_image.layout.display = "none"

    def _load_config_to_widgets(self, config_name: str):
        """Load a configuration into the widgets."""
        if config_name not in self.configs:
            return
        config = self.configs[config_name]
        self.current_config_name = config_name
        # Basic fields
        self.config_name.value = config.get("name", config_name)
        self.cluster_type.value = config.get("cluster_type", "local")
        # Connection fields
        self.host_field.value = config.get("cluster_host", "")
        self.username_field.value = config.get("username", "")
        self.ssh_key_field.value = config.get("key_file", "")
        self.port_field.value = config.get("cluster_port", 22)
        # Resource fields
        self.cores_field.value = config.get("default_cores", 4)
        self.memory_field.value = config.get("default_memory", "8GB")
        self.time_field.value = config.get("default_time", "01:00:00")
        # Kubernetes fields
        self.k8s_namespace.value = config.get("k8s_namespace", "default")
        self.k8s_image.value = config.get("k8s_image", "python:3.11-slim")
        # Paths
        self.work_dir_field.value = config.get("remote_work_dir", "/tmp/clustrix")
        # Advanced options
        self.package_manager.value = config.get("package_manager", "auto")
        self.python_version.value = config.get("python_executable", "python")
        # Environment variables
        env_vars = config.get("environment_variables", {})
        if env_vars:
            self.env_vars.value = "\n".join(f"{k}={v}" for k, v in env_vars.items())
        else:
            self.env_vars.value = ""
        # Module loads
        modules = config.get("module_loads", [])
        self.module_loads.value = "\n".join(modules) if modules else ""
        # Pre-execution commands
        pre_cmds = config.get("pre_execution_commands", [])
        self.pre_exec_commands.value = "\n".join(pre_cmds) if pre_cmds else ""
        # Update field visibility
        self._on_cluster_type_change({"new": self.cluster_type.value})

    def _save_config_from_widgets(self) -> Dict[str, Any]:
        """Save current widget values to a configuration dict."""
        config = {
            "name": self.config_name.value,
            "cluster_type": self.cluster_type.value,
            "default_cores": self.cores_field.value,
            "default_memory": self.memory_field.value,
            "default_time": self.time_field.value,
            "package_manager": self.package_manager.value,
            "python_executable": self.python_version.value,
        }
        # Add cluster-specific fields
        if self.cluster_type.value != "local":
            if self.cluster_type.value == "kubernetes":
                config["cluster_host"] = self.host_field.value
                config["cluster_port"] = self.port_field.value
                config["remote_work_dir"] = self.work_dir_field.value
                config["k8s_namespace"] = self.k8s_namespace.value
                config["k8s_image"] = self.k8s_image.value
            else:  # SSH-based clusters
                config["cluster_host"] = self.host_field.value
                config["cluster_port"] = self.port_field.value
                config["username"] = self.username_field.value
                config["key_file"] = self.ssh_key_field.value
                config["remote_work_dir"] = self.work_dir_field.value
        # Parse environment variables
        if self.env_vars.value.strip():
            env_dict = {}
            for line in self.env_vars.value.strip().split("\n"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    env_dict[key.strip()] = value.strip()
            if env_dict:
                config["environment_variables"] = env_dict
        # Parse module loads
        if self.module_loads.value.strip():
            config["module_loads"] = [
                m.strip()
                for m in self.module_loads.value.strip().split("\n")
                if m.strip()
            ]
        # Parse pre-execution commands
        if self.pre_exec_commands.value.strip():
            config["pre_execution_commands"] = [
                c.strip()
                for c in self.pre_exec_commands.value.strip().split("\n")
                if c.strip()
            ]
        return config

    def _on_config_select(self, change):
        """Handle configuration selection from dropdown."""
        config_name = change["new"]
        if config_name:
            self._load_config_to_widgets(config_name)

    def _on_add_config(self, button):
        """Add a new configuration."""
        with self.status_output:
            self.status_output.clear_output()
            # Generate unique name
            base_name = "new_config"
            counter = 1
            config_name = base_name
            while config_name in self.configs:
                config_name = f"{base_name}_{counter}"
                counter += 1
            # Create new config
            self.configs[config_name] = {
                "name": f"New Configuration {counter}",
                "cluster_type": "local",
                "default_cores": 4,
                "default_memory": "8GB",
                "default_time": "01:00:00",
            }
            # Update dropdown
            self.config_dropdown.options = list(self.configs.keys())
            self.config_dropdown.value = config_name
            print(f"✅ Created new configuration: {config_name}")

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
            # Delete config
            del self.configs[self.current_config_name]
            if self.current_config_name in self.config_file_map:
                del self.config_file_map[self.current_config_name]
            # Update dropdown
            self.config_dropdown.options = list(self.configs.keys())
            self.config_dropdown.value = list(self.configs.keys())[0]
            print(f"✅ Deleted configuration: {self.current_config_name}")

    def _on_apply_config(self, button):
        """Apply the current configuration."""
        with self.status_output:
            self.status_output.clear_output()
            try:
                # Save current state
                config = self._save_config_from_widgets()
                self.configs[self.current_config_name] = config
                # Prepare config for application
                apply_config = config.copy()
                apply_config.pop("name", None)
                # Apply configuration
                configure(**apply_config)
                print(f"✅ Applied configuration: {self.current_config_name}")
                # Show current config
                current = get_config()
                print("\nActive configuration:")
                print(f"  Type: {current.cluster_type}")
                if current.cluster_type != "local":
                    print(f"  Host: {current.cluster_host}")
                print(f"  CPUs: {current.default_cores}")
                print(f"  Memory: {current.default_memory}")
            except Exception as e:
                print(f"❌ Error applying configuration: {str(e)}")

    def _on_save_config(self, button):
        """Save configuration to file."""
        with self.status_output:
            self.status_output.clear_output()
            try:
                # Save current widget state
                config = self._save_config_from_widgets()
                self.configs[self.current_config_name] = config
                # Determine save file
                save_option = self.save_file_dropdown.value
                if save_option.startswith("New file:"):
                    save_path = Path("clustrix.yml")
                else:
                    # Extract path from "Existing: /path/to/file"
                    save_path = Path(save_option.split("Existing: ", 1)[1])
                # Load existing configs if updating a file
                if save_path.exists():
                    existing_configs = load_config_from_file(save_path)
                    if isinstance(existing_configs, dict):
                        if "cluster_type" in existing_configs:
                            # Single config file - convert to multi-config
                            existing_configs = {save_path.stem: existing_configs}
                        existing_configs[self.current_config_name] = config
                        save_data = existing_configs
                    else:
                        save_data = {self.current_config_name: config}
                else:
                    save_data = {self.current_config_name: config}
                # Save to file
                with open(save_path, "w") as f:
                    yaml.dump(save_data, f, default_flow_style=False)
                print(
                    f"✅ Saved configuration '{self.current_config_name}' to {save_path}"
                )
                # Update file list if new file
                if save_path not in self.config_files:
                    self.config_files.append(save_path)
                    self.config_file_map[self.current_config_name] = save_path
                    # Update save dropdown
                    file_options = ["New file: clustrix.yml"]
                    for config_file in self.config_files:
                        file_options.append(f"Existing: {config_file}")
                    self.save_file_dropdown.options = file_options
            except Exception as e:
                print(f"❌ Error saving configuration: {str(e)}")

    def display(self):
        """Display the enhanced widget interface."""
        # Title with conditional text
        title_text = "🚀 Clustrix Configuration Manager"
        if self.auto_display:
            title_text += " (Auto-displayed on import)"
        display(HTML(f"<h3>{title_text}</h3>"))
        # Configuration selector section
        config_section = widgets.HBox(
            [
                self.config_dropdown,
                self.add_config_btn,
            ]
        )
        # Main configuration fields
        basic_fields = widgets.VBox(
            [
                self.config_name,
                self.cluster_type,
            ]
        )
        # Connection fields (dynamically shown/hidden)
        connection_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Connection Settings</h5>"),
                self.host_field,
                widgets.HBox([self.username_field, self.ssh_key_field]),
                self.port_field,
            ]
        )
        # Kubernetes fields
        k8s_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Kubernetes Settings</h5>"),
                widgets.HBox([self.k8s_namespace, self.k8s_image]),
            ]
        )
        # Resource fields
        resource_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Resource Defaults</h5>"),
                widgets.HBox([self.cores_field, self.memory_field, self.time_field]),
                self.work_dir_field,
            ]
        )
        # Save section
        save_section = widgets.VBox(
            [
                widgets.HTML("<h5>Configuration Management</h5>"),
                widgets.HBox([self.save_file_dropdown, self.save_btn]),
            ]
        )
        # Action buttons
        action_buttons = widgets.HBox(
            [
                self.apply_btn,
                self.delete_btn,
            ]
        )
        # Main layout
        main_layout = widgets.VBox(
            [
                config_section,
                widgets.HTML("<hr>"),
                basic_fields,
                connection_fields,
                k8s_fields,
                resource_fields,
                self.advanced_accordion,
                widgets.HTML("<hr>"),
                save_section,
                widgets.HTML("<hr>"),
                action_buttons,
                widgets.HTML("<hr>"),
                self.status_output,
            ]
        )
        display(main_layout)


# Global variable to track if widget has been auto-displayed
_auto_displayed = False


def display_config_widget(auto_display: bool = False):
    """Display the configuration widget."""
    widget = EnhancedClusterConfigWidget(auto_display=auto_display)
    widget.display()


def auto_display_on_import():
    """Automatically display widget when clustrix is imported in a notebook."""
    global _auto_displayed
    if _auto_displayed or not IPYTHON_AVAILABLE:
        return
    ipython = get_ipython()
    if ipython is None:
        return
    # Check if we're in a notebook environment
    if hasattr(ipython, "kernel") and hasattr(ipython, "register_magic_function"):
        # Mark as displayed
        _auto_displayed = True
        # Display the widget
        display_config_widget(auto_display=True)


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
        - Select and manage cluster configurations
        - Create new configurations with validation
        - Save/load configurations from files
        - Apply configurations to the current session
        """
        if not IPYTHON_AVAILABLE:
            print("❌ This magic command requires IPython and ipywidgets")
            print("Install with: pip install ipywidgets")
            return
        # Create and display the widget (not auto-display)
        display_config_widget(auto_display=False)
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


# Export the widget class for testing
ClusterConfigWidget = EnhancedClusterConfigWidget
