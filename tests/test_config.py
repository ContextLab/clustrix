import pytest
import json
import yaml
from pathlib import Path
from clustrix.config import (
    ClusterConfig,
    configure,
    get_config,
    load_config,
    save_config,
    _load_default_config,
)


class TestClusterConfig:
    """Test ClusterConfig dataclass."""

    def test_default_initialization(self):
        """Test default configuration values."""
        config = ClusterConfig()
        assert config.cluster_type == "slurm"
        assert config.cluster_port == 22
        assert config.default_cores == 4
        assert config.default_memory == "8GB"
        assert config.default_time == "01:00:00"
        assert config.auto_parallel is True
        assert config.max_parallel_jobs == 100
        assert config.cleanup_on_success is True

    def test_custom_initialization(self):
        """Test custom configuration values."""
        config = ClusterConfig(
            cluster_type="pbs",
            cluster_host="custom.host.com",
            username="testuser",
            default_cores=8,
            default_memory="16GB",
        )
        assert config.cluster_type == "pbs"
        assert config.cluster_host == "custom.host.com"
        assert config.username == "testuser"
        assert config.default_cores == 8
        assert config.default_memory == "16GB"

    def test_post_init_defaults(self):
        """Test that post_init sets default mutable values."""
        config = ClusterConfig()
        assert config.environment_variables == {}
        assert config.module_loads == []
        assert config.pre_execution_commands == []


class TestConfigureFunctions:
    """Test configuration functions."""

    def test_configure(self):
        """Test configure function."""
        configure(
            cluster_type="ssh",
            cluster_host="test.example.com",
            username="myuser",
            default_cores=16,
        )

        config = get_config()
        assert config.cluster_type == "ssh"
        assert config.cluster_host == "test.example.com"
        assert config.username == "myuser"
        assert config.default_cores == 16

    def test_configure_invalid_parameter(self):
        """Test configure with invalid parameter."""
        with pytest.raises(ValueError, match="Unknown configuration parameter"):
            configure(invalid_param="value")

    def test_get_config(self):
        """Test get_config returns current configuration."""
        configure(cluster_type="kubernetes")
        config = get_config()
        assert isinstance(config, ClusterConfig)
        assert config.cluster_type == "kubernetes"


class TestConfigFileOperations:
    """Test configuration file operations."""

    def test_save_load_yaml(self, temp_dir):
        """Test saving and loading YAML configuration."""
        config_path = Path(temp_dir) / "test_config.yml"

        # Configure and save
        configure(
            cluster_type="slurm",
            cluster_host="yaml.test.com",
            username="yamluser",
            default_cores=32,
            environment_variables={"TEST_VAR": "value"},
            module_loads=["python/3.9", "cuda/11.2"],
        )
        save_config(str(config_path))

        # Reset and load
        configure(cluster_type="ssh")  # Change to verify load works
        load_config(str(config_path))

        config = get_config()
        assert config.cluster_type == "slurm"
        assert config.cluster_host == "yaml.test.com"
        assert config.username == "yamluser"
        assert config.default_cores == 32
        assert config.environment_variables == {"TEST_VAR": "value"}
        assert config.module_loads == ["python/3.9", "cuda/11.2"]

    def test_save_load_json(self, temp_dir):
        """Test saving and loading JSON configuration."""
        config_path = Path(temp_dir) / "test_config.json"

        # Configure and save
        configure(
            cluster_type="pbs",
            cluster_host="json.test.com",
            username="jsonuser",
            default_memory="64GB",
        )
        save_config(str(config_path))

        # Reset and load
        configure(cluster_type="ssh")  # Change to verify load works
        load_config(str(config_path))

        config = get_config()
        assert config.cluster_type == "pbs"
        assert config.cluster_host == "json.test.com"
        assert config.username == "jsonuser"
        assert config.default_memory == "64GB"

    def test_load_nonexistent_config(self):
        """Test loading non-existent configuration file."""
        with pytest.raises(FileNotFoundError):
            load_config("/path/to/nonexistent/config.yml")

    def test_load_default_config(self, temp_dir, monkeypatch):
        """Test loading configuration from default locations."""
        # Create a test config file
        config_path = Path(temp_dir) / "clustrix.yml"
        test_config = {
            "cluster_type": "sge",
            "cluster_host": "default.test.com",
            "username": "defaultuser",
        }

        with open(config_path, "w") as f:
            yaml.dump(test_config, f)

        # Patch cwd to return our temp directory
        monkeypatch.chdir(temp_dir)

        # Reset config and load defaults
        configure(cluster_type="ssh")  # Set to different value
        _load_default_config()

        config = get_config()
        assert config.cluster_type == "sge"
        assert config.cluster_host == "default.test.com"
        assert config.username == "defaultuser"


class TestConfigContent:
    """Test configuration content validation."""

    def test_all_cluster_types(self):
        """Test all supported cluster types."""
        cluster_types = ["slurm", "pbs", "sge", "kubernetes", "ssh"]

        for cluster_type in cluster_types:
            configure(cluster_type=cluster_type)
            config = get_config()
            assert config.cluster_type == cluster_type

    def test_resource_specifications(self):
        """Test resource specification formats."""
        configure(
            default_cores=64,
            default_memory="128GB",
            default_time="24:00:00",
            default_partition="gpu",
            default_queue="batch",
        )

        config = get_config()
        assert config.default_cores == 64
        assert config.default_memory == "128GB"
        assert config.default_time == "24:00:00"
        assert config.default_partition == "gpu"
        assert config.default_queue == "batch"

    def test_path_configurations(self):
        """Test path-related configurations."""
        configure(
            remote_work_dir="/scratch/user/clustrix",
            local_cache_dir="/tmp/clustrix_cache",
            key_file="/home/user/.ssh/cluster_key",
        )

        config = get_config()
        assert config.remote_work_dir == "/scratch/user/clustrix"
        assert config.local_cache_dir == "/tmp/clustrix_cache"
        assert config.key_file == "/home/user/.ssh/cluster_key"
