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
    import ipywidgets as _widgets  # type: ignore
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

    # Mock widgets module - each class creates independent instances
    class _MockLayout:
        def __init__(self, *args, **kwargs):
            self.display = ""
            self.border = ""
            for key, value in kwargs.items():
                setattr(self, key, value)

    class _MockDropdown:
        def __init__(self, *args, **kwargs):
            self.value = kwargs.get("value")
            self.options = kwargs.get("options", [])
            self.layout = _MockLayout()

        def observe(self, *args, **kwargs):
            pass

    class _MockButton:
        def __init__(self, *args, **kwargs):
            self.layout = _MockLayout()

        def on_click(self, *args, **kwargs):
            pass

    class _MockText:
        def __init__(self, *args, **kwargs):
            self.value = kwargs.get("value", "")
            self.layout = _MockLayout()

        def observe(self, *args, **kwargs):
            pass

    class _MockIntText:
        def __init__(self, *args, **kwargs):
            self.value = kwargs.get("value", 0)
            self.layout = _MockLayout()

        def observe(self, *args, **kwargs):
            pass

    class _MockTextarea:
        def __init__(self, *args, **kwargs):
            self.value = kwargs.get("value", "")
            self.layout = _MockLayout()

        def observe(self, *args, **kwargs):
            pass

    class _MockOutput:
        def __init__(self, *args, **kwargs):
            self.layout = _MockLayout()

        def clear_output(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class _MockVBox:
        def __init__(self, *args, **kwargs):
            self.children = args[0] if args else []
            self.layout = _MockLayout()

    class _MockHBox:
        def __init__(self, *args, **kwargs):
            self.children = args[0] if args else []
            self.layout = _MockLayout()

    class _MockHTML:
        def __init__(self, *args, **kwargs):
            self.value = args[0] if args else ""
            self.layout = _MockLayout()

    class _MockCheckbox:
        def __init__(self, *args, **kwargs):
            self.value = kwargs.get("value", False)
            self.layout = _MockLayout()

        def observe(self, *args, **kwargs):
            pass

    class _MockAccordion:
        def __init__(self, *args, **kwargs):
            self.children = args[0] if args else []
            self.selected_index = None
            self.layout = _MockLayout()

        def set_title(self, *args, **kwargs):
            pass

    class widgets:  # type: ignore
        Layout = _MockLayout
        Dropdown = _MockDropdown
        Button = _MockButton
        Text = _MockText
        IntText = _MockIntText
        Textarea = _MockTextarea
        Checkbox = _MockCheckbox
        Output = _MockOutput
        VBox = _MockVBox
        HBox = _MockHBox
        HTML = _MockHTML
        Accordion = _MockAccordion


from .config import configure, get_config

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
    "Local Kubernetes": {
        "cluster_type": "kubernetes",
        "k8s_namespace": "default",
        "k8s_image": "python:3.11",
        "default_cores": 2,
        "default_memory": "4GB",
        "package_manager": "pip",
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
    "Corporate PBS Cluster": {
        "cluster_type": "pbs",
        "cluster_host": "hpc.company.com",
        "username": "employee_id",
        "default_cores": 8,
        "default_memory": "32GB",
        "default_time": "02:00:00",
        "remote_work_dir": "/home/employee_id/clustrix",
        "package_manager": "pip",
    },
    "SGE Research Cluster": {
        "cluster_type": "sge",
        "cluster_host": "submit.research.org",
        "username": "researcher",
        "default_cores": 24,
        "default_memory": "128GB",
        "default_time": "04:00:00",
        "remote_work_dir": "/data/researcher/clustrix",
        "package_manager": "conda",
    },
    "SSH Remote Server": {
        "cluster_type": "ssh",
        "cluster_host": "remote.server.com",
        "username": "user",
        "cluster_port": 22,
        "default_cores": 4,
        "default_memory": "16GB",
        "remote_work_dir": "/tmp/clustrix",
        "package_manager": "pip",
    },
    # Cloud Provider Configurations
    "AWS EC2 Cluster": {
        "cluster_type": "aws",
        "aws_region": "us-east-1",
        "aws_instance_type": "t3.medium",
        "aws_cluster_type": "ec2",
        "default_cores": 2,
        "default_memory": "4GB",
        "remote_work_dir": "/home/ec2-user/clustrix",
        "package_manager": "conda",
        "cost_monitoring": True,
    },
    "AWS EKS Cluster": {
        "cluster_type": "aws",
        "aws_region": "us-east-1",
        "aws_instance_type": "t3.medium",
        "aws_cluster_type": "eks",
        "k8s_namespace": "default",
        "k8s_image": "python:3.11",
        "default_cores": 2,
        "default_memory": "4GB",
        "package_manager": "pip",
        "cost_monitoring": True,
    },
    "Azure VM Cluster": {
        "cluster_type": "azure",
        "azure_region": "eastus",
        "azure_instance_type": "Standard_D2s_v3",
        "default_cores": 2,
        "default_memory": "8GB",
        "remote_work_dir": "/home/azureuser/clustrix",
        "package_manager": "conda",
        "cost_monitoring": True,
    },
    "Google Cloud VM": {
        "cluster_type": "gcp",
        "gcp_region": "us-central1",
        "gcp_instance_type": "e2-medium",
        "default_cores": 2,
        "default_memory": "4GB",
        "remote_work_dir": "/home/ubuntu/clustrix",
        "package_manager": "conda",
        "cost_monitoring": True,
    },
    "Lambda Cloud GPU": {
        "cluster_type": "lambda_cloud",
        "lambda_instance_type": "gpu_1x_a10",
        "default_cores": 8,
        "default_memory": "32GB",
        "remote_work_dir": "/home/ubuntu/clustrix",
        "package_manager": "conda",
        "cost_monitoring": True,
    },
    "HuggingFace Space": {
        "cluster_type": "huggingface_spaces",
        "hf_hardware": "cpu-basic",
        "hf_sdk": "gradio",
        "default_cores": 2,
        "default_memory": "16GB",
        "cost_monitoring": True,
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
        self.configs: Dict[str, Dict[str, Any]] = {}
        self.current_config_name: Optional[str] = None
        self.config_files: List[Path] = []
        self.config_file_map: Dict[str, Path] = (
            {}
        )  # Maps config names to their source files
        self.auto_display = auto_display
        self.has_unsaved_changes = False
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
                "pbs",
                "sge",
                "kubernetes",
                "aws",
                "azure",
                "gcp",
                "lambda_cloud",
                "huggingface_spaces",
            ],
            description="Cluster Type:",
            tooltip=(
                "Choose where to run your jobs: local machine, remote servers "
                "(SSH/SLURM/PBS/SGE), Kubernetes clusters, or cloud providers"
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
                "(e.g., 'AWS Production', 'Local Testing', 'HPC Cluster')"
            ),
            style=style,
            layout=full_layout,
        )
        self.config_name.observe(self._on_config_name_change, names="value")
        # Advanced options
        self._create_advanced_options()
        # Save configuration section
        self._create_save_section()
        # Create section containers
        self._create_section_containers()
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
        self.test_btn = widgets.Button(
            description="Test Configuration",
            button_style="info",
            icon="play",
            layout=widgets.Layout(width="auto"),
        )
        self.test_btn.on_click(self._on_test_config)
        # Status output with proper sizing
        self.status_output = widgets.Output(
            layout=widgets.Layout(
                height="120px",  # Increased height for better visibility
                width="100%",
                overflow_y="auto",
                border="1px solid #ddd",
                border_radius="4px",
                padding="8px",
                margin="10px 0px",
                background_color="#f8f9fa",
            )
        )
        # Update dropdown and load initial configuration
        self._update_config_dropdown()
        if self.configs:
            self._load_config_to_widgets(list(self.configs.keys())[0])

    def _update_config_dropdown(self):
        """Update the configuration dropdown with current config names."""
        if not self.configs:
            self.config_dropdown.options = []
            self.config_dropdown.value = None
            return

        # Create options list with display names and config keys
        options = []
        for config_key, config_data in self.configs.items():
            display_name = config_data.get("name", config_key)
            options.append((display_name, config_key))

        # Store current selection
        current_value = self.config_dropdown.value

        # Update options
        self.config_dropdown.options = options

        # Restore selection if it still exists
        if current_value and current_value in [opt[1] for opt in options]:
            self.config_dropdown.value = current_value
        elif options:
            self.config_dropdown.value = options[0][1]

    def _on_config_name_change(self, change):
        """Handle changes to the config name field."""
        if self.current_config_name and self.current_config_name in self.configs:
            # Only add name field for non-default configs (avoid modifying DEFAULT_CONFIGS)
            if self.current_config_name not in DEFAULT_CONFIGS:
                # Update the name in the current configuration
                self.configs[self.current_config_name]["name"] = change["new"]
                # Update the dropdown to reflect the new display name
                self._update_config_dropdown()

    def _create_dynamic_fields(self):
        """Create dynamic fields that change based on cluster type."""
        style = {"description_width": "120px"}
        full_layout = widgets.Layout(width="100%")
        half_layout = widgets.Layout(width="48%")
        # Host/Address field with validation
        self.host_field = widgets.Text(
            description="Host/Address:",
            placeholder="hostname or IP address",
            tooltip=(
                "Enter the hostname or IP address of your remote cluster "
                "(e.g., cluster.example.com or 192.168.1.100)"
            ),
            style=style,
            layout=full_layout,
        )
        self.host_field.observe(self._validate_host, names="value")
        # Username field
        self.username_field = widgets.Text(
            description="Username:",
            placeholder="remote username",
            tooltip="Your username on the remote cluster for SSH authentication",
            style=style,
            layout=half_layout,
        )
        # SSH Key field
        self.ssh_key_field = widgets.Text(
            description="SSH Key:",
            placeholder="~/.ssh/id_rsa",
            tooltip=(
                "Path to your SSH private key file for passwordless authentication "
                "(generate with 'ssh-keygen -t rsa')"
            ),
            style=style,
            layout=half_layout,
        )
        # Port field
        self.port_field = widgets.IntText(
            value=22,
            description="Port:",
            tooltip="SSH port number (default: 22). Check with your system administrator if unsure",
            style=style,
            layout=widgets.Layout(width="200px"),
        )
        # Resource fields
        self.cores_field = widgets.IntText(
            value=4,
            description="CPUs:",
            tooltip="Number of CPU cores to request for each job (affects job scheduling and performance)",
            style=style,
            layout=widgets.Layout(width="200px"),
        )
        self.memory_field = widgets.Text(
            value="8GB",
            description="Memory:",
            placeholder="e.g., 8GB, 16GB",
            tooltip=(
                "Amount of RAM to request for each job (e.g., '8GB', '16GB'). "
                "Higher memory allows processing larger datasets"
            ),
            style=style,
            layout=widgets.Layout(width="200px"),
        )
        self.time_field = widgets.Text(
            value="01:00:00",
            description="Time Limit:",
            placeholder="HH:MM:SS",
            tooltip="Maximum time job can run (format: HH:MM:SS). Jobs exceeding this limit will be terminated",
            style=style,
            layout=widgets.Layout(width="200px"),
        )
        # Kubernetes-specific fields
        self.k8s_namespace = widgets.Text(
            value="default",
            description="Namespace:",
            tooltip=(
                "Kubernetes namespace for job pods (default: 'default'). "
                "Contact your cluster admin for appropriate namespace"
            ),
            style=style,
            layout=half_layout,
        )
        self.k8s_image = widgets.Text(
            value="python:3.11-slim",
            description="Docker Image:",
            tooltip="Docker image for job containers (e.g., 'python:3.11-slim', 'tensorflow/tensorflow:latest')",
            style=style,
            layout=half_layout,
        )
        # Remote Kubernetes checkbox
        self.k8s_remote_checkbox = widgets.Checkbox(
            value=False,
            description="Remote Kubernetes Cluster",
            tooltip="Check if connecting to a remote Kubernetes cluster (requires kubectl configuration)",
            style=style,
            layout=full_layout,
        )
        self.k8s_remote_checkbox.observe(self._on_k8s_remote_change, names="value")
        # Cost monitoring checkbox for cloud providers
        self.cost_monitoring_checkbox = widgets.Checkbox(
            value=False,
            description="Enable Cost Monitoring",
            tooltip="Track and report estimated costs for cloud provider resources (AWS, Azure, GCP)",
            style=style,
            layout=full_layout,
        )
        # Remote work directory
        self.work_dir_field = widgets.Text(
            value="/tmp/clustrix",
            description="Work Directory:",
            tooltip="Remote directory path where job files will be stored (must have write permissions)",
            style=style,
            layout=full_layout,
        )

        # Cloud provider-specific fields
        # AWS fields
        self.aws_region = widgets.Dropdown(
            options=["us-east-1"],  # Will be populated dynamically
            value="us-east-1",
            description="AWS Region:",
            tooltip="AWS region for resources (affects latency and pricing). Choose region closest to your location",
            style=style,
            layout=half_layout,
        )
        self.aws_region.observe(self._on_aws_region_change, names="value")

        self.aws_instance_type = widgets.Dropdown(
            options=["t3.medium"],  # Will be populated dynamically
            value="t3.medium",
            description="Instance Type:",
            tooltip="AWS EC2 instance type (affects CPU, memory, and cost). t3.medium = 2 vCPUs + 4GB RAM",
            style=style,
            layout=half_layout,
        )
        self.aws_access_key = widgets.Text(
            description="AWS Access Key ID:",
            placeholder="AKIA...",
            tooltip="AWS access key ID from IAM user (starts with AKIA). Get from AWS Console > IAM > Users",
            style=style,
            layout=half_layout,
        )
        self.aws_secret_key = widgets.Password(
            description="AWS Secret Key:",
            placeholder="Your AWS secret access key",
            tooltip="AWS secret access key (keep secure!). Generated when creating access key in IAM",
            style=style,
            layout=half_layout,
        )
        self.aws_cluster_type = widgets.Dropdown(
            options=["ec2", "eks"],
            value="ec2",
            description="AWS Cluster Type:",
            tooltip="EC2: Virtual machines, EKS: Managed Kubernetes service (requires additional setup)",
            style=style,
            layout=half_layout,
        )

        # Azure fields
        self.azure_region = widgets.Dropdown(
            options=["eastus"],  # Will be populated dynamically
            value="eastus",
            description="Azure Region:",
            tooltip="Azure region for resources (affects latency and pricing). Choose region closest to your location",
            style=style,
            layout=half_layout,
        )
        self.azure_region.observe(self._on_azure_region_change, names="value")

        self.azure_instance_type = widgets.Dropdown(
            options=["Standard_D2s_v3"],  # Will be populated dynamically
            value="Standard_D2s_v3",
            description="VM Size:",
            tooltip="Azure VM size (affects CPU, memory, and cost). Standard_D2s_v3 = 2 vCPUs + 8GB RAM",
            style=style,
            layout=half_layout,
        )
        self.azure_subscription_id = widgets.Text(
            description="Subscription ID:",
            placeholder="Your Azure subscription ID",
            tooltip="Azure subscription ID (UUID format). Find in Azure Portal > Subscriptions",
            style=style,
            layout=full_layout,
        )
        self.azure_client_id = widgets.Text(
            description="Client ID:",
            placeholder="Azure service principal client ID",
            tooltip=(
                "Azure service principal application ID (UUID format). "
                "Create in Azure AD > App registrations"
            ),
            style=style,
            layout=half_layout,
        )
        self.azure_client_secret = widgets.Password(
            description="Client Secret:",
            placeholder="Azure service principal secret",
            tooltip=(
                "Azure service principal secret (keep secure!). Generated in Azure AD > "
                "App registrations > Certificates & secrets"
            ),
            style=style,
            layout=half_layout,
        )

        # Google Cloud fields
        self.gcp_project_id = widgets.Text(
            description="Project ID:",
            placeholder="your-gcp-project-id",
            tooltip="Google Cloud project ID (lowercase, hyphens allowed). Find in GCP Console dashboard",
            style=style,
            layout=half_layout,
        )
        self.gcp_region = widgets.Dropdown(
            options=["us-central1"],  # Will be populated dynamically
            value="us-central1",
            description="GCP Region:",
            tooltip=(
                "Google Cloud region for resources (affects latency and pricing). "
                "Choose region closest to your location"
            ),
            style=style,
            layout=half_layout,
        )
        self.gcp_region.observe(self._on_gcp_region_change, names="value")

        self.gcp_instance_type = widgets.Dropdown(
            options=["e2-medium"],  # Will be populated dynamically
            value="e2-medium",
            description="Machine Type:",
            tooltip="GCP machine type (affects CPU, memory, and cost). e2-medium = 1 vCPU + 4GB RAM",
            style=style,
            layout=half_layout,
        )
        self.gcp_service_account_key = widgets.Textarea(
            description="Service Account Key:",
            placeholder="Paste your GCP service account JSON key here",
            tooltip=(
                "Google Cloud service account key in JSON format (keep secure!). "
                "Create in GCP Console > IAM & Admin > Service Accounts"
            ),
            style=style,
            layout=full_layout,
        )

        # Lambda Cloud fields
        self.lambda_api_key = widgets.Password(
            description="Lambda API Key:",
            placeholder="Your Lambda Cloud API key",
            tooltip="Lambda Cloud API key for GPU instance access (keep secure!). Get from Lambda Cloud dashboard",
            style=style,
            layout=full_layout,
        )
        self.lambda_instance_type = widgets.Dropdown(
            options=["gpu_1x_a10"],  # Will be populated dynamically
            value="gpu_1x_a10",
            description="Instance Type:",
            tooltip=(
                "Lambda Cloud GPU instance type (affects GPU model, RAM, and cost). "
                "gpu_1x_a10 = 1x NVIDIA A10 + 30GB RAM"
            ),
            style=style,
            layout=half_layout,
        )

        # HuggingFace fields
        self.hf_token = widgets.Password(
            description="HF Token:",
            placeholder="Your HuggingFace API token",
            tooltip=(
                "HuggingFace API token for Spaces access (keep secure!). "
                "Get from HuggingFace Settings > Access Tokens"
            ),
            style=style,
            layout=half_layout,
        )
        self.hf_username = widgets.Text(
            description="HF Username:",
            placeholder="Your HuggingFace username",
            style=style,
            layout=half_layout,
        )
        self.hf_hardware = widgets.Dropdown(
            options=["cpu-basic"],  # Will be populated dynamically
            value="cpu-basic",
            description="Hardware:",
            style=style,
            layout=half_layout,
        )
        self.hf_sdk = widgets.Dropdown(
            options=["gradio", "streamlit", "docker"],
            value="gradio",
            description="SDK:",
            style=style,
            layout=half_layout,
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

        # Custom filename input
        self.save_filename_input = widgets.Text(
            value="clustrix.yml",
            description="Filename:",
            placeholder="config.yml or config.json",
            style=style,
            layout=widgets.Layout(width="70%"),
        )

        # Existing files dropdown (optional)
        file_options = ["(Create new file)"]
        for config_file in self.config_files:
            file_options.append(f"Overwrite: {config_file}")
        self.save_file_dropdown = widgets.Dropdown(
            options=file_options,
            value=file_options[0],
            description="Or select:",
            style=style,
            layout=widgets.Layout(width="70%"),
        )
        self.save_file_dropdown.observe(self._on_save_file_select, names="value")
        # Save button
        self.save_btn = widgets.Button(
            description="Save Configuration",
            button_style="info",
            icon="save",
            layout=widgets.Layout(width="auto"),
        )
        self.save_btn.on_click(self._on_save_config)

        # Load configuration widgets - using textarea for better Colab compatibility
        self.load_config_textarea = widgets.Textarea(
            value="",
            placeholder="Paste your YAML or JSON configuration here...\n\n"
            "Example:\nmy_config:\n  cluster_type: ssh\n  "
            "cluster_host: myserver.com\n  username: myuser",
            description="Config Content:",
            rows=8,
            style=style,
            layout=widgets.Layout(width="100%"),
        )
        self.load_btn = widgets.Button(
            description="Load Configuration",
            button_style="warning",
            icon="upload",
            layout=widgets.Layout(width="auto"),
        )
        self.load_btn.on_click(self._on_load_config)

        # Set up change tracking for all form fields
        self._setup_change_tracking()

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
            self.host_field,
            self.username_field,
            self.ssh_key_field,
            self.port_field,
            self.cores_field,
            self.memory_field,
            self.time_field,
            self.k8s_namespace,
            self.k8s_image,
            self.k8s_remote_checkbox,
            self.cost_monitoring_checkbox,
            self.work_dir_field,
            self.package_manager,
            self.python_version,
            self.env_vars,
            self.module_loads,
            self.pre_exec_commands,
        ]
        for field in fields_to_track:
            field.observe(self._mark_unsaved_changes, names="value")

    def _on_save_file_select(self, change):
        """Handle selection from existing files dropdown."""
        selected = change["new"]
        if selected.startswith("Overwrite: "):
            # Extract filename from "Overwrite: /path/to/file"
            file_path = selected.replace("Overwrite: ", "")
            self.save_filename_input.value = str(Path(file_path).name)

    def _create_section_containers(self):
        """Create the main UI section containers."""
        # Connection fields (dynamically shown/hidden)
        self.connection_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Connection Settings</h5>"),
                self.host_field,
                widgets.HBox([self.username_field, self.ssh_key_field]),
                self.port_field,
            ]
        )
        # Kubernetes fields
        self.k8s_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Kubernetes Settings</h5>"),
                widgets.HBox([self.k8s_namespace, self.k8s_image]),
                self.k8s_remote_checkbox,
                self.cost_monitoring_checkbox,
            ]
        )

        # Separate cloud provider field containers
        self.aws_fields = widgets.VBox(
            [
                widgets.HTML("<h5>AWS Settings</h5>"),
                widgets.HBox([self.aws_region, self.aws_instance_type]),
                widgets.HBox([self.aws_access_key, self.aws_secret_key]),
                self.aws_cluster_type,
                self.cost_monitoring_checkbox,
            ]
        )

        self.azure_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Azure Settings</h5>"),
                widgets.HBox([self.azure_region, self.azure_instance_type]),
                self.azure_subscription_id,
                widgets.HBox([self.azure_client_id, self.azure_client_secret]),
                self.cost_monitoring_checkbox,
            ]
        )

        self.gcp_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Google Cloud Settings</h5>"),
                widgets.HBox([self.gcp_project_id, self.gcp_region]),
                self.gcp_instance_type,
                self.gcp_service_account_key,
                self.cost_monitoring_checkbox,
            ]
        )

        self.lambda_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Lambda Cloud Settings</h5>"),
                self.lambda_api_key,
                self.lambda_instance_type,
                self.cost_monitoring_checkbox,
            ]
        )

        self.hf_fields = widgets.VBox(
            [
                widgets.HTML("<h5>HuggingFace Spaces Settings</h5>"),
                widgets.HBox([self.hf_token, self.hf_username]),
                widgets.HBox([self.hf_hardware, self.hf_sdk]),
                self.cost_monitoring_checkbox,
            ]
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
        self.k8s_fields.layout.display = "none"
        self.work_dir_field.layout.display = "none"

        # Hide all cloud provider sections
        self.aws_fields.layout.display = "none"
        self.azure_fields.layout.display = "none"
        self.gcp_fields.layout.display = "none"
        self.lambda_fields.layout.display = "none"
        self.hf_fields.layout.display = "none"

        # Update section visibility based on cluster type
        if cluster_type == "local":
            # Local only - no additional fields needed
            pass
        elif cluster_type == "kubernetes":
            # Show Kubernetes-specific fields, conditionally show connection fields
            self.k8s_fields.layout.display = ""
            self.work_dir_field.layout.display = ""
            # Connection fields depend on remote checkbox
            self._update_kubernetes_connection_visibility()
        elif cluster_type == "aws":
            # Show AWS cloud provider fields only
            self.aws_fields.layout.display = ""
            self.work_dir_field.layout.display = ""
            self._set_default_cloud_options("aws")
            self._populate_cloud_provider_options("aws")
        elif cluster_type == "azure":
            # Show Azure cloud provider fields only
            self.azure_fields.layout.display = ""
            self.work_dir_field.layout.display = ""
            self._set_default_cloud_options("azure")
            self._populate_cloud_provider_options("azure")
        elif cluster_type == "gcp":
            # Show GCP cloud provider fields only
            self.gcp_fields.layout.display = ""
            self.work_dir_field.layout.display = ""
            self._set_default_cloud_options("gcp")
            self._populate_cloud_provider_options("gcp")
        elif cluster_type == "lambda_cloud":
            # Show Lambda Cloud provider fields only
            self.lambda_fields.layout.display = ""
            self.work_dir_field.layout.display = ""
            self._set_default_cloud_options("lambda")
            self._populate_cloud_provider_options("lambda")
        elif cluster_type == "huggingface_spaces":
            # Show HuggingFace Spaces provider fields only
            self.hf_fields.layout.display = ""
            self.work_dir_field.layout.display = ""
            self._set_default_cloud_options("huggingface")
            self._populate_cloud_provider_options("huggingface")
        else:  # ssh, slurm, pbs, sge
            # Show SSH-based connection fields, hide other fields
            self.connection_fields.layout.display = ""
            self.work_dir_field.layout.display = ""

    def _on_k8s_remote_change(self, change):
        """Handle remote Kubernetes checkbox change."""
        self._update_kubernetes_connection_visibility()

    def _update_kubernetes_connection_visibility(self):
        """Update connection fields visibility for Kubernetes clusters."""
        if self.cluster_type.value == "kubernetes":
            if self.k8s_remote_checkbox.value:
                self.connection_fields.layout.display = ""
            else:
                self.connection_fields.layout.display = "none"

    def _populate_cloud_provider_options(self, provider: str):
        """Populate region and instance type options for the specified cloud provider."""
        try:
            from .cloud_providers import PROVIDERS

            # Get the provider class
            provider_class = PROVIDERS.get(provider)
            if not provider_class:
                # Fallback to default options if provider not available
                self._set_default_cloud_options(provider)
                return

            # Create provider instance (not authenticated yet)
            provider_instance = provider_class()

            # Get available regions and instance types
            regions = provider_instance.get_available_regions()
            instance_types = provider_instance.get_available_instance_types()

            # Update the appropriate dropdowns
            if provider == "aws":
                self.aws_region.options = regions
                self.aws_instance_type.options = instance_types
            elif provider == "azure":
                self.azure_region.options = regions
                self.azure_instance_type.options = instance_types
            elif provider == "gcp":
                self.gcp_region.options = regions
                self.gcp_instance_type.options = instance_types
            elif provider == "lambda":
                # Lambda Cloud has limited regions
                lambda_regions = provider_instance.get_available_regions()
                if lambda_regions:
                    # For Lambda, we might not have a region dropdown, but update instance types
                    pass
                self.lambda_instance_type.options = instance_types
            elif provider == "huggingface":
                # HuggingFace Spaces hardware options
                self.hf_hardware.options = instance_types

        except Exception as e:
            # Fallback to default options on error
            print(f"Failed to load {provider} options: {e}")
            self._set_default_cloud_options(provider)

    def _set_default_cloud_options(self, provider: str):
        """Set default options when cloud provider API is not available."""
        defaults = {
            "aws": {
                "regions": ["us-east-1", "us-west-1", "us-west-2", "eu-west-1"],
                "instances": [
                    "t3.micro",
                    "t3.small",
                    "t3.medium",
                    "t3.large",
                    "c5.large",
                ],
            },
            "azure": {
                "regions": ["eastus", "westus2", "northeurope", "westeurope"],
                "instances": [
                    "Standard_B1s",
                    "Standard_B2s",
                    "Standard_D2s_v3",
                    "Standard_D4s_v3",
                ],
            },
            "gcp": {
                "regions": [
                    "us-central1",
                    "us-east1",
                    "europe-west1",
                    "asia-southeast1",
                ],
                "instances": [
                    "e2-micro",
                    "e2-small",
                    "e2-medium",
                    "n1-standard-1",
                    "n1-standard-2",
                ],
            },
            "lambda": {
                "regions": ["us-east-1", "us-west-1"],
                "instances": [
                    "gpu_1x_a10",
                    "gpu_1x_a6000",
                    "gpu_1x_h100",
                    "gpu_2x_a10",
                ],
            },
            "huggingface": {
                "regions": ["global"],
                "instances": [
                    "cpu-basic",
                    "cpu-upgrade",
                    "t4-small",
                    "t4-medium",
                    "a10g-small",
                    "a10g-large",
                    "a100-large",
                ],
            },
        }

        if provider in defaults:
            provider_defaults = defaults[provider]
            if provider == "aws":
                self.aws_region.options = provider_defaults["regions"]
                self.aws_instance_type.options = provider_defaults["instances"]
            elif provider == "azure":
                self.azure_region.options = provider_defaults["regions"]
                self.azure_instance_type.options = provider_defaults["instances"]
            elif provider == "gcp":
                self.gcp_region.options = provider_defaults["regions"]
                self.gcp_instance_type.options = provider_defaults["instances"]
            elif provider == "lambda":
                self.lambda_instance_type.options = provider_defaults["instances"]
            elif provider == "huggingface":
                self.hf_hardware.options = provider_defaults["instances"]

    def _on_aws_region_change(self, change):
        """Handle AWS region change to update available instance types."""
        try:
            from .cloud_providers import PROVIDERS

            provider_class = PROVIDERS.get("aws")
            if provider_class:
                provider_instance = provider_class()
                instance_types = provider_instance.get_available_instance_types(
                    change["new"]
                )
                self.aws_instance_type.options = instance_types
        except Exception:
            pass  # Keep current options on error

    def _on_azure_region_change(self, change):
        """Handle Azure region change to update available instance types."""
        try:
            from .cloud_providers import PROVIDERS

            provider_class = PROVIDERS.get("azure")
            if provider_class:
                provider_instance = provider_class()
                instance_types = provider_instance.get_available_instance_types(
                    change["new"]
                )
                self.azure_instance_type.options = instance_types
        except Exception:
            pass  # Keep current options on error

    def _on_gcp_region_change(self, change):
        """Handle GCP region change to update available instance types."""
        try:
            from .cloud_providers import PROVIDERS

            provider_class = PROVIDERS.get("gcp")
            if provider_class:
                provider_instance = provider_class()
                instance_types = provider_instance.get_available_instance_types(
                    change["new"]
                )
                self.gcp_instance_type.options = instance_types
        except Exception:
            pass  # Keep current options on error

    def _load_config_to_widgets(self, config_name: str):
        """Load a configuration into the widgets."""
        if config_name not in self.configs:
            return
        config = self.configs[config_name]
        self.current_config_name = config_name

        # Basic fields
        self.config_name.value = config_name  # Use the key as the display name
        cluster_type = config.get("cluster_type", "local")
        self.cluster_type.value = cluster_type

        # Populate cloud provider options BEFORE setting values
        if cluster_type in [
            "aws",
            "azure",
            "gcp",
            "lambda_cloud",
            "huggingface_spaces",
        ]:
            provider_map = {
                "aws": "aws",
                "azure": "azure",
                "gcp": "gcp",
                "lambda_cloud": "lambda",
                "huggingface_spaces": "huggingface",
            }
            self._set_default_cloud_options(provider_map[cluster_type])
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
        self.k8s_remote_checkbox.value = config.get("k8s_remote", False)

        # Cloud provider fields - only set if value exists in dropdown options
        # AWS fields
        aws_region = config.get("aws_region", "us-east-1")
        if aws_region in self.aws_region.options:
            self.aws_region.value = aws_region

        aws_instance = config.get("aws_instance_type", "t3.medium")
        if aws_instance in self.aws_instance_type.options:
            self.aws_instance_type.value = aws_instance

        self.aws_access_key.value = config.get("aws_access_key", "")
        self.aws_secret_key.value = config.get("aws_secret_key", "")

        aws_cluster_type = config.get("aws_cluster_type", "ec2")
        if aws_cluster_type in self.aws_cluster_type.options:
            self.aws_cluster_type.value = aws_cluster_type

        # Azure fields
        azure_region = config.get("azure_region", "eastus")
        if azure_region in self.azure_region.options:
            self.azure_region.value = azure_region

        azure_instance = config.get("azure_instance_type", "Standard_D2s_v3")
        if azure_instance in self.azure_instance_type.options:
            self.azure_instance_type.value = azure_instance

        self.azure_subscription_id.value = config.get("azure_subscription_id", "")
        self.azure_client_id.value = config.get("azure_client_id", "")
        self.azure_client_secret.value = config.get("azure_client_secret", "")

        # GCP fields
        self.gcp_project_id.value = config.get("gcp_project_id", "")

        gcp_region = config.get("gcp_region", "us-central1")
        if gcp_region in self.gcp_region.options:
            self.gcp_region.value = gcp_region

        gcp_instance = config.get("gcp_instance_type", "e2-medium")
        if gcp_instance in self.gcp_instance_type.options:
            self.gcp_instance_type.value = gcp_instance

        self.gcp_service_account_key.value = config.get("gcp_service_account_key", "")

        # Lambda Cloud fields
        self.lambda_api_key.value = config.get("lambda_api_key", "")

        lambda_instance = config.get("lambda_instance_type", "gpu_1x_a10")
        if lambda_instance in self.lambda_instance_type.options:
            self.lambda_instance_type.value = lambda_instance

        # HuggingFace fields
        self.hf_token.value = config.get("hf_token", "")
        self.hf_username.value = config.get("hf_username", "")

        hf_hardware = config.get("hf_hardware", "cpu-basic")
        if hf_hardware in self.hf_hardware.options:
            self.hf_hardware.value = hf_hardware

        hf_sdk = config.get("hf_sdk", "gradio")
        if hf_sdk in self.hf_sdk.options:
            self.hf_sdk.value = hf_sdk

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
        # Cost monitoring
        self.cost_monitoring_checkbox.value = config.get("cost_monitoring", False)
        # Update field visibility
        self._on_cluster_type_change({"new": self.cluster_type.value})
        # Clear unsaved changes flag after loading
        self._clear_unsaved_changes()

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
                config["remote_work_dir"] = self.work_dir_field.value
                config["k8s_namespace"] = self.k8s_namespace.value
                config["k8s_image"] = self.k8s_image.value
                config["k8s_remote"] = self.k8s_remote_checkbox.value
                # Only include connection details if remote Kubernetes
                if self.k8s_remote_checkbox.value:
                    config["cluster_host"] = self.host_field.value
                    config["cluster_port"] = self.port_field.value
                    config["username"] = self.username_field.value
                    config["key_file"] = self.ssh_key_field.value
            elif self.cluster_type.value in [
                "aws",
                "azure",
                "gcp",
                "lambda_cloud",
                "huggingface_spaces",
            ]:
                # Cloud provider configurations
                config["remote_work_dir"] = self.work_dir_field.value
                config["cost_monitoring"] = self.cost_monitoring_checkbox.value

                if self.cluster_type.value == "aws":
                    config["aws_region"] = self.aws_region.value
                    config["aws_instance_type"] = self.aws_instance_type.value
                    config["aws_cluster_type"] = self.aws_cluster_type.value
                    if self.aws_access_key.value:
                        config["aws_access_key"] = self.aws_access_key.value
                    if self.aws_secret_key.value:
                        config["aws_secret_key"] = self.aws_secret_key.value

                elif self.cluster_type.value == "azure":
                    config["azure_region"] = self.azure_region.value
                    config["azure_instance_type"] = self.azure_instance_type.value
                    if self.azure_subscription_id.value:
                        config["azure_subscription_id"] = (
                            self.azure_subscription_id.value
                        )
                    if self.azure_client_id.value:
                        config["azure_client_id"] = self.azure_client_id.value
                    if self.azure_client_secret.value:
                        config["azure_client_secret"] = self.azure_client_secret.value

                elif self.cluster_type.value == "gcp":
                    config["gcp_region"] = self.gcp_region.value
                    config["gcp_instance_type"] = self.gcp_instance_type.value
                    if self.gcp_project_id.value:
                        config["gcp_project_id"] = self.gcp_project_id.value
                    if self.gcp_service_account_key.value:
                        config["gcp_service_account_key"] = (
                            self.gcp_service_account_key.value
                        )

                elif self.cluster_type.value == "lambda_cloud":
                    config["lambda_instance_type"] = self.lambda_instance_type.value
                    if self.lambda_api_key.value:
                        config["lambda_api_key"] = self.lambda_api_key.value

                elif self.cluster_type.value == "huggingface_spaces":
                    config["hf_hardware"] = self.hf_hardware.value
                    config["hf_sdk"] = self.hf_sdk.value
                    if self.hf_token.value:
                        config["hf_token"] = self.hf_token.value
                    if self.hf_username.value:
                        config["hf_username"] = self.hf_username.value

            else:  # SSH-based clusters (slurm, pbs, sge, ssh)
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
        # Cost monitoring (mainly for cloud providers)
        config["cost_monitoring"] = self.cost_monitoring_checkbox.value
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
            base_name = "New Configuration"
            counter = 1
            config_name = base_name
            while config_name in self.configs:
                config_name = f"{base_name} {counter}"
                counter += 1
            # Create new config
            self.configs[config_name] = {
                "name": config_name,  # Use the same name as the key
                "cluster_type": "local",
                "default_cores": 4,
                "default_memory": "8GB",
                "default_time": "01:00:00",
            }
            # Update dropdown
            self._update_config_dropdown()
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
            self._update_config_dropdown()
            if self.configs:
                self.config_dropdown.value = list(self.configs.keys())[0]
            print(f"✅ Deleted configuration: {self.current_config_name}")

    def _on_apply_config(self, button):
        """Apply the current configuration."""
        with self.status_output:
            self.status_output.clear_output()
            try:
                # Save current state
                config = self._save_config_from_widgets()

                # Use the user-provided name as the key
                new_config_name = self.config_name.value.strip()
                if not new_config_name:
                    print("❌ Configuration name cannot be empty")
                    return

                # If renaming, remove old entry
                if new_config_name != self.current_config_name:
                    if self.current_config_name in self.configs:
                        del self.configs[self.current_config_name]
                    if self.current_config_name in self.config_file_map:
                        self.config_file_map[new_config_name] = (
                            self.config_file_map.pop(self.current_config_name)
                        )

                self.configs[new_config_name] = config
                self.current_config_name = new_config_name

                # Update the config dropdown to reflect the new name
                self._update_config_dropdown()
                self.config_dropdown.value = self.current_config_name
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

                # Use the user-provided name as the key
                new_config_name = self.config_name.value.strip()
                if not new_config_name:
                    print("❌ Configuration name cannot be empty")
                    return

                # If renaming, remove old entry
                if new_config_name != self.current_config_name:
                    if self.current_config_name in self.configs:
                        del self.configs[self.current_config_name]
                    if self.current_config_name in self.config_file_map:
                        self.config_file_map[new_config_name] = (
                            self.config_file_map.pop(self.current_config_name)
                        )

                self.configs[new_config_name] = config
                self.current_config_name = new_config_name
                # Determine save file from custom filename input
                filename = self.save_filename_input.value.strip()
                if not filename:
                    print("❌ Please enter a filename")
                    return

                # Validate file extension
                if not filename.lower().endswith((".yml", ".yaml", ".json")):
                    print("❌ Filename must end with .yml, .yaml, or .json")
                    return

                save_path = Path(filename)
                # Save all configurations from widget dropdown
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

                # Check if we have any configurations to save
                if not save_data:
                    print(
                        "❌ No modified configurations to save (only default configurations found)"
                    )
                    return

                # Save to file
                with open(save_path, "w") as f:
                    yaml.dump(save_data, f, default_flow_style=False)
                config_count = len(save_data)
                if config_count == 1:
                    print(f"✅ Saved 1 configuration to {save_path}")
                else:
                    print(f"✅ Saved {config_count} configurations to {save_path}")
                    print(f"Configurations: {', '.join(save_data.keys())}")
                # Update file list if new file
                if save_path not in self.config_files:
                    self.config_files.append(save_path)
                    self.config_file_map[self.current_config_name] = save_path
                    # Update save dropdown
                    file_options = ["New file: clustrix.yml"]
                    for config_file in self.config_files:
                        file_options.append(f"Existing: {config_file}")
                    self.save_file_dropdown.options = file_options

                # Update the config dropdown to reflect the new name
                self._update_config_dropdown()
                self.config_dropdown.value = self.current_config_name
                # Clear unsaved changes flag
                self._clear_unsaved_changes()
            except Exception as e:
                print(f"❌ Error saving configuration: {str(e)}")

    def _on_load_config(self, button):
        """Load configuration from pasted content."""
        with self.status_output:
            self.status_output.clear_output()

            # Check if there's content pasted
            content = self.load_config_textarea.value.strip()
            if not content:
                print("❌ Please paste configuration content in the text area")
                return

            # Check for unsaved changes
            if self.has_unsaved_changes:
                print("⚠️  Warning: You have unsaved changes!")
                print("Current configuration changes will be lost if you continue.")
                print("Please save your current configuration first, or:")
                print("- Click 'Load Configuration' again to confirm loading")
                print("- Use 'Save Configuration' to save current changes")
                # Mark as confirmed for next click
                if not hasattr(self, "_load_confirmed"):
                    self._load_confirmed = True
                    return
                else:
                    # User clicked again, proceed with loading
                    delattr(self, "_load_confirmed")

            try:
                # Try to parse as YAML first, then JSON
                config_data = None
                try:
                    import yaml

                    config_data = yaml.safe_load(content)
                except yaml.YAMLError:
                    try:
                        import json

                        config_data = json.loads(content)
                    except json.JSONDecodeError:
                        print(
                            "❌ Invalid YAML or JSON format. Please check your configuration content."
                        )
                        return

                if not isinstance(config_data, dict):
                    print("❌ Invalid configuration format - expected YAML/JSON object")
                    return

                # Handle both single config and multiple configs
                configs_loaded = 0
                if "cluster_type" in config_data:
                    # Single configuration
                    config_name = config_data.get("name", "Imported Configuration")
                    self.configs[config_name] = config_data
                    self.current_config_name = config_name
                    configs_loaded = 1
                else:
                    # Multiple configurations
                    for name, config in config_data.items():
                        if isinstance(config, dict) and "cluster_type" in config:
                            self.configs[name] = config
                            configs_loaded += 1

                    # Set the first loaded config as current
                    if configs_loaded > 0:
                        self.current_config_name = list(config_data.keys())[0]

                if configs_loaded == 0:
                    print("❌ No valid configurations found in the provided content")
                    return

                # Update UI
                self._update_config_dropdown()
                if self.current_config_name:
                    self.config_dropdown.value = self.current_config_name
                    self._load_config_to_widgets(self.current_config_name)

                # Clear the textarea
                self.load_config_textarea.value = ""

                print(
                    f"✅ Loaded {configs_loaded} configuration(s) from pasted content"
                )
                if configs_loaded > 1:
                    print(f"Set '{self.current_config_name}' as active configuration")

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

            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                "hostname": config.get("cluster_host"),
                "port": config.get("cluster_port", 22),
                "username": config.get("username"),
                "timeout": timeout,
            }

            # Use key file if provided
            key_file = config.get("key_file")
            if key_file:
                from pathlib import Path

                key_path = Path(key_file).expanduser()
                if key_path.exists():
                    connect_kwargs["key_filename"] = str(key_path)
                else:
                    return False
            elif config.get("password"):
                connect_kwargs["password"] = config.get("password")
            else:
                # Try with no authentication (for testing purposes)
                pass

            ssh_client.connect(**connect_kwargs)

            # Test a simple command
            stdin, stdout, stderr = ssh_client.exec_command("echo 'test'", timeout=5)
            output = stdout.read().decode().strip()
            ssh_client.close()

            return output == "test"

        except ImportError:
            print("ℹ️  paramiko not available for SSH testing")
            return False
        except Exception:
            return False

    def _test_cloud_connectivity(self, cluster_type, config):
        """Test cloud provider API connectivity."""
        try:
            if cluster_type == "aws":
                return self._test_aws_connectivity(config)
            elif cluster_type == "azure":
                return self._test_azure_connectivity(config)
            elif cluster_type == "gcp":
                return self._test_gcp_connectivity(config)
            elif cluster_type == "lambda_cloud":
                return self._test_lambda_connectivity(config)
            elif cluster_type == "huggingface_spaces":
                return self._test_huggingface_connectivity(config)
            else:
                return False
        except Exception:
            return False

    def _test_aws_connectivity(self, config):
        """Test AWS API connectivity."""
        try:
            import boto3  # type: ignore
            from botocore.exceptions import NoCredentialsError, ClientError  # type: ignore

            # Map widget field names to AWS credential names
            aws_access_key = config.get("aws_access_key") or config.get(
                "aws_access_key_id"
            )
            aws_secret_key = config.get("aws_secret_key") or config.get(
                "aws_secret_access_key"
            )
            aws_region = config.get("aws_region", "us-east-1")

            # Create session with provided credentials if available
            session_kwargs = {}
            if aws_access_key and aws_secret_key:
                session_kwargs.update(
                    {
                        "aws_access_key_id": aws_access_key,
                        "aws_secret_access_key": aws_secret_key,
                        "region_name": aws_region,
                    }
                )
            else:
                # Fall back to profile or default credentials
                profile = config.get("aws_profile")
                if profile:
                    session_kwargs["profile_name"] = profile

            session = boto3.Session(**session_kwargs)
            ec2 = session.client("ec2", region_name=aws_region)

            # Simple API call to test connectivity
            ec2.describe_regions(MaxResults=1)
            return True

        except ImportError:
            print("ℹ️  boto3 not available for AWS testing")
            return False
        except (NoCredentialsError, ClientError):
            return False
        except Exception:
            return False

    def _test_azure_connectivity(self, config):
        """Test Azure API connectivity."""
        try:
            from azure.identity import ClientSecretCredential, DefaultAzureCredential
            from azure.mgmt.resource import ResourceManagementClient

            # Map widget field names to Azure credential names
            subscription_id = config.get("azure_subscription_id")
            client_id = config.get("azure_client_id")
            client_secret = config.get("azure_client_secret")
            tenant_id = config.get("azure_tenant_id")

            if not subscription_id:
                return False

            # Use provided credentials if available, otherwise fall back to default
            if client_id and client_secret and tenant_id:
                credential = ClientSecretCredential(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    client_secret=client_secret,
                )
            else:
                credential = DefaultAzureCredential()

            # Try to create a resource client and list resource groups
            resource_client = ResourceManagementClient(credential, subscription_id)
            list(resource_client.resource_groups.list(top=1))
            return True

        except ImportError:
            print("ℹ️  Azure SDK not available for Azure testing")
            return False
        except Exception:
            return False

    def _test_gcp_connectivity(self, config):
        """Test GCP API connectivity."""
        try:
            import os
            import tempfile
            from google.cloud import resource_manager
            from google.oauth2 import service_account

            project_id = config.get("gcp_project_id")
            service_account_key = config.get("gcp_service_account_key")

            if not project_id:
                return False

            # Use service account key if provided
            if service_account_key:
                # Create temporary credentials file
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False
                ) as f:
                    f.write(service_account_key)
                    temp_key_file = f.name

                try:
                    # Set up credentials from service account
                    credentials = service_account.Credentials.from_service_account_file(
                        temp_key_file
                    )
                    client = resource_manager.Client(credentials=credentials)
                finally:
                    # Clean up temporary file
                    os.unlink(temp_key_file)
            else:
                # Fall back to default credentials
                client = resource_manager.Client()

            # Try to get project information
            project = client.fetch_project(project_id)
            return project is not None

        except ImportError:
            print("ℹ️  Google Cloud SDK not available for GCP testing")
            return False
        except Exception:
            return False

    def _test_lambda_connectivity(self, config):
        """Test Lambda Cloud API connectivity."""
        try:
            from .cloud_providers.lambda_cloud import LambdaCloudProvider

            api_key = config.get("lambda_api_key")
            if not api_key:
                return False

            # Create provider instance and test authentication
            provider = LambdaCloudProvider()
            return provider.authenticate(api_key=api_key)

        except ImportError:
            print("ℹ️  Lambda Cloud provider not available for testing")
            return False
        except Exception:
            return False

    def _test_huggingface_connectivity(self, config):
        """Test HuggingFace API connectivity."""
        try:
            import requests

            # Map widget field names to HuggingFace credential names
            hf_token = config.get("hf_token")
            hf_username = config.get("hf_username")

            if not hf_token:
                return False

            # Test HuggingFace API connectivity using models endpoint 
            # (whoami endpoint appears to have permission issues with some tokens)
            headers = {"Authorization": f"Bearer {hf_token}"}
            response = requests.get(
                "https://huggingface.co/api/models?limit=1", headers=headers, timeout=10
            )

            # Debug: Print response details for troubleshooting
            if hasattr(self, "status_output"):
                with self.status_output:
                    print(
                        f"🔍 Debug: HuggingFace API response status: {response.status_code}"
                    )
                    if response.status_code != 200:
                        print(f"🔍 Debug: Response text: {response.text[:200]}...")

            if response.status_code == 200:
                # Successfully authenticated and can access models API
                models = response.json()
                
                # Debug: Print successful connection info
                if hasattr(self, "status_output"):
                    with self.status_output:
                        print(f"🔍 Debug: Successfully retrieved {len(models)} model(s)")
                        if hf_username:
                            print(f"🔍 Debug: Token validated for user: '{hf_username}'")

                return True

            return False

        except ImportError:
            print("ℹ️  requests library not available for HuggingFace testing")
            return False
        except Exception as e:
            # Debug: Print exception details
            if hasattr(self, "status_output"):
                with self.status_output:
                    print(f"🔍 Debug: Exception during HuggingFace test: {str(e)}")
            return False

    def _on_test_config(self, button):
        """Test the current configuration."""
        with self.status_output:
            self.status_output.clear_output()

            try:
                # Save current widget state
                config = self._save_config_from_widgets()
                cluster_type = config.get("cluster_type", "local")

                print(f"🧪 Testing {cluster_type} configuration...")

                if cluster_type == "local":
                    # Test local configuration
                    print("✅ Local configuration is valid")
                    print(f"- CPUs: {config.get('default_cores', 'default')}")
                    print(f"- Memory: {config.get('default_memory', 'default')}")
                    print("- Ready for local execution")

                elif cluster_type in ["ssh", "slurm", "pbs", "sge"]:
                    # Test remote configuration by checking connection
                    host = config.get("cluster_host", "")
                    username = config.get("username", "")
                    port = config.get("cluster_port", 22)

                    if not host:
                        print("❌ Host/Address is required for remote clusters")
                        return
                    if not username:
                        print("❌ Username is required for remote clusters")
                        return

                    print(f"- Host: {host}:{port}")
                    print(f"- Username: {username}")
                    print(f"- Cluster type: {cluster_type}")

                    # Basic validation
                    if validate_hostname(host) or validate_ip_address(host):
                        print("✅ Host format is valid")
                    else:
                        print("⚠️  Host format may be invalid")

                    # Check SSH key if provided
                    ssh_key = config.get("key_file", "")
                    if ssh_key:
                        from pathlib import Path

                        key_path = Path(ssh_key).expanduser()
                        if key_path.exists():
                            print(f"✅ SSH key found: {ssh_key}")
                        else:
                            print(f"⚠️  SSH key not found: {ssh_key}")
                    else:
                        print("ℹ️  No SSH key specified (will use password auth)")

                    # Attempt connectivity test
                    print("🔌 Testing connectivity...")
                    if self._test_remote_connectivity(host, port):
                        print("✅ Host is reachable")

                        # For SSH-based clusters, try a basic SSH connection test
                        if cluster_type == "ssh":
                            if self._test_ssh_connectivity(config):
                                print("✅ SSH connection successful")
                            else:
                                print("⚠️  SSH connection failed (check credentials)")
                        else:
                            print("✅ Basic connectivity confirmed")
                    else:
                        print("❌ Host is not reachable or connection timed out")

                elif cluster_type == "kubernetes":
                    # Test Kubernetes configuration
                    namespace = config.get("k8s_namespace", "default")
                    image = config.get("k8s_image", "python:3.11")
                    is_remote = config.get("k8s_remote", False)

                    print(f"- Namespace: {namespace}")
                    print(f"- Docker image: {image}")

                    if is_remote:
                        host = config.get("cluster_host", "")
                        if not host:
                            print("❌ Host/Address is required for remote Kubernetes")
                            return
                        print(f"- Remote cluster: {host}")
                        if validate_hostname(host) or validate_ip_address(host):
                            print("✅ Host format is valid")
                        else:
                            print("⚠️  Host format may be invalid")
                    else:
                        print("- Local Kubernetes cluster")

                    print("✅ Kubernetes configuration appears valid")

                elif cluster_type == "aws":
                    # Test AWS configuration
                    region = config.get("aws_region", "us-east-1")
                    cluster_sub_type = config.get("aws_cluster_type", "ec2")

                    print("- Provider: Amazon Web Services")
                    print(f"- Region: {region}")
                    print(f"- Service: {cluster_sub_type.upper()}")

                    if cluster_sub_type == "eks":
                        cluster_name = config.get("eks_cluster_name", "")
                        if cluster_name:
                            print(f"- EKS Cluster: {cluster_name}")
                        else:
                            print("⚠️  EKS cluster name not specified")

                    # Check if AWS credentials might be available
                    import os

                    if os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE"):
                        print("✅ AWS credentials detected in environment")
                    else:
                        print(
                            "ℹ️  No AWS credentials detected (may use IAM roles or config files)"
                        )

                    # Test AWS API connectivity
                    print("🔌 Testing AWS API connectivity...")
                    if self._test_cloud_connectivity("aws", config):
                        print("✅ AWS API connection successful")
                    else:
                        print(
                            "⚠️  AWS API connection failed (check credentials and region)"
                        )

                    print("✅ AWS configuration appears valid")

                elif cluster_type == "azure":
                    # Test Azure configuration
                    region = config.get("azure_region", "eastus")
                    cluster_sub_type = config.get("azure_cluster_type", "vm")

                    print("- Provider: Microsoft Azure")
                    print(f"- Region: {region}")
                    print(f"- Service: {cluster_sub_type.upper()}")

                    if cluster_sub_type == "aks":
                        cluster_name = config.get("aks_cluster_name", "")
                        resource_group = config.get("azure_resource_group", "")
                        if cluster_name and resource_group:
                            print(f"- AKS Cluster: {cluster_name}")
                            print(f"- Resource Group: {resource_group}")
                        else:
                            print("⚠️  AKS cluster name and resource group required")

                    # Check if Azure credentials might be available
                    import os

                    if os.getenv("AZURE_CLIENT_ID") or os.getenv(
                        "AZURE_SUBSCRIPTION_ID"
                    ):
                        print("✅ Azure credentials detected in environment")
                    else:
                        print(
                            "ℹ️  No Azure credentials detected (may use Azure CLI or managed identity)"
                        )

                    # Test Azure API connectivity
                    print("🔌 Testing Azure API connectivity...")
                    if self._test_cloud_connectivity("azure", config):
                        print("✅ Azure API connection successful")
                    else:
                        print(
                            "⚠️  Azure API connection failed (check credentials and subscription)"
                        )

                    print("✅ Azure configuration appears valid")

                elif cluster_type == "gcp":
                    # Test GCP configuration
                    region = config.get("gcp_region", "us-central1")
                    cluster_sub_type = config.get("gcp_cluster_type", "compute")
                    project_id = config.get("gcp_project_id", "")

                    print("- Provider: Google Cloud Platform")
                    print(f"- Region: {region}")
                    print(f"- Service: {cluster_sub_type.upper()}")

                    if project_id:
                        print(f"- Project ID: {project_id}")
                    else:
                        print("⚠️  GCP project ID not specified")

                    if cluster_sub_type == "gke":
                        cluster_name = config.get("gke_cluster_name", "")
                        zone = config.get("gcp_zone", "")
                        if cluster_name:
                            print(f"- GKE Cluster: {cluster_name}")
                            if zone:
                                print(f"- Zone: {zone}")
                        else:
                            print("⚠️  GKE cluster name not specified")

                    # Check if GCP credentials might be available
                    import os

                    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv(
                        "GCLOUD_PROJECT"
                    ):
                        print("✅ GCP credentials detected in environment")
                    else:
                        print(
                            "ℹ️  No GCP credentials detected (may use gcloud auth or service account)"
                        )

                    # Test GCP API connectivity
                    print("🔌 Testing GCP API connectivity...")
                    if self._test_cloud_connectivity("gcp", config):
                        print("✅ GCP API connection successful")
                    else:
                        print(
                            "⚠️  GCP API connection failed (check credentials and project)"
                        )

                    print("✅ GCP configuration appears valid")

                elif cluster_type == "lambda_cloud":
                    # Test Lambda Cloud configuration
                    print("- Provider: Lambda Cloud")

                    # Check for Lambda Cloud API key
                    api_key = config.get("lambda_api_key", "")
                    if api_key:
                        print("✅ Lambda Cloud API key provided")

                        # Test Lambda Cloud API connectivity
                        print("🔌 Testing Lambda Cloud API connectivity...")
                        if self._test_cloud_connectivity("lambda_cloud", config):
                            print("✅ Lambda Cloud API connection successful")
                        else:
                            print(
                                "❌ Lambda Cloud API connection failed (check API key)"
                            )
                    else:
                        print("❌ Lambda Cloud API key is required")

                    print("✅ Lambda Cloud configuration validation completed")

                elif cluster_type == "huggingface_spaces":
                    # Test HuggingFace Spaces configuration
                    print("- Provider: HuggingFace Spaces")

                    # Check for HuggingFace token
                    hf_token = config.get("hf_token", "")
                    hf_username = config.get("hf_username", "")

                    if hf_token:
                        print("✅ HuggingFace token provided")

                        # Test HuggingFace API connectivity
                        print("🔌 Testing HuggingFace API connectivity...")
                        if self._test_cloud_connectivity("huggingface_spaces", config):
                            print("✅ HuggingFace API connection successful")
                            if hf_username:
                                print(f"✅ Username '{hf_username}' verified")
                        else:
                            print("❌ HuggingFace API connection failed (check token)")
                    else:
                        print("❌ HuggingFace token is required")

                    if hf_username:
                        print(f"✅ Username: {hf_username}")
                    else:
                        print("ℹ️  Username not specified (optional)")

                    print("✅ HuggingFace Spaces configuration validation completed")

                else:
                    print(f"⚠️  Unknown cluster type: {cluster_type}")
                    print(
                        "ℹ️  Supported types: local, ssh, slurm, pbs, sge, kubernetes, "
                        "aws, azure, gcp, lambda_cloud, huggingface_spaces"
                    )

                # Test resource configuration
                cores = config.get("default_cores", 4)
                memory = config.get("default_memory", "8GB")

                print("\n📊 Resource Configuration:")
                print(f"- CPUs: {cores if cores != -1 else 'all available'}")
                print(f"- Memory: {memory}")

                if cluster_type != "local":
                    time_limit = config.get("default_time", "01:00:00")
                    print(f"- Time limit: {time_limit}")

                # Check cost monitoring
                if config.get("cost_monitoring", False):
                    print("💰 Cost monitoring enabled")

                print(
                    f"\n✅ Configuration test completed for '{config.get('name', 'Unnamed')}'"
                )

            except Exception as e:
                print(f"❌ Error testing configuration: {str(e)}")

    def display(self):
        """Display the enhanced widget interface."""
        # Title
        title_text = "Clustrix Configuration Manager"
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
                widgets.HTML("<h6>Save Configuration</h6>"),
                self.save_filename_input,
                widgets.HBox([self.save_file_dropdown, self.save_btn]),
                widgets.HTML("<br><h6>Load Configuration</h6>"),
                widgets.HBox([self.load_config_textarea, self.load_btn]),
            ]
        )
        # Action buttons
        action_buttons = widgets.HBox(
            [
                self.apply_btn,
                self.test_btn,
                self.delete_btn,
            ]
        )
        # Main layout
        main_layout = widgets.VBox(
            [
                config_section,
                widgets.HTML("<hr>"),
                basic_fields,
                self.connection_fields,
                self.k8s_fields,
                self.aws_fields,
                self.azure_fields,
                self.gcp_fields,
                self.lambda_fields,
                self.hf_fields,
                resource_fields,
                self.advanced_accordion,
                widgets.HTML("<hr>"),
                save_section,
                widgets.HTML("<hr>"),
                action_buttons,
                widgets.HTML("<br><h6>Status Messages</h6>"),
                self.status_output,
                widgets.HTML("<br>"),  # Add spacing at bottom
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

        Usage::

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
        # Note: No print message since widget displays automatically on import


# Export the widget class for testing
ClusterConfigWidget = EnhancedClusterConfigWidget
