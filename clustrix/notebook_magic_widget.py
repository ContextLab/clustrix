"""
Enhanced interactive widget for managing Clustrix configurations.

This module contains the main EnhancedClusterConfigWidget class that provides
a comprehensive interface for configuring and managing cluster settings in
Jupyter notebooks.
"""

from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

from .notebook_magic_config import (
    DEFAULT_CONFIGS,
    detect_config_files,
    load_config_from_file,
    validate_ip_address,
    validate_hostname,
)

try:
    from IPython.display import display, HTML
    import ipywidgets as widgets  # type: ignore

    IPYTHON_AVAILABLE = True
except ImportError:
    IPYTHON_AVAILABLE = False
    from .notebook_magic_mocks import display, HTML, widgets

from .config import configure

logger = logging.getLogger(__name__)


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
            value="/tmp/clustrix",
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
        """Update the configuration dropdown with current config names."""
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

    def _on_config_name_change(self, change):
        """Handle changes to the config name field."""
        new_name = change["new"].strip()
        if not new_name:
            return
        # Update the configuration if we have one loaded
        if (
            self.current_config_name
            and self.current_config_name in self.configs
            and new_name != self.current_config_name
        ):
            # Rename the configuration
            old_config = self.configs.pop(self.current_config_name)
            old_config["name"] = new_name
            self.configs[new_name] = old_config
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
        # Kubernetes specific fields
        self.k8s_namespace_field = widgets.Text(
            description="K8s Namespace:",
            value="default",
            placeholder="Kubernetes namespace",
            tooltip="Kubernetes namespace to deploy jobs in",
            style=style,
            layout=half_layout,
        )
        self.k8s_image_field = widgets.Text(
            description="Container Image:",
            value="python:3.11",
            placeholder="e.g., python:3.11, ubuntu:20.04",
            tooltip="Docker image to use for job containers",
            style=style,
            layout=full_layout,
        )
        # Kubernetes remote checkbox
        self.k8s_remote_checkbox = widgets.Checkbox(
            value=False,
            description="Remote Kubernetes Cluster",
            tooltip="Check if this is a remote Kubernetes cluster (requires SSH)",
            style={"description_width": "160px"},
            layout=widgets.Layout(width="300px"),
        )
        self.k8s_remote_checkbox.observe(self._on_k8s_remote_change, names="value")
        # Cloud provider specific fields
        # AWS fields
        self.aws_region_field = widgets.Dropdown(
            options=[
                "us-east-1",
                "us-east-2",
                "us-west-1",
                "us-west-2",
                "eu-west-1",
                "eu-west-2",
                "eu-central-1",
                "ap-southeast-1",
                "ap-southeast-2",
                "ap-northeast-1",
            ],
            value="us-east-1",
            description="AWS Region:",
            tooltip="AWS region for your resources",
            style=style,
            layout=half_layout,
        )
        self.aws_region_field.observe(self._on_aws_region_change, names="value")
        self.aws_instance_type_field = widgets.Dropdown(
            options=[
                "t3.micro",
                "t3.small",
                "t3.medium",
                "t3.large",
                "t3.xlarge",
                "m5.large",
                "m5.xlarge",
                "m5.2xlarge",
                "c5.large",
                "c5.xlarge",
                "c5.2xlarge",
                "r5.large",
                "r5.xlarge",
                "r5.2xlarge",
            ],
            value="t3.medium",
            description="Instance Type:",
            tooltip="AWS EC2 instance type",
            style=style,
            layout=half_layout,
        )
        self.aws_cluster_type_field = widgets.Dropdown(
            options=["ec2", "eks", "batch"],
            value="ec2",
            description="AWS Service:",
            tooltip="AWS service to use (EC2, EKS, or Batch)",
            style=style,
            layout=half_layout,
        )
        self.aws_access_key_field = widgets.Text(
            description="Access Key ID:",
            placeholder="Your AWS access key ID",
            tooltip="AWS Access Key ID for authentication",
            style=style,
            layout=full_layout,
        )
        self.aws_secret_key_field = widgets.Text(
            description="Secret Key:",
            placeholder="Your AWS secret access key",
            tooltip="AWS Secret Access Key for authentication",
            style=style,
            layout=full_layout,
        )
        # Azure fields
        self.azure_region_field = widgets.Dropdown(
            options=[
                "eastus",
                "eastus2",
                "westus",
                "westus2",
                "centralus",
                "northeurope",
                "westeurope",
                "eastasia",
                "southeastasia",
                "japaneast",
            ],
            value="eastus",
            description="Azure Region:",
            tooltip="Azure region for your resources",
            style=style,
            layout=half_layout,
        )
        self.azure_region_field.observe(self._on_azure_region_change, names="value")
        self.azure_instance_type_field = widgets.Dropdown(
            options=[
                "Standard_B1s",
                "Standard_B2s",
                "Standard_D2s_v3",
                "Standard_D4s_v3",
                "Standard_D8s_v3",
                "Standard_E2s_v3",
                "Standard_E4s_v3",
                "Standard_E8s_v3",
                "Standard_F2s_v2",
                "Standard_F4s_v2",
            ],
            value="Standard_D2s_v3",
            description="VM Size:",
            tooltip="Azure virtual machine size",
            style=style,
            layout=half_layout,
        )
        self.azure_subscription_field = widgets.Text(
            description="Subscription ID:",
            placeholder="Your Azure subscription ID",
            tooltip="Azure subscription ID",
            style=style,
            layout=full_layout,
        )
        self.azure_client_id_field = widgets.Text(
            description="Client ID:",
            placeholder="Service principal client ID",
            tooltip="Azure service principal client ID",
            style=style,
            layout=half_layout,
        )
        self.azure_client_secret_field = widgets.Text(
            description="Client Secret:",
            placeholder="Service principal client secret",
            tooltip="Azure service principal client secret",
            style=style,
            layout=half_layout,
        )
        self.azure_tenant_id_field = widgets.Text(
            description="Tenant ID:",
            placeholder="Azure tenant ID",
            tooltip="Azure Active Directory tenant ID",
            style=style,
            layout=full_layout,
        )
        # GCP fields
        self.gcp_region_field = widgets.Dropdown(
            options=[
                "us-central1",
                "us-east1",
                "us-west1",
                "us-west2",
                "europe-west1",
                "europe-west2",
                "europe-west3",
                "asia-east1",
                "asia-southeast1",
                "asia-northeast1",
            ],
            value="us-central1",
            description="GCP Region:",
            tooltip="Google Cloud region",
            style=style,
            layout=half_layout,
        )
        self.gcp_region_field.observe(self._on_gcp_region_change, names="value")
        self.gcp_instance_type_field = widgets.Dropdown(
            options=[
                "e2-micro",
                "e2-small",
                "e2-medium",
                "e2-standard-2",
                "e2-standard-4",
                "n1-standard-1",
                "n1-standard-2",
                "n1-standard-4",
                "n2-standard-2",
                "n2-standard-4",
            ],
            value="e2-medium",
            description="Machine Type:",
            tooltip="Google Cloud machine type",
            style=style,
            layout=half_layout,
        )
        self.gcp_project_field = widgets.Text(
            description="Project ID:",
            placeholder="Your GCP project ID",
            tooltip="Google Cloud project ID",
            style=style,
            layout=half_layout,
        )
        self.gcp_zone_field = widgets.Text(
            description="Zone:",
            placeholder="e.g., us-central1-a",
            tooltip="Google Cloud zone within the region",
            style=style,
            layout=half_layout,
        )
        self.gcp_credentials_field = widgets.Textarea(
            description="Service Account:",
            placeholder="Paste JSON service account key here",
            tooltip="Google Cloud service account JSON key",
            rows=5,
            style=style,
            layout=full_layout,
        )
        # Lambda Cloud fields
        self.lambda_api_key_field = widgets.Text(
            description="API Key:",
            placeholder="Your Lambda Cloud API key",
            tooltip="Lambda Cloud API key for authentication",
            style=style,
            layout=full_layout,
        )
        self.lambda_instance_type_field = widgets.Dropdown(
            options=[
                "gpu_1x_a10",
                "gpu_1x_a100",
                "gpu_2x_a100",
                "gpu_4x_a100",
                "gpu_8x_a100",
                "gpu_1x_v100",
            ],
            value="gpu_1x_a10",
            description="Instance Type:",
            tooltip="Lambda Cloud instance type",
            style=style,
            layout=half_layout,
        )
        # HuggingFace fields
        self.hf_token_field = widgets.Text(
            description="HF Token:",
            placeholder="Your HuggingFace access token",
            tooltip="HuggingFace access token for authentication",
            style=style,
            layout=full_layout,
        )
        self.hf_space_name_field = widgets.Text(
            description="Space Name:",
            placeholder="e.g., my-awesome-space",
            tooltip="Name of the HuggingFace Space to create",
            style=style,
            layout=half_layout,
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
            tooltip="HuggingFace Space hardware tier",
            style=style,
            layout=half_layout,
        )
        self.hf_sdk_field = widgets.Dropdown(
            options=["gradio", "streamlit", "static"],
            value="gradio",
            description="SDK:",
            tooltip="HuggingFace Space SDK to use",
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
        # Cost monitoring checkbox
        self.cost_monitoring_checkbox = widgets.Checkbox(
            value=False,
            description="Cost Monitoring",
            tooltip="Enable cost tracking for cloud providers",
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
            self.cost_monitoring_checkbox,
            self.env_vars_field,
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
        # Kubernetes fields
        self.kubernetes_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Kubernetes Settings</h5>"),
                self.k8s_remote_checkbox,
                widgets.HBox([self.k8s_namespace_field, widgets.HTML("")]),
                self.k8s_image_field,
            ],
            layout=widgets.Layout(
                border="1px solid #ddd",
                padding="10px",
                margin="10px 0px",
                display="none",
            ),
        )
        # Cloud provider fields containers
        self.aws_fields = widgets.VBox(
            [
                widgets.HTML("<h5>AWS Settings</h5>"),
                widgets.HBox([self.aws_region_field, self.aws_instance_type_field]),
                widgets.HBox([self.aws_cluster_type_field, widgets.HTML("")]),
                self.aws_access_key_field,
                self.aws_secret_key_field,
            ],
            layout=widgets.Layout(
                border="1px solid #ddd",
                padding="10px",
                margin="10px 0px",
                display="none",
            ),
        )
        self.azure_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Azure Settings</h5>"),
                widgets.HBox([self.azure_region_field, self.azure_instance_type_field]),
                self.azure_subscription_field,
                widgets.HBox(
                    [self.azure_client_id_field, self.azure_client_secret_field]
                ),
                self.azure_tenant_id_field,
            ],
            layout=widgets.Layout(
                border="1px solid #ddd",
                padding="10px",
                margin="10px 0px",
                display="none",
            ),
        )
        self.gcp_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Google Cloud Settings</h5>"),
                widgets.HBox([self.gcp_region_field, self.gcp_instance_type_field]),
                widgets.HBox([self.gcp_project_field, self.gcp_zone_field]),
                self.gcp_credentials_field,
            ],
            layout=widgets.Layout(
                border="1px solid #ddd",
                padding="10px",
                margin="10px 0px",
                display="none",
            ),
        )
        self.lambda_fields = widgets.VBox(
            [
                widgets.HTML("<h5>Lambda Cloud Settings</h5>"),
                self.lambda_api_key_field,
                widgets.HBox([self.lambda_instance_type_field, widgets.HTML("")]),
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
                widgets.HTML("<h5>HuggingFace Settings</h5>"),
                self.hf_token_field,
                widgets.HBox([self.hf_space_name_field, self.hf_hardware_field]),
                widgets.HBox([self.hf_sdk_field, widgets.HTML("")]),
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
        self.kubernetes_fields.layout.display = "none"
        self.aws_fields.layout.display = "none"
        self.azure_fields.layout.display = "none"
        self.gcp_fields.layout.display = "none"
        self.lambda_fields.layout.display = "none"
        self.hf_fields.layout.display = "none"

        # Show relevant sections based on cluster type
        if cluster_type in ["ssh", "slurm", "pbs", "sge"]:
            self.connection_fields.layout.display = ""
        elif cluster_type == "kubernetes":
            self.kubernetes_fields.layout.display = ""
            self._update_kubernetes_connection_visibility()
        elif cluster_type == "aws":
            self.aws_fields.layout.display = ""
            # Populate AWS options if available
            self._populate_cloud_provider_options("aws")
        elif cluster_type == "azure":
            self.azure_fields.layout.display = ""
            self._populate_cloud_provider_options("azure")
        elif cluster_type == "gcp":
            self.gcp_fields.layout.display = ""
            self._populate_cloud_provider_options("gcp")
        elif cluster_type == "lambda_cloud":
            self.lambda_fields.layout.display = ""
        elif cluster_type == "huggingface_spaces":
            self.hf_fields.layout.display = ""

        # Update time field visibility (only for cluster schedulers)
        if cluster_type in ["slurm", "pbs", "sge"]:
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
            if provider_class is None:
                # Fallback to default options
                self._set_default_cloud_options(provider)
                return

            # Initialize the provider
            provider_instance = provider_class()

            if provider == "aws":
                # Get AWS regions and instance types
                regions = provider_instance.get_available_regions()
                if regions:
                    self.aws_region_field.options = regions

                instance_types = provider_instance.get_available_instance_types()
                if instance_types:
                    self.aws_instance_type_field.options = instance_types

            elif provider == "azure":
                # Get Azure regions and VM sizes
                regions = provider_instance.get_available_regions()
                if regions:
                    self.azure_region_field.options = regions

                vm_sizes = provider_instance.get_available_instance_types()
                if vm_sizes:
                    self.azure_instance_type_field.options = vm_sizes

            elif provider == "gcp":
                # Get GCP regions and machine types
                regions = provider_instance.get_available_regions()
                if regions:
                    self.gcp_region_field.options = regions

                machine_types = provider_instance.get_available_instance_types()
                if machine_types:
                    self.gcp_instance_type_field.options = machine_types

        except Exception as e:
            # If cloud provider API is unavailable, use default options
            logger.warning(f"Could not load {provider} options: {e}")
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
                    "m5.large",
                    "c5.large",
                    "r5.large",
                ],
            },
            "azure": {
                "regions": ["eastus", "westus", "northeurope", "westeurope"],
                "instances": [
                    "Standard_B1s",
                    "Standard_B2s",
                    "Standard_D2s_v3",
                    "Standard_D4s_v3",
                    "Standard_E2s_v3",
                    "Standard_F2s_v2",
                ],
            },
            "gcp": {
                "regions": ["us-central1", "us-east1", "europe-west1", "asia-east1"],
                "instances": [
                    "e2-micro",
                    "e2-small",
                    "e2-medium",
                    "e2-standard-2",
                    "n1-standard-1",
                    "n2-standard-2",
                ],
            },
        }

        if provider in defaults:
            config = defaults[provider]
            if provider == "aws":
                self.aws_region_field.options = config["regions"]
                self.aws_instance_type_field.options = config["instances"]
            elif provider == "azure":
                self.azure_region_field.options = config["regions"]
                self.azure_instance_type_field.options = config["instances"]
            elif provider == "gcp":
                self.gcp_region_field.options = config["regions"]
                self.gcp_instance_type_field.options = config["instances"]

    def _on_aws_region_change(self, change):
        """Handle AWS region change to update available instance types."""
        try:
            from .cloud_providers import PROVIDERS

            provider_class = PROVIDERS.get("aws")
            if provider_class:
                provider_instance = provider_class()
                instance_types = provider_instance.get_instance_types(
                    region=change["new"]
                )
                if instance_types:
                    self.aws_instance_type_field.options = instance_types
        except Exception:
            pass  # Keep current options

    def _on_azure_region_change(self, change):
        """Handle Azure region change to update available instance types."""
        try:
            from .cloud_providers import PROVIDERS

            provider_class = PROVIDERS.get("azure")
            if provider_class:
                provider_instance = provider_class()
                vm_sizes = provider_instance.get_vm_sizes(region=change["new"])
                if vm_sizes:
                    self.azure_instance_type_field.options = vm_sizes
        except Exception:
            pass  # Keep current options

    def _on_gcp_region_change(self, change):
        """Handle GCP region change to update available instance types."""
        try:
            from .cloud_providers import PROVIDERS

            provider_class = PROVIDERS.get("gcp")
            if provider_class:
                provider_instance = provider_class()
                machine_types = provider_instance.get_machine_types(
                    region=change["new"]
                )
                if machine_types:
                    self.gcp_instance_type_field.options = machine_types
        except Exception:
            pass  # Keep current options

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
        self.work_dir_field.value = config.get("remote_work_dir", "/tmp/clustrix")

        # Connection fields
        self.host_field.value = config.get("cluster_host", "")
        self.username_field.value = config.get("username", "")
        self.password_field.value = config.get("password", "")
        self.port_field.value = config.get("cluster_port", 22)

        # Kubernetes fields
        self.k8s_namespace_field.value = config.get("k8s_namespace", "default")
        self.k8s_image_field.value = config.get("k8s_image", "python:3.11")
        self.k8s_remote_checkbox.value = config.get("k8s_remote", False)

        # AWS fields
        self.aws_region_field.value = config.get("aws_region", "us-east-1")
        self.aws_instance_type_field.value = config.get(
            "aws_instance_type", "t3.medium"
        )
        self.aws_cluster_type_field.value = config.get("aws_cluster_type", "ec2")
        self.aws_access_key_field.value = config.get("aws_access_key_id", "")
        self.aws_secret_key_field.value = config.get("aws_secret_access_key", "")

        # Azure fields
        self.azure_region_field.value = config.get("azure_region", "eastus")
        self.azure_instance_type_field.value = config.get(
            "azure_instance_type", "Standard_D2s_v3"
        )
        self.azure_subscription_field.value = config.get("azure_subscription_id", "")
        self.azure_client_id_field.value = config.get("azure_client_id", "")
        self.azure_client_secret_field.value = config.get("azure_client_secret", "")
        self.azure_tenant_id_field.value = config.get("azure_tenant_id", "")

        # GCP fields
        self.gcp_region_field.value = config.get("gcp_region", "us-central1")
        self.gcp_instance_type_field.value = config.get(
            "gcp_instance_type", "e2-medium"
        )
        self.gcp_project_field.value = config.get("gcp_project", "")
        self.gcp_zone_field.value = config.get("gcp_zone", "")
        self.gcp_credentials_field.value = config.get("gcp_credentials", "")

        # Lambda Cloud fields
        self.lambda_api_key_field.value = config.get("lambda_api_key", "")
        self.lambda_instance_type_field.value = config.get(
            "lambda_instance_type", "gpu_1x_a10"
        )

        # HuggingFace fields
        self.hf_token_field.value = config.get("hf_token", "")
        self.hf_space_name_field.value = config.get("hf_space_name", "")
        self.hf_hardware_field.value = config.get("hf_hardware", "cpu-basic")
        self.hf_sdk_field.value = config.get("hf_sdk", "gradio")

        # Advanced options
        self.package_manager.value = config.get("package_manager", "pip")
        self.cost_monitoring_checkbox.value = config.get("cost_monitoring", False)

        # Environment variables
        env_vars = config.get("environment_variables", {})
        if env_vars:
            import json

            self.env_vars_field.value = json.dumps(env_vars, indent=2)
        else:
            self.env_vars_field.value = ""

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
            "cost_monitoring": self.cost_monitoring_checkbox.value,
            "queue": self.queue_field.value,
            "ssh_key_path": self.ssh_key_field.value,
        }

        # Include password only if provided
        if self.password_field.value:
            config["password"] = self.password_field.value

        # Kubernetes specific fields
        if self.cluster_type.value == "kubernetes":
            config.update(
                {
                    "k8s_namespace": self.k8s_namespace_field.value,
                    "k8s_image": self.k8s_image_field.value,
                    "k8s_remote": self.k8s_remote_checkbox.value,
                }
            )

        # AWS specific fields
        elif self.cluster_type.value == "aws":
            config.update(
                {
                    "aws_region": self.aws_region_field.value,
                    "aws_instance_type": self.aws_instance_type_field.value,
                    "aws_cluster_type": self.aws_cluster_type_field.value,
                }
            )
            # Include credentials only if provided
            if self.aws_access_key_field.value:
                config["aws_access_key_id"] = self.aws_access_key_field.value
            if self.aws_secret_key_field.value:
                config["aws_secret_access_key"] = self.aws_secret_key_field.value

        # Azure specific fields
        elif self.cluster_type.value == "azure":
            config.update(
                {
                    "azure_region": self.azure_region_field.value,
                    "azure_instance_type": self.azure_instance_type_field.value,
                }
            )
            # Include credentials only if provided
            if self.azure_subscription_field.value:
                config["azure_subscription_id"] = self.azure_subscription_field.value
            if self.azure_client_id_field.value:
                config["azure_client_id"] = self.azure_client_id_field.value
            if self.azure_client_secret_field.value:
                config["azure_client_secret"] = self.azure_client_secret_field.value
            if self.azure_tenant_id_field.value:
                config["azure_tenant_id"] = self.azure_tenant_id_field.value

        # GCP specific fields
        elif self.cluster_type.value == "gcp":
            config.update(
                {
                    "gcp_region": self.gcp_region_field.value,
                    "gcp_instance_type": self.gcp_instance_type_field.value,
                }
            )
            # Include credentials only if provided
            if self.gcp_project_field.value:
                config["gcp_project"] = self.gcp_project_field.value
            if self.gcp_zone_field.value:
                config["gcp_zone"] = self.gcp_zone_field.value
            if self.gcp_credentials_field.value:
                config["gcp_credentials"] = self.gcp_credentials_field.value

        # Lambda Cloud specific fields
        elif self.cluster_type.value == "lambda_cloud":
            config.update(
                {
                    "lambda_instance_type": self.lambda_instance_type_field.value,
                }
            )
            if self.lambda_api_key_field.value:
                config["lambda_api_key"] = self.lambda_api_key_field.value

        # HuggingFace specific fields
        elif self.cluster_type.value == "huggingface_spaces":
            config.update(
                {
                    "hf_hardware": self.hf_hardware_field.value,
                    "hf_sdk": self.hf_sdk_field.value,
                }
            )
            if self.hf_token_field.value:
                config["hf_token"] = self.hf_token_field.value
            if self.hf_space_name_field.value:
                config["hf_space_name"] = self.hf_space_name_field.value

        # Environment variables
        if self.env_vars_field.value.strip():
            try:
                import json

                env_vars = json.loads(self.env_vars_field.value)
                if env_vars:
                    config["environment_variables"] = env_vars
            except json.JSONDecodeError:
                pass  # Ignore invalid JSON

        # Remove empty string values
        config = {k: v for k, v in config.items() if v != ""}
        return config

    def _on_config_select(self, change):
        """Handle configuration selection from dropdown."""
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

            # Save current widget state as new config
            config_data = self._save_config_from_widgets()
            config_data["name"] = config_name
            self.configs[config_name] = config_data
            self.current_config_name = config_name

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
                # Remove from file map if it exists
                if self.current_config_name in self.config_file_map:
                    del self.config_file_map[self.current_config_name]
                # Select a different configuration
                remaining_configs = list(self.configs.keys())
                if remaining_configs:
                    self._load_config_to_widgets(remaining_configs[0])
                self._update_config_dropdown()
                print(f"✅ Deleted configuration: '{deleted_name}'")

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
                # Apply to Clustrix
                configure(**config_data)
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

                # Determine save directory
                save_dir = Path.home() / ".clustrix"
                save_dir.mkdir(exist_ok=True)
                file_path = save_dir / filename

                # Prepare data to save
                if len(self.configs) == 1 and self.current_config_name:
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

                # Save to file
                import yaml

                with open(file_path, "w") as f:
                    yaml.dump(save_data, f, default_flow_style=False, sort_keys=False)

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
            config_dirs = [Path.home() / ".clustrix", Path(".")]
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
                    config_name = data.get("name", "Loaded Configuration")
                    self.configs[config_name] = data
                    self.current_config_name = config_name
                    self._load_config_to_widgets(config_name)
                    self._update_config_dropdown()
                    print(f"✅ Loaded configuration: '{config_name}'")
                else:
                    # Multiple configurations
                    loaded_count = 0
                    for name, config in data.items():
                        if isinstance(config, dict) and "cluster_type" in config:
                            config["name"] = name
                            self.configs[name] = config
                            loaded_count += 1

                    if loaded_count > 0:
                        # Load the first configuration
                        first_config = next(iter(data.keys()))
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

            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

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
                return False, f"Cloud testing not implemented for {cluster_type}"
        except Exception as e:
            return False, f"Cloud connectivity test failed: {str(e)}"

    def _test_aws_connectivity(self, config):
        """Test AWS API connectivity with proper field mapping."""
        try:
            import boto3  # type: ignore
            from botocore.exceptions import NoCredentialsError, ClientError  # type: ignore
            from .field_mappings import (
                map_widget_fields_to_provider,
                validate_provider_config,
            )

            # Map widget fields to provider fields
            provider_config = map_widget_fields_to_provider(config, "aws")

            # Validate configuration
            is_valid, missing_fields = validate_provider_config(provider_config, "aws")
            if not is_valid:
                return False, f"Missing required fields: {', '.join(missing_fields)}"

            # Create boto3 session
            session_params = {}
            if provider_config.get("aws_access_key_id"):
                session_params["aws_access_key_id"] = provider_config[
                    "aws_access_key_id"
                ]
            if provider_config.get("aws_secret_access_key"):
                session_params["aws_secret_access_key"] = provider_config[
                    "aws_secret_access_key"
                ]

            session = boto3.Session(**session_params)

            # Test EC2 connectivity
            region = provider_config.get("region", "us-east-1")
            ec2_client = session.client("ec2", region_name=region)

            # Try to describe regions (basic API call)
            response = ec2_client.describe_regions()
            if response.get("Regions"):
                return True, "AWS connectivity successful"
            else:
                return False, "No AWS regions returned"

        except ImportError:
            return False, "boto3 not installed. Run: pip install boto3"
        except NoCredentialsError:
            return False, "AWS credentials not configured"
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "UnauthorizedOperation":
                return (
                    True,
                    "AWS credentials valid (got authorization error on describe_regions)",
                )
            else:
                return False, f"AWS API error: {error_code}"
        except Exception as e:
            return False, f"AWS connectivity failed: {str(e)}"

    def _test_azure_connectivity(self, config):
        """Test Azure API connectivity with proper field mapping."""
        try:
            from azure.identity import ClientSecretCredential
            from .field_mappings import (
                map_widget_fields_to_provider,
                validate_provider_config,
            )

            # Map widget fields to provider fields
            provider_config = map_widget_fields_to_provider(config, "azure")

            # Validate configuration
            is_valid, missing_fields = validate_provider_config(
                provider_config, "azure"
            )
            if not is_valid:
                return False, f"Missing required fields: {', '.join(missing_fields)}"

            # Create credentials
            credential = ClientSecretCredential(
                tenant_id=provider_config["tenant_id"],
                client_id=provider_config["client_id"],
                client_secret=provider_config["client_secret"],
            )

            # Test token acquisition
            token = credential.get_token("https://management.azure.com/.default")
            if token and token.token:
                return True, "Azure connectivity successful"
            else:
                return False, "Could not acquire Azure token"

        except ImportError:
            return (
                False,
                "Azure SDK not installed. Run: pip install azure-identity azure-mgmt-compute",
            )
        except Exception as e:
            return False, f"Azure connectivity failed: {str(e)}"

    def _test_gcp_connectivity(self, config):
        """Test GCP API connectivity with proper field mapping."""
        try:
            import json
            from google.cloud import resourcemanager
            from google.oauth2 import service_account
            from .field_mappings import (
                map_widget_fields_to_provider,
                validate_provider_config,
            )

            # Map widget fields to provider fields
            provider_config = map_widget_fields_to_provider(config, "gcp")

            # Validate configuration
            is_valid, missing_fields = validate_provider_config(provider_config, "gcp")
            if not is_valid:
                return False, f"Missing required fields: {', '.join(missing_fields)}"

            # Parse credentials
            credentials_json = provider_config.get("credentials_json")
            if not credentials_json:
                return False, "No GCP credentials provided"

            try:
                creds_dict = json.loads(credentials_json)
            except json.JSONDecodeError:
                return False, "Invalid JSON in GCP credentials"

            # Create credentials object
            credentials = service_account.Credentials.from_service_account_info(
                creds_dict
            )

            # Test connectivity
            client = resourcemanager.Client(credentials=credentials)

            # Try to list projects (basic API call)
            projects = list(client.list_projects())
            return True, f"GCP connectivity successful (found {len(projects)} projects)"

        except ImportError:
            return (
                False,
                "Google Cloud SDK not installed. Run: pip install google-cloud-resource-manager",
            )
        except Exception as e:
            return False, f"GCP connectivity failed: {str(e)}"

    def _test_lambda_connectivity(self, config):
        """Test Lambda Cloud API connectivity."""
        try:
            from .cloud_providers.lambda_cloud import LambdaCloudProvider

            api_key = config.get("lambda_api_key")
            if not api_key:
                return False, "Lambda Cloud API key not provided"

            provider = LambdaCloudProvider(api_key=api_key)
            instances = provider.list_instances()

            return (
                True,
                f"Lambda Cloud connectivity successful ({len(instances)} instances)",
            )

        except ImportError:
            return False, "Lambda Cloud provider not available"
        except Exception as e:
            return False, f"Lambda Cloud connectivity failed: {str(e)}"

    def _test_huggingface_connectivity(self, config):
        """Test HuggingFace API connectivity with proper field mapping."""
        try:
            from .field_mappings import (
                map_widget_fields_to_provider,
                validate_provider_config,
            )

            # Map widget fields to provider fields
            provider_config = map_widget_fields_to_provider(
                config, "huggingface_spaces"
            )

            # Validate configuration
            is_valid, missing_fields = validate_provider_config(
                provider_config, "huggingface_spaces"
            )
            if not is_valid:
                return False, f"Missing required fields: {', '.join(missing_fields)}"

            # Test HuggingFace API
            import requests

            token = provider_config.get("token")
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

                elif cluster_type in ["ssh", "slurm", "pbs", "sge"]:
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

                elif cluster_type == "kubernetes":
                    k8s_remote = config_data.get("k8s_remote", False)
                    if k8s_remote:
                        # Test SSH connectivity for remote K8s
                        host = config_data.get("cluster_host")
                        if host:
                            print(
                                f"🌐 Testing connectivity to remote Kubernetes at {host}..."
                            )
                            # Same SSH tests as above
                            port = config_data.get("cluster_port", 22)
                            if not self._test_remote_connectivity(host, port):
                                print(f"❌ Cannot reach {host}:{port}")
                                return
                            print("✅ Remote Kubernetes connectivity successful")
                        else:
                            print("❌ Host required for remote Kubernetes")
                    else:
                        print("✅ Local Kubernetes configuration")
                        print("💡 Ensure kubectl is configured for your cluster")

                elif cluster_type in [
                    "aws",
                    "azure",
                    "gcp",
                    "lambda_cloud",
                    "huggingface_spaces",
                ]:
                    # Test cloud provider connectivity
                    print(f"☁️  Testing {cluster_type.upper()} API connectivity...")
                    result = self._test_cloud_connectivity(cluster_type, config_data)

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

                if cluster_type not in ["ssh", "slurm", "pbs", "sge", "kubernetes"]:
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
                self.kubernetes_fields,
                self.aws_fields,
                self.azure_fields,
                self.gcp_fields,
                self.lambda_fields,
                self.hf_fields,
            ]
        )
        # Advanced options accordion
        advanced_content = widgets.VBox(
            [
                widgets.HBox([self.package_manager, self.cost_monitoring_checkbox]),
                self.env_vars_field,
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
