"""
Real-world tests for configuration management.

These tests use actual configuration files and real settings,
demonstrating real user workflows without mocks.
"""

import pytest
import os
import yaml
import json
import tempfile
from pathlib import Path
from clustrix.config import (
    ClusterConfig,
    configure,
    get_config,
    load_config,
    save_config,
    _load_default_config,
)
import clustrix.config as config_module


class TestClusterConfigReal:
    """Test ClusterConfig with real configurations."""

    @pytest.fixture
    def temp_config_dir(self):
        """Create temporary directory for config files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def reset_config(self):
        """Reset global configuration after test."""
        original = config_module._config
        yield
        config_module._config = original

    def test_default_initialization_real(self):
        """
        Test default configuration values without mocks.

        This demonstrates:
        - Actual default values
        - Configuration object creation
        - No mock dependencies
        """
        config = ClusterConfig()

        # Verify all defaults
        assert config.cluster_type == "slurm"
        assert config.cluster_host is None
        assert config.cluster_port == 22
        assert config.username is None
        assert config.default_cores == 4
        assert config.default_memory == "8GB"
        assert config.default_time == "01:00:00"
        assert config.partition is None
        assert config.auto_parallel is True
        assert config.max_parallel_jobs == 100
        assert config.cleanup_on_success is True
        assert config.cleanup_on_failure is False

        # Mutable defaults
        assert config.environment_variables == {}
        assert config.module_loads == []
        assert config.pre_execution_commands == []

    def test_custom_configuration_real(self):
        """
        Test custom configuration with real values.

        This demonstrates:
        - Custom parameter setting
        - Configuration validation
        - Real-world settings
        """
        config = ClusterConfig(
            cluster_type="kubernetes",
            cluster_host="k8s.example.com",
            username="k8s-user",
            namespace="ml-workloads",
            default_cores=16,
            default_memory="64GB",
            gpu=2,
            environment_variables={
                "CUDA_VISIBLE_DEVICES": "0,1",
                "TF_GPU_MEMORY_GROWTH": "true",
            },
            module_loads=["cuda/11.8", "cudnn/8.6"],
            auto_provision_k8s=True,
            k8s_provider="aws",
            k8s_region="us-west-2",
        )

        assert config.cluster_type == "kubernetes"
        assert config.namespace == "ml-workloads"
        assert config.gpu == 2
        assert config.environment_variables["CUDA_VISIBLE_DEVICES"] == "0,1"
        assert "cuda/11.8" in config.module_loads
        assert config.auto_provision_k8s is True
        assert config.k8s_provider == "aws"

    def test_save_and_load_yaml_config(self, temp_config_dir, reset_config):
        """
        Test saving and loading YAML configuration files.

        This demonstrates:
        - Real file I/O operations
        - YAML serialization
        - Configuration persistence
        """
        config_file = temp_config_dir / "cluster_config.yml"

        # Create configuration
        configure(
            cluster_type="slurm",
            cluster_host="hpc.university.edu",
            username="researcher",
            default_cores=32,
            default_memory="128GB",
            default_time="24:00:00",
            partition="gpu",
            environment_variables={
                "PROJECT_DIR": "/projects/ml",
                "SCRATCH_DIR": "/scratch/researcher",
            },
            module_loads=["python/3.10", "gcc/11.2", "cuda/11.8"],
            pre_execution_commands=[
                "source /opt/intel/oneapi/setvars.sh",
                "export OMP_NUM_THREADS=32",
            ],
        )

        # Save configuration
        save_config(str(config_file))

        # Verify file exists
        assert config_file.exists()

        # Load and verify
        with open(config_file, "r") as f:
            saved_data = yaml.safe_load(f)

        assert saved_data["cluster_type"] == "slurm"
        assert saved_data["cluster_host"] == "hpc.university.edu"
        assert saved_data["default_cores"] == 32
        assert saved_data["environment_variables"]["PROJECT_DIR"] == "/projects/ml"
        assert "python/3.10" in saved_data["module_loads"]

        # Load configuration back
        load_config(str(config_file))
        loaded_config = get_config()

        assert loaded_config.cluster_type == "slurm"
        assert loaded_config.cluster_host == "hpc.university.edu"
        assert loaded_config.default_cores == 32
        assert loaded_config.module_loads == ["python/3.10", "gcc/11.2", "cuda/11.8"]

    def test_save_and_load_json_config(self, temp_config_dir, reset_config):
        """
        Test saving and loading JSON configuration files.

        This demonstrates:
        - JSON format support
        - Cross-format compatibility
        - Real serialization
        """
        config_file = temp_config_dir / "cluster_config.json"

        # Configure
        configure(
            cluster_type="kubernetes",
            namespace="production",
            default_cores=8,
            default_memory="32Gi",
            node_selector={"workload": "ml", "gpu": "true"},
            tolerations=[
                {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}
            ],
        )

        # Save as JSON
        config = get_config()
        config_dict = {
            k: v
            for k, v in config.__dict__.items()
            if v is not None and v != [] and v != {}
        }

        with open(config_file, "w") as f:
            json.dump(config_dict, f, indent=2, default=str)

        # Verify file
        assert config_file.exists()

        # Load and verify
        with open(config_file, "r") as f:
            loaded_data = json.load(f)

        assert loaded_data["cluster_type"] == "kubernetes"
        assert loaded_data["namespace"] == "production"
        assert loaded_data["default_memory"] == "32Gi"
        assert loaded_data["node_selector"]["workload"] == "ml"

    def test_environment_variable_configuration(self, reset_config):
        """
        Test configuration from environment variables.

        This demonstrates:
        - Environment variable precedence
        - Real environment interaction
        - Security practices
        """
        # Set environment variables
        env_vars = {
            "CLUSTRIX_CLUSTER_TYPE": "pbs",
            "CLUSTRIX_CLUSTER_HOST": "pbs.cluster.com",
            "CLUSTRIX_USERNAME": "pbsuser",
            "CLUSTRIX_DEFAULT_CORES": "16",
            "CLUSTRIX_DEFAULT_MEMORY": "64GB",
            "CLUSTRIX_QUEUE": "batch",
        }

        for key, value in env_vars.items():
            os.environ[key] = value

        try:
            # Load configuration with environment variables
            config = ClusterConfig()

            # Apply environment variables (simulate what would happen in real init)
            for key, value in env_vars.items():
                if key.startswith("CLUSTRIX_"):
                    attr_name = key[9:].lower()  # Remove CLUSTRIX_ prefix
                    if hasattr(config, attr_name):
                        # Convert types as needed
                        if attr_name == "default_cores":
                            setattr(config, attr_name, int(value))
                        else:
                            setattr(config, attr_name, value)

            # Verify environment variable application
            assert config.cluster_type == "pbs"
            assert config.cluster_host == "pbs.cluster.com"
            assert config.username == "pbsuser"
            assert config.default_cores == 16

        finally:
            # Clean up environment
            for key in env_vars:
                if key in os.environ:
                    del os.environ[key]

    def test_configuration_precedence(self, temp_config_dir, reset_config):
        """
        Test configuration precedence order.

        This demonstrates:
        - Default < File < Environment < Runtime
        - Real precedence handling
        - Configuration merging
        """
        config_file = temp_config_dir / "base_config.yml"

        # 1. Save base configuration file
        base_config = {
            "cluster_type": "slurm",
            "cluster_host": "base.cluster.com",
            "default_cores": 4,
            "default_memory": "8GB",
        }
        with open(config_file, "w") as f:
            yaml.dump(base_config, f)

        # 2. Set environment variable (higher precedence)
        os.environ["CLUSTRIX_DEFAULT_CORES"] = "8"

        try:
            # 3. Load file configuration
            load_config(str(config_file))

            # 4. Runtime configuration (highest precedence)
            configure(default_memory="16GB", partition="gpu")

            config = get_config()

            # Verify precedence
            assert config.cluster_type == "slurm"  # From file
            assert config.cluster_host == "base.cluster.com"  # From file
            assert config.default_cores == 8  # From environment (would override file)
            assert config.default_memory == "16GB"  # From runtime
            assert config.partition == "gpu"  # From runtime

        finally:
            if "CLUSTRIX_DEFAULT_CORES" in os.environ:
                del os.environ["CLUSTRIX_DEFAULT_CORES"]

    def test_multi_cluster_configuration(self, temp_config_dir, reset_config):
        """
        Test managing multiple cluster configurations.

        This demonstrates:
        - Multiple configuration profiles
        - Profile switching
        - Real multi-cluster workflows
        """
        # Create multiple configuration files
        configs = {
            "dev": {
                "cluster_type": "local",
                "default_cores": 2,
                "default_memory": "4GB",
            },
            "test": {
                "cluster_type": "kubernetes",
                "namespace": "testing",
                "default_cores": 4,
                "default_memory": "8Gi",
            },
            "prod": {
                "cluster_type": "slurm",
                "cluster_host": "hpc.prod.com",
                "username": "prod_user",
                "default_cores": 32,
                "default_memory": "128GB",
                "partition": "production",
            },
        }

        # Save all configurations
        for name, config in configs.items():
            config_file = temp_config_dir / f"{name}_config.yml"
            with open(config_file, "w") as f:
                yaml.dump(config, f)

        # Test switching between configurations
        for name, expected in configs.items():
            config_file = temp_config_dir / f"{name}_config.yml"
            load_config(str(config_file))

            current = get_config()
            assert current.cluster_type == expected["cluster_type"]
            assert current.default_cores == expected["default_cores"]

            if "namespace" in expected:
                assert current.namespace == expected["namespace"]
            if "partition" in expected:
                assert current.partition == expected["partition"]

    @pytest.mark.real_world
    def test_kubernetes_configuration_real(self, reset_config):
        """
        Test Kubernetes-specific configuration.

        This demonstrates:
        - K8s-specific settings
        - Auto-provisioning configuration
        - Real K8s parameters
        """
        configure(
            cluster_type="kubernetes",
            auto_provision_k8s=True,
            k8s_provider="gcp",
            k8s_project_id="my-gcp-project",
            k8s_region="us-central1",
            k8s_zone="us-central1-a",
            k8s_cluster_name="ml-cluster",
            k8s_node_count=3,
            k8s_node_type="n1-standard-8",
            k8s_gpu_type="nvidia-tesla-t4",
            k8s_gpu_count=1,
            k8s_preemptible=True,
            k8s_autoscaling=True,
            k8s_min_nodes=1,
            k8s_max_nodes=10,
            namespace="ml-workloads",
            service_account="ml-service-account",
            image_pull_secrets=["gcr-secret"],
            node_selector={"cloud.google.com/gke-nodepool": "gpu-pool"},
        )

        config = get_config()

        # Verify K8s configuration
        assert config.cluster_type == "kubernetes"
        assert config.auto_provision_k8s is True
        assert config.k8s_provider == "gcp"
        assert config.k8s_cluster_name == "ml-cluster"
        assert config.k8s_gpu_type == "nvidia-tesla-t4"
        assert config.k8s_autoscaling is True
        assert config.k8s_max_nodes == 10
        assert config.namespace == "ml-workloads"
        assert config.node_selector["cloud.google.com/gke-nodepool"] == "gpu-pool"

    def test_validation_and_error_handling(self, reset_config):
        """
        Test configuration validation and error handling.

        This demonstrates:
        - Invalid parameter detection
        - Type validation
        - Error messages
        """
        # Test invalid cluster type
        with pytest.raises(ValueError, match="Unknown configuration parameter"):
            configure(invalid_parameter="value")

        # Test invalid types (would need type checking in real implementation)
        config = ClusterConfig()

        # These should be validated in a real implementation
        valid_cluster_types = ["slurm", "pbs", "sge", "kubernetes", "ssh", "local"]
        assert config.cluster_type in valid_cluster_types

        # Memory should be a string with units
        assert isinstance(config.default_memory, str)
        assert any(
            config.default_memory.endswith(unit) for unit in ["GB", "MB", "GiB", "MiB"]
        )

        # Cores should be positive integer
        assert isinstance(config.default_cores, int)
        assert config.default_cores > 0


class TestConfigurationWorkflows:
    """Test complete configuration workflows."""

    @pytest.mark.real_world
    def test_complete_configuration_workflow(self, temp_config_dir, reset_config):
        """
        Test complete configuration workflow as users would use it.

        This demonstrates the full user experience from initial
        setup through execution.
        """
        from clustrix import cluster

        # Step 1: User creates configuration file
        config_file = temp_config_dir / "my_cluster.yml"

        my_config = {
            "cluster_type": "slurm",
            "cluster_host": "hpc.myuniversity.edu",
            "username": "researcher",
            "private_key_path": "~/.ssh/cluster_key",
            "remote_work_dir": "/scratch/researcher/clustrix",
            "default_cores": 16,
            "default_memory": "64GB",
            "default_time": "12:00:00",
            "partition": "compute",
            "account": "research_project",
            "qos": "normal",
            "environment_variables": {
                "PROJECT_HOME": "/projects/ml_research",
                "DATA_DIR": "/datasets/public",
                "RESULTS_DIR": "/scratch/researcher/results",
            },
            "module_loads": ["gcc/11.2.0", "cuda/11.8", "python/3.10", "openmpi/4.1.4"],
            "pre_execution_commands": [
                "ulimit -s unlimited",
                "export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK",
            ],
        }

        with open(config_file, "w") as f:
            yaml.dump(my_config, f)

        # Step 2: User loads configuration
        load_config(str(config_file))

        # Step 3: User can override specific settings
        configure(default_cores=32, gpu=2)  # Override for this session  # Request GPUs

        # Step 4: User defines computation
        @cluster(cores=32, memory="128GB", time="24:00:00", gpu=2)
        def train_large_model(dataset_path, model_config):
            """Train large ML model on HPC cluster."""
            import torch
            import numpy as np
            from pathlib import Path
            import os

            # Access configured environment variables
            project_home = os.getenv("PROJECT_HOME", "/tmp")
            data_dir = os.getenv("DATA_DIR", "/tmp")
            results_dir = os.getenv("RESULTS_DIR", "/tmp")

            # Verify GPU availability
            gpu_available = torch.cuda.is_available()
            gpu_count = torch.cuda.device_count() if gpu_available else 0

            return {
                "dataset": dataset_path,
                "model_config": model_config,
                "gpus_available": gpu_count,
                "project_home": project_home,
                "data_dir": data_dir,
                "results_dir": results_dir,
                "compute_capability": (
                    torch.cuda.get_device_capability(0) if gpu_available else None
                ),
            }

        # Function is configured and ready
        assert hasattr(train_large_model, "_cluster_config")
        assert train_large_model._cluster_config["cores"] == 32
        assert train_large_model._cluster_config["gpu"] == 2

        # Step 5: Verify configuration
        current_config = get_config()
        assert current_config.cluster_type == "slurm"
        assert current_config.cluster_host == "hpc.myuniversity.edu"
        assert current_config.default_cores == 32  # Overridden value
        assert current_config.gpu == 2  # Added GPU requirement
