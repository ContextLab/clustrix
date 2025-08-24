"""
Configuration management for notebook magic functionality.

This module contains default configurations and configuration-related utilities
for the notebook magic interface.
"""

import json
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
        "environment_variables": {
            "CUDA_VISIBLE_DEVICES": "0",
            "NVIDIA_VISIBLE_DEVICES": "all",
        },
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


def load_config_from_file(file_path: Union[Path, str]) -> Dict[str, Any]:
    """Load configuration from a YAML or JSON file."""
    try:
        # Convert string to Path if needed
        if isinstance(file_path, str):
            file_path = Path(file_path)

        # Read and parse the file
        content = file_path.read_text()
        if file_path.suffix.lower() in [".yml", ".yaml"]:
            return yaml.safe_load(content) or {}
        elif file_path.suffix.lower() == ".json":
            return json.loads(content)
        else:
            # Try YAML first, then JSON
            try:
                return yaml.safe_load(content) or {}
            except yaml.YAMLError:
                return json.loads(content)
    except Exception:
        return {}


def validate_ip_address(ip: str) -> bool:
    """Validate IP address format."""
    if not ip:
        return False
    # IPv4 validation
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        for part in parts:
            if not (0 <= int(part) <= 255):
                return False
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
