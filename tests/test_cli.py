import pytest
from click.testing import CliRunner
from unittest.mock import patch, Mock
from pathlib import Path
from clustrix.cli import cli
from clustrix.config import ClusterConfig


class TestCLI:
    """Test command-line interface."""

    @pytest.fixture
    def runner(self):
        """Create a CLI runner."""
        return CliRunner()

    def test_cli_help(self, runner):
        """Test CLI help command."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Clustrix CLI" in result.output
        assert "config" in result.output
        assert "load" in result.output
        assert "status" in result.output

    @patch("clustrix.cli.get_config")
    def test_config_show(self, mock_get_config, runner):
        """Test showing current configuration."""
        mock_config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="test.cluster.com",
            username="testuser",
            default_cores=8,
        )
        mock_get_config.return_value = mock_config

        result = runner.invoke(cli, ["config"])

        assert result.exit_code == 0
        assert "Current Clustrix Configuration" in result.output
        assert "cluster_type: slurm" in result.output
        assert "cluster_host: test.cluster.com" in result.output
        assert "username: testuser" in result.output
        assert "default_cores: 8" in result.output

    @patch("clustrix.cli.configure")
    @patch("clustrix.cli.get_config")
    def test_config_set_values(self, mock_get_config, mock_configure, runner):
        """Test setting configuration values."""
        mock_config = ClusterConfig()
        mock_get_config.return_value = mock_config

        result = runner.invoke(
            cli,
            [
                "config",
                "--cluster-type",
                "pbs",
                "--cluster-host",
                "new.cluster.com",
                "--username",
                "newuser",
                "--cores",
                "16",
                "--memory",
                "32GB",
                "--time",
                "04:00:00",
            ],
        )

        assert result.exit_code == 0
        assert "Configuration updated successfully!" in result.output

        # Verify configure was called with correct parameters
        mock_configure.assert_called_once_with(
            cluster_type="pbs",
            cluster_host="new.cluster.com",
            username="newuser",
            default_cores=16,
            default_memory="32GB",
            default_time="04:00:00",
        )

    @patch("clustrix.cli.configure")
    def test_config_invalid_cluster_type(self, mock_configure, runner):
        """Test setting invalid cluster type."""
        result = runner.invoke(cli, ["config", "--cluster-type", "invalid"])

        assert result.exit_code == 2
        assert "Invalid value for '--cluster-type'" in result.output

    @patch("clustrix.cli.load_config")
    def test_load_config_success(self, mock_load_config, runner):
        """Test loading configuration from file."""
        config_file = "test_config.yml"

        result = runner.invoke(cli, ["load", config_file])

        assert result.exit_code == 0
        assert f"Configuration loaded from {config_file}" in result.output
        mock_load_config.assert_called_once_with(config_file)

    @patch("clustrix.cli.load_config")
    def test_load_config_file_not_found(self, mock_load_config, runner):
        """Test loading non-existent configuration file."""
        mock_load_config.side_effect = FileNotFoundError("File not found")

        result = runner.invoke(cli, ["load", "nonexistent.yml"])

        assert result.exit_code == 1
        assert "Error: File not found" in result.output

    @patch("clustrix.cli.load_config")
    def test_load_config_invalid_format(self, mock_load_config, runner):
        """Test loading invalid configuration file."""
        mock_load_config.side_effect = Exception("Invalid YAML")

        result = runner.invoke(cli, ["load", "invalid.yml"])

        assert result.exit_code == 1
        assert "Error loading configuration: Invalid YAML" in result.output

    @patch("clustrix.cli.get_config")
    @patch("clustrix.cli.ClusterExecutor")
    def test_status_no_cluster_configured(
        self, mock_executor_class, mock_get_config, runner
    ):
        """Test status when no cluster is configured."""
        mock_config = ClusterConfig(cluster_host=None)
        mock_get_config.return_value = mock_config

        result = runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        assert "No cluster configured" in result.output
        mock_executor_class.assert_not_called()

    @patch("clustrix.cli.get_config")
    @patch("clustrix.cli.ClusterExecutor")
    def test_status_with_cluster(self, mock_executor_class, mock_get_config, runner):
        """Test status with cluster configured."""
        mock_config = ClusterConfig(
            cluster_type="slurm", cluster_host="test.cluster.com", username="testuser"
        )
        mock_get_config.return_value = mock_config

        # Setup mock executor
        mock_executor = Mock()
        mock_executor_class.return_value = mock_executor

        result = runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        assert "Cluster Status" in result.output
        assert "Type: slurm" in result.output
        assert "Host: test.cluster.com" in result.output
        assert "User: testuser" in result.output
        assert "Connection: ✓ Connected" in result.output

        mock_executor.connect.assert_called_once()
        mock_executor.disconnect.assert_called_once()

    @patch("clustrix.cli.get_config")
    @patch("clustrix.cli.ClusterExecutor")
    def test_status_connection_failed(
        self, mock_executor_class, mock_get_config, runner
    ):
        """Test status when connection fails."""
        mock_config = ClusterConfig(
            cluster_type="slurm", cluster_host="test.cluster.com"
        )
        mock_get_config.return_value = mock_config

        # Setup mock executor to fail connection
        mock_executor = Mock()
        mock_executor.connect.side_effect = Exception("Connection failed")
        mock_executor_class.return_value = mock_executor

        result = runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        assert "Connection: ✗ Failed" in result.output
        assert "Connection failed" in result.output

    def test_cli_no_command(self, runner):
        """Test CLI with no command shows help."""
        result = runner.invoke(cli, [])
        # Click behavior varies by version: may return 0 or 2 when no command is given
        assert result.exit_code in [0, 2]
        assert "Clustrix CLI" in result.output
