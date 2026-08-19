import pytest
import yaml
from pathlib import Path
from clustrix.config import (
    ClusterConfig,
    REMOVED_CLUSTER_TYPES,
    SUPPORTED_CLUSTER_TYPES,
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
            cluster_type="ssh",
            cluster_host="custom.host.com",
            username="testuser",
            default_cores=8,
            default_memory="16GB",
        )
        assert config.cluster_type == "ssh"
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
        configure(cluster_type="huggingface")
        config = get_config()
        assert isinstance(config, ClusterConfig)
        assert config.cluster_type == "huggingface"


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
        # environment_variables is treated as a secret-bearing field (users
        # commonly stuff API keys/tokens into it) and is omitted from saved
        # config files by default -- see test_config_file_permissions.py.
        # This test wants a full round trip, so opt in explicitly.
        save_config(str(config_path), include_secrets=True)

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
            cluster_type="ssh",
            cluster_host="json.test.com",
            username="jsonuser",
            default_memory="64GB",
        )
        save_config(str(config_path))

        # Reset and load
        configure(cluster_type="ssh")  # Change to verify load works
        load_config(str(config_path))

        config = get_config()
        assert config.cluster_type == "ssh"
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
            "cluster_type": "huggingface",
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
        assert config.cluster_type == "huggingface"
        assert config.cluster_host == "default.test.com"
        assert config.username == "defaultuser"


class TestConfigContent:
    """Test configuration content validation."""

    def test_all_cluster_types(self):
        """Test all supported cluster types."""
        # Read the tuple rather than keeping a second copy: the old hardcoded
        # list still named pbs/sge/kubernetes long after those backends were
        # removed, so it asserted nothing about what clustrix supports.
        assert SUPPORTED_CLUSTER_TYPES == ("local", "ssh", "slurm", "huggingface")

        for cluster_type in SUPPORTED_CLUSTER_TYPES:
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


class TestRemovedBackendsAreExplained:
    """A config file naming a deleted backend must say what happened to it.

    Real files on disk, real ``load_config`` calls -- nothing is stubbed. The
    point of every assertion here is the *content* of the message: before the
    removal work, ``cluster_type: pbs`` was accepted silently and a stale
    ``k8s_namespace`` key came back through difflib pointing at an unrelated
    field, so the user was sent after the wrong thing.
    """

    @pytest.mark.parametrize(
        "cluster_type,issue",
        [
            ("pbs", 140),
            ("sge", 141),
            ("kubernetes", 142),
            ("aws", 143),
            ("gcp", 144),
            ("azure", 145),
            ("lambda_cloud", 146),
        ],
    )
    def test_removed_cluster_type_names_backend_reason_and_issue(
        self, tmp_path, cluster_type, issue
    ):
        config_path = tmp_path / "clustrix.yml"
        config_path.write_text(
            yaml.safe_dump({"cluster_type": cluster_type, "username": "someone"})
        )

        with pytest.raises(ValueError) as excinfo:
            load_config(str(config_path))

        message = str(excinfo.value)
        assert cluster_type in message
        assert "no longer implemented" in message
        assert "never been verified against real hardware" in message
        assert f"#{issue}" in message
        for supported in SUPPORTED_CLUSTER_TYPES:
            assert supported in message
        # The file that caused it, so the user knows which one to edit.
        assert str(config_path) in message

    def test_the_removed_type_table_matches_the_issues_that_track_them(self):
        assert REMOVED_CLUSTER_TYPES["pbs"] == 140
        assert REMOVED_CLUSTER_TYPES["sge"] == 141
        assert REMOVED_CLUSTER_TYPES["kubernetes"] == 142
        assert REMOVED_CLUSTER_TYPES["aws"] == 143
        assert REMOVED_CLUSTER_TYPES["gcp"] == 144
        assert REMOVED_CLUSTER_TYPES["azure"] == 145
        assert REMOVED_CLUSTER_TYPES["lambda_cloud"] == 146
        # No removed name may still be advertised as supported.
        assert not set(REMOVED_CLUSTER_TYPES) & set(SUPPORTED_CLUSTER_TYPES)

    def test_a_supported_cluster_type_still_loads(self, tmp_path):
        config_path = tmp_path / "clustrix.yml"
        config_path.write_text(
            yaml.safe_dump({"cluster_type": "slurm", "cluster_host": "hpc.example"})
        )

        load_config(str(config_path))

        assert get_config().cluster_type == "slurm"
        assert get_config().cluster_host == "hpc.example"

    @pytest.mark.parametrize(
        "setting,value,what,issue",
        [
            ("k8s_namespace", "default", "Kubernetes", 142),
            ("k8s_image", "python:3.11", "Kubernetes", 142),
            ("auto_provision_k8s", True, "Kubernetes", 142),
            ("aws_region", "us-east-1", "the AWS backend", 143),
            ("eks_cluster_name", "prod", "the AWS backend", 143),
            ("gcp_project_id", "proj", "the GCP backend", 144),
            ("azure_subscription_id", "sub", "the Azure backend", 145),
            ("lambda_api_key", "k", "the Lambda Cloud backend", 146),
        ],
    )
    def test_removed_setting_is_explained_not_guessed_at(
        self, tmp_path, setting, value, what, issue
    ):
        config_path = tmp_path / "clustrix.yml"
        config_path.write_text(yaml.safe_dump({"cluster_type": "ssh", setting: value}))

        with pytest.raises(ValueError) as excinfo:
            load_config(str(config_path))

        message = str(excinfo.value)
        assert setting in message
        assert f"{setting} configured {what}" in message
        assert "has been removed" in message
        assert f"#{issue}" in message
        # The old difflib path would have offered some unrelated field.
        assert "did you mean" not in message

    @pytest.mark.parametrize(
        "setting,value,what",
        [
            ("cloud_provider", "aws", "the cloud VM backends"),
            ("cloud_region", "us-east-1", "the cloud VM backends"),
            ("cloud_auto_configure", True, "the cloud VM backends"),
            ("cost_monitoring", True, "cloud cost monitoring"),
        ],
    )
    def test_removed_setting_without_a_tracking_issue_still_explains_itself(
        self, tmp_path, setting, value, what
    ):
        config_path = tmp_path / "clustrix.yml"
        config_path.write_text(yaml.safe_dump({setting: value}))

        with pytest.raises(ValueError) as excinfo:
            load_config(str(config_path))

        message = str(excinfo.value)
        assert f"{setting} configured {what}, which has been removed" in message
        assert "did you mean" not in message

    def test_a_genuine_typo_still_gets_the_did_you_mean_hint(self, tmp_path):
        """The removed-setting path must not have swallowed the typo hint."""
        config_path = tmp_path / "clustrix.yml"
        config_path.write_text(yaml.safe_dump({"cluster_hostt": "hpc.example"}))

        with pytest.raises(ValueError) as excinfo:
            load_config(str(config_path))

        assert "did you mean cluster_host?" in str(excinfo.value)
