import json
import yaml
import os
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass, asdict


@dataclass
class ClusterConfig:
    """Configuration settings for cluster execution."""

    # Authentication
    api_key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    key_file: Optional[str] = None

    # Cluster settings
    cluster_type: str = "slurm"  # slurm, pbs, sge, kubernetes, ssh
    cluster_host: Optional[str] = None
    cluster_port: int = 22

    # Kubernetes-specific settings
    k8s_namespace: str = "default"
    k8s_image: str = "python:3.11-slim"
    k8s_service_account: Optional[str] = None
    k8s_pull_policy: str = "IfNotPresent"
    k8s_job_ttl_seconds: int = 3600
    k8s_backoff_limit: int = 3
    k8s_remote: bool = False

    # Cloud provider settings for remote Kubernetes
    cloud_provider: str = "manual"  # manual, aws, azure, gcp
    cloud_region: Optional[str] = None
    cloud_auto_configure: bool = False

    # AWS-specific settings
    aws_profile: Optional[str] = None
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_access_key: Optional[str] = None  # Alternative name used by widget
    aws_secret_key: Optional[str] = None  # Alternative name used by widget
    aws_instance_type: Optional[str] = None
    aws_cluster_type: Optional[str] = None  # ec2 or eks
    eks_cluster_name: Optional[str] = None
    aws_region: Optional[str] = None

    # Azure-specific settings
    azure_subscription_id: Optional[str] = None
    azure_resource_group: Optional[str] = None
    azure_tenant_id: Optional[str] = None
    azure_client_id: Optional[str] = None
    azure_client_secret: Optional[str] = None
    azure_instance_type: Optional[str] = None
    aks_cluster_name: Optional[str] = None
    azure_region: Optional[str] = None

    # GCP-specific settings
    gcp_project_id: Optional[str] = None
    gcp_zone: Optional[str] = None
    gcp_service_account_key: Optional[str] = None
    gcp_instance_type: Optional[str] = None
    gke_cluster_name: Optional[str] = None
    gcp_region: Optional[str] = None

    # Lambda Cloud settings
    lambda_instance_type: Optional[str] = None
    lambda_api_key: Optional[str] = None

    # Hugging Face Spaces settings
    hf_hardware: Optional[str] = None
    hf_token: Optional[str] = None
    hf_username: Optional[str] = None
    hf_sdk: Optional[str] = None

    # Resource defaults
    default_cores: int = 4
    default_memory: str = "8GB"
    default_time: str = "01:00:00"
    default_partition: Optional[str] = None
    default_queue: Optional[str] = None

    # Paths
    remote_work_dir: str = "/tmp/clustrix"
    local_work_dir: Optional[str] = None  # If None, uses current working directory
    local_cache_dir: str = "~/.clustrix/cache"
    conda_env_name: Optional[str] = None
    python_executable: str = "python"
    package_manager: str = "pip"  # pip, uv, or auto

    # Execution preferences
    auto_parallel: bool = True
    auto_gpu_parallel: bool = True  # Automatically parallelize across GPUs when available
    max_parallel_jobs: int = 100
    max_gpu_parallel_jobs: int = 8  # Maximum parallel jobs per GPU
    job_poll_interval: int = 30
    cleanup_on_success: bool = True
    prefer_local_parallel: bool = False
    local_parallel_threshold: int = 1000  # Use local if iterations < threshold
    async_submit: bool = False  # Use asynchronous job submission

    # Monitoring settings
    cost_monitoring: bool = False  # Enable cost monitoring for cloud providers

    # Enhanced Authentication Options
    use_1password: bool = False  # Enable 1Password integration
    onepassword_note: str = ""  # 1Password secure note name
    use_env_password: bool = False  # Enable environment variable password
    password_env_var: str = ""  # Name of environment variable containing password
    cache_credentials: bool = True  # Cache credentials in memory
    credential_cache_ttl: int = 300  # Credential cache TTL in seconds (5 minutes)
    ssh_port: int = 22  # SSH port (for consistency with cluster_port)

    # Advanced settings
    environment_variables: Optional[Dict[str, str]] = None
    module_loads: Optional[list] = None
    pre_execution_commands: Optional[list] = None

    # Cluster-specific package and setup configuration
    cluster_packages: Optional[list] = None  # Additional packages to install in VENV2
    venv_post_install_commands: Optional[list] = (
        None  # Commands to run after package installation
    )

    # Runtime venv information (set during execution)
    venv_info: Optional[dict] = None  # Information about created virtual environments

    def __post_init__(self):
        if self.environment_variables is None:
            self.environment_variables = {}
        if self.module_loads is None:
            self.module_loads = []
        if self.pre_execution_commands is None:
            self.pre_execution_commands = []
        if self.cluster_packages is None:
            self.cluster_packages = []
        if self.venv_post_install_commands is None:
            self.venv_post_install_commands = []

        # Auto-install cloud provider dependencies if needed
        self._ensure_cloud_dependencies()

    def _ensure_cloud_dependencies(self) -> None:
        """Ensure cloud provider dependencies are available for this configuration."""
        try:
            from .auto_install import ensure_cloud_provider_dependencies

            ensure_cloud_provider_dependencies(
                cluster_type=self.cluster_type,
                cloud_provider=self.cloud_provider,
                auto_install=True,
                quiet=True,  # Quiet in constructor to avoid spam
            )
        except Exception:
            # Silently fail in constructor to avoid breaking imports
            pass

    def get_env_password(self) -> Optional[str]:
        """Get password from specified environment variable."""
        if self.use_env_password and self.password_env_var:
            return os.environ.get(self.password_env_var)
        return None

    def save_to_file(self, config_path: str) -> None:
        """Save this configuration instance to a file."""
        config_path_obj = Path(config_path)
        config_data = asdict(self)

        with open(config_path_obj, "w") as f:
            if config_path_obj.suffix.lower() in [".yml", ".yaml"]:
                yaml.dump(config_data, f, default_flow_style=False)
            else:
                json.dump(config_data, f, indent=2)

    @classmethod
    def load_from_file(cls, config_path: str) -> "ClusterConfig":
        """Load configuration from a file and return a new instance."""
        config_path_obj = Path(config_path)
        if not config_path_obj.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path_obj, "r") as f:
            if config_path_obj.suffix.lower() in [".yml", ".yaml"]:
                config_data = yaml.safe_load(f)
            else:
                config_data = json.load(f)

        return cls(**config_data)


# Global configuration instance
_config = ClusterConfig()


def configure(auto_install_deps: bool = True, **kwargs) -> None:
    """
    Configure Clustrix settings.

    Args:
        auto_install_deps: Whether to automatically install cloud provider dependencies
        **kwargs: Configuration parameters matching ClusterConfig fields
    """
    global _config  # noqa: F824

    # Update configuration with provided kwargs
    for key, value in kwargs.items():
        if hasattr(_config, key):
            setattr(_config, key, value)
        else:
            raise ValueError(f"Unknown configuration parameter: {key}")

    # Check if we need to install cloud provider dependencies
    if auto_install_deps:
        from .auto_install import ensure_cloud_provider_dependencies

        cluster_type = kwargs.get("cluster_type", _config.cluster_type)
        cloud_provider = kwargs.get("cloud_provider", _config.cloud_provider)

        # Try to ensure dependencies, but don't fail if installation fails
        try:
            ensure_cloud_provider_dependencies(
                cluster_type=cluster_type,
                cloud_provider=cloud_provider,
                auto_install=True,
                quiet=False,
            )
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Could not auto-install cloud provider dependencies: {e}")


def load_config(config_path: str) -> None:
    """
    Load configuration from a file (JSON or YAML).

    Args:
        config_path: Path to configuration file
    """
    global _config

    config_path_obj = Path(config_path)
    if not config_path_obj.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path_obj, "r") as f:
        if config_path_obj.suffix.lower() in [".yml", ".yaml"]:
            config_data = yaml.safe_load(f)
        else:
            config_data = json.load(f)

    _config = ClusterConfig(**config_data)


def save_config(config_path: str) -> None:
    """
    Save current configuration to a file.

    Args:
        config_path: Path where to save configuration
    """
    config_path_obj = Path(config_path)
    config_data = asdict(_config)

    with open(config_path_obj, "w") as f:
        if config_path_obj.suffix.lower() in [".yml", ".yaml"]:
            yaml.dump(config_data, f, default_flow_style=False)
        else:
            json.dump(config_data, f, indent=2)


def get_config() -> ClusterConfig:
    """Get current configuration."""
    return _config


# Try to load configuration from default locations
def _load_default_config():
    """Load configuration from default locations."""
    default_paths = [
        Path.home() / ".clustrix" / "config.yml",
        Path.home() / ".clustrix" / "config.yaml",
        Path.home() / ".clustrix" / "config.json",
        Path.cwd() / "clustrix.yml",
        Path.cwd() / "clustrix.yaml",
        Path.cwd() / "clustrix.json",
    ]

    for path in default_paths:
        if path.exists():
            try:
                load_config(str(path))
                break
            except Exception:
                continue


# Load default configuration on import
_load_default_config()
