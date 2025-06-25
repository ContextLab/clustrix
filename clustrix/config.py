import os
import json
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
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
    
    # Resource defaults
    default_cores: int = 4
    default_memory: str = "8GB"
    default_time: str = "01:00:00"
    default_partition: Optional[str] = None
    default_queue: Optional[str] = None
    
    # Paths
    remote_work_dir: str = "/tmp/clusterpy"
    local_cache_dir: str = "~/.clusterpy/cache"
    conda_env_name: Optional[str] = None
    python_executable: str = "python"
    
    # Execution preferences
    auto_parallel: bool = True
    max_parallel_jobs: int = 100
    job_poll_interval: int = 30
    cleanup_on_success: bool = True
    
    # Advanced settings
    environment_variables: Dict[str, str] = None
    module_loads: list = None
    pre_execution_commands: list = None
    
    def __post_init__(self):
        if self.environment_variables is None:
            self.environment_variables = {}
        if self.module_loads is None:
            self.module_loads = []
        if self.pre_execution_commands is None:
            self.pre_execution_commands = []

# Global configuration instance
_config = ClusterConfig()


def configure(**kwargs) -> None:
    """
    Configure ClusterPy settings.
    
    Args:
        **kwargs: Configuration parameters matching ClusterConfig fields
    """
    global _config
    
    # Update configuration with provided kwargs
    for key, value in kwargs.items():
        if hasattr(_config, key):
            setattr(_config, key, value)
        else:
            raise ValueError(f"Unknown configuration parameter: {key}")


def load_config(config_path: str) -> None:
    """
    Load configuration from a file (JSON or YAML).
    
    Args:
        config_path: Path to configuration file
    """
    global _config
    
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_path, 'r') as f:
        if config_path.suffix.lower() in ['.yml', '.yaml']:
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
    config_path = Path(config_path)
    config_data = asdict(_config)
    
    with open(config_path, 'w') as f:
        if config_path.suffix.lower() in ['.yml', '.yaml']:
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
        Path.home() / ".clusterpy" / "config.yml",
        Path.home() / ".clusterpy" / "config.yaml",
        Path.home() / ".clusterpy" / "config.json",
        Path.cwd() / "clusterpy.yml",
        Path.cwd() / "clusterpy.yaml",
        Path.cwd() / "clusterpy.json",
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