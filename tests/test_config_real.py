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
    SUPPORTED_CLUSTER_TYPES,
    configure,
    get_config,
    load_config,
    save_config,
    _load_default_config,
)
import clustrix.config as config_module


@pytest.fixture
def temp_config_dir():
    """Create temporary directory for config files.

    Module-level (not class-scoped) so both TestClusterConfigReal and
    TestConfigurationWorkflows can use it -- it used to live only inside
    TestClusterConfigReal, which meant tests in TestConfigurationWorkflows
    that request it hit "fixture 'temp_config_dir' not found" (Issue #114).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestClusterConfigReal:
    """Test ClusterConfig with real configurations."""

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
        # "partition" was never a ClusterConfig field; the real name is
        # "default_partition" (Issue #114).
        assert config.default_partition is None
        assert config.auto_parallel is True
        assert config.max_parallel_jobs == 100
        assert config.cleanup_on_success is True
        # "cleanup_on_failure" has never existed on ClusterConfig (`git log
        # -S` finds no commit introducing it) -- only "cleanup_on_success"
        # does. Not asserted (Issue #114).

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
        # Restated over SLURM. This used to configure Kubernetes via
        # k8s_namespace/auto_provision_k8s/k8s_provider/k8s_region; that
        # backend and all four fields were removed (issue #142) because it had
        # never been run against a real cluster. "gpu" is a per-job @cluster
        # decorator kwarg, not a ClusterConfig field, so it is not passed
        # here (Issue #114).
        config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="hpc.example.com",
            username="hpc-user",
            default_partition="gpu",
            default_cores=16,
            default_memory="64GB",
            environment_variables={
                "CUDA_VISIBLE_DEVICES": "0,1",
                "TF_GPU_MEMORY_GROWTH": "true",
            },
            module_loads=["cuda/11.8", "cudnn/8.6"],
        )

        assert config.cluster_type == "slurm"
        assert config.default_partition == "gpu"
        assert config.environment_variables["CUDA_VISIBLE_DEVICES"] == "0,1"
        assert "cuda/11.8" in config.module_loads

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
            default_partition="gpu",  # "partition" is not a real field name
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
        assert "python/3.10" in saved_data["module_loads"]
        # Rewritten, not relaxed. This used to assert that PROJECT_DIR
        # survived the save, which encoded the rule that each
        # environment_variables entry is judged by its key name -- and that
        # rule leaked SSH_PASSPHRASE, GITHUB_PAT, a DATABASE_URL with the
        # password in it and USE_PASSWORD. The names and values in that
        # mapping are the user's, so clustrix cannot tell a setting from a
        # token; it now withholds the mapping and says so rather than
        # guessing. save_to_file(include_secrets=True) writes it.
        assert "environment_variables" not in saved_data

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
        # Restated over HuggingFace Jobs: the Kubernetes backend this used to
        # configure has been removed (issue #142). The round trip is the point.
        configure(
            cluster_type="huggingface",
            hf_namespace="contextlab",
            default_cores=8,
            default_memory="32Gi",
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

        assert loaded_data["cluster_type"] == "huggingface"
        assert loaded_data["hf_namespace"] == "contextlab"
        assert loaded_data["default_memory"] == "32Gi"

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
            "CLUSTRIX_CLUSTER_TYPE": "ssh",
            "CLUSTRIX_CLUSTER_HOST": "gpu.cluster.com",
            "CLUSTRIX_USERNAME": "sshuser",
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
            assert config.cluster_type == "ssh"
            assert config.cluster_host == "gpu.cluster.com"
            assert config.username == "sshuser"
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
        - Default < File < Runtime
        - Real precedence handling
        - Configuration merging

        NOTE: ClusterConfig/configure()/load_config() have no mechanism that
        reads CLUSTRIX_<FIELD> environment variables to override config
        values -- the only environment variable config.py itself consults is
        CLUSTRIX_CONFIG_DIR (which controls *where* config files are found,
        not values inside them). The original version of this test set
        CLUSTRIX_DEFAULT_CORES expecting load_config()/configure() to pick
        it up automatically; that never happened in the real implementation.
        (test_environment_variable_configuration, elsewhere in this file,
        has to hand-apply env vars via setattr() with a comment admitting
        it is simulating "what would happen in a real init" -- i.e. this
        layer is documented in CLAUDE.md's "Configuration Priority" section
        but not actually wired up; see the Issue #114 report.) This test
        now only exercises the precedence that is real: Default < File <
        Runtime.
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

        # 2. Load file configuration
        load_config(str(config_file))

        # 3. Runtime configuration (highest precedence)
        configure(default_memory="16GB", default_partition="gpu", default_cores=8)

        config = get_config()

        # Verify precedence
        assert config.cluster_type == "slurm"  # From file
        assert config.cluster_host == "base.cluster.com"  # From file
        assert config.default_cores == 8  # From runtime, overriding file's 4
        assert config.default_memory == "16GB"  # From runtime
        assert config.default_partition == "gpu"  # From runtime

    def test_multi_cluster_configuration(self, temp_config_dir, reset_config):
        """
        Test managing multiple cluster configurations.

        This demonstrates:
        - Multiple configuration profiles
        - Profile switching
        - Real multi-cluster workflows
        """
        # Create multiple configuration files
        # "partition" is not a real field name; the real one is
        # "default_partition" (Issue #114). The middle profile used to be a
        # Kubernetes one keyed on k8s_namespace, a removed backend and a
        # removed field (issue #142).
        configs = {
            "dev": {
                "cluster_type": "local",
                "default_cores": 2,
                "default_memory": "4GB",
            },
            "test": {
                "cluster_type": "huggingface",
                "hf_namespace": "contextlab",
                "default_cores": 4,
                "default_memory": "8Gi",
            },
            "prod": {
                "cluster_type": "slurm",
                "cluster_host": "hpc.prod.com",
                "username": "prod_user",
                "default_cores": 32,
                "default_memory": "128GB",
                "default_partition": "production",
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

            if "hf_namespace" in expected:
                assert current.hf_namespace == expected["hf_namespace"]
            if "default_partition" in expected:
                assert current.default_partition == expected["default_partition"]

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

        # Read the shipped tuple rather than a second hand-maintained copy:
        # the old literal list here still named pbs/sge/kubernetes after those
        # backends were removed.
        assert config.cluster_type in SUPPORTED_CLUSTER_TYPES

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

        # "private_key_path" is not a real field (the real one is
        # "key_file"). "partition" is not a real field (the real one is
        # "default_partition"). "account"/"qos" are not ClusterConfig
        # fields at all -- SLURM --account/--qos passthrough is a genuine
        # gap, not a test bug; see Issue #114 report. (Issue #114)
        my_config = {
            "cluster_type": "slurm",
            "cluster_host": "hpc.myuniversity.edu",
            "username": "researcher",
            "key_file": "~/.ssh/cluster_key",
            "remote_work_dir": "/scratch/researcher/clustrix",
            "default_cores": 16,
            "default_memory": "64GB",
            "default_time": "12:00:00",
            "default_partition": "compute",
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
        # "gpu" is not a ClusterConfig field (it's a per-job @cluster
        # decorator kwarg, exercised below), so it is not passed to
        # configure() here (Issue #114).
        configure(default_cores=32)  # Override for this session

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
        # "gpu" is not a ClusterConfig field (see above), so it is not
        # asserted on current_config here -- it was already verified on the
        # decorator's _cluster_config above.
        current_config = get_config()
        assert current_config.cluster_type == "slurm"
        assert current_config.cluster_host == "hpc.myuniversity.edu"
        assert current_config.default_cores == 32  # Overridden value
