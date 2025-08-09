"""Unit tests for FlexibleCredentialManager."""

import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, mock_open

from clustrix.credential_manager import (
    FlexibleCredentialManager,
    DotEnvCredentialSource,
    EnvironmentCredentialSource,
    GitHubActionsCredentialSource,
    get_credential_manager,
)


class TestDotEnvCredentialSource:
    """Test DotEnvCredentialSource functionality."""

    def test_is_available_with_existing_file(self):
        """Test that source is available when .env file exists."""
        with tempfile.NamedTemporaryFile(suffix=".env", delete=False) as f:
            env_path = Path(f.name)

        try:
            source = DotEnvCredentialSource(env_path)
            assert source.is_available()
        finally:
            env_path.unlink()

    def test_is_available_with_nonexistent_file(self):
        """Test that source is not available when .env file doesn't exist."""
        env_path = Path("/nonexistent/path/.env")
        source = DotEnvCredentialSource(env_path)
        assert not source.is_available()

    @patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "test_key",
            "AWS_SECRET_ACCESS_KEY": "test_secret",
            "AWS_REGION": "us-west-2",
        },
    )
    def test_get_aws_credentials(self):
        """Test getting AWS credentials from environment after .env load."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("AWS_ACCESS_KEY_ID=test_key\n")
            f.write("AWS_SECRET_ACCESS_KEY=test_secret\n")
            f.write("AWS_REGION=us-west-2\n")
            env_path = Path(f.name)

        try:
            source = DotEnvCredentialSource(env_path)
            creds = source.get_credentials("aws")

            assert creds is not None
            assert creds["access_key_id"] == "test_key"
            assert creds["secret_access_key"] == "test_secret"
            assert creds["region"] == "us-west-2"
        finally:
            env_path.unlink()

    def test_get_credentials_unsupported_provider(self):
        """Test that unsupported providers return None."""
        with tempfile.NamedTemporaryFile(suffix=".env", delete=False) as f:
            env_path = Path(f.name)

        try:
            source = DotEnvCredentialSource(env_path)
            creds = source.get_credentials("unsupported_provider")
            assert creds is None
        finally:
            env_path.unlink()


class TestEnvironmentCredentialSource:
    """Test EnvironmentCredentialSource functionality."""

    def test_is_available(self):
        """Test that environment source is always available."""
        source = EnvironmentCredentialSource()
        assert source.is_available()

    @patch.dict(
        os.environ,
        {
            "SSH_HOST": "test.example.com",
            "SSH_USERNAME": "testuser",
            "SSH_PASSWORD": "testpass",
            "SSH_PORT": "22",
        },
    )
    def test_get_ssh_credentials(self):
        """Test getting SSH credentials from environment."""
        source = EnvironmentCredentialSource()
        creds = source.get_credentials("ssh")

        assert creds is not None
        assert creds["host"] == "test.example.com"
        assert creds["username"] == "testuser"
        assert creds["password"] == "testpass"
        assert creds["port"] == "22"

    def test_get_credentials_no_env_vars(self):
        """Test that minimal credentials return only defaults."""
        with patch.dict(os.environ, {}, clear=True):
            source = EnvironmentCredentialSource()

            # AWS always has a default region
            aws_creds = source.get_credentials("aws")
            assert aws_creds == {"region": "us-east-1"}

            # SSH has a default port
            ssh_creds = source.get_credentials("ssh")
            assert ssh_creds == {"port": "22"}

            # Test a provider with no defaults - it should be None since no env vars are set
            # and filtered_credentials will be empty for providers with only None values
            azure_creds = source.get_credentials("azure")
            assert azure_creds is None


class TestGitHubActionsCredentialSource:
    """Test GitHubActionsCredentialSource functionality."""

    @patch.dict(os.environ, {"GITHUB_ACTIONS": "true"})
    def test_is_available_in_github_actions(self):
        """Test that source is available in GitHub Actions."""
        source = GitHubActionsCredentialSource()
        assert source.is_available()

    def test_is_available_outside_github_actions(self):
        """Test that source is not available outside GitHub Actions."""
        with patch.dict(os.environ, {}, clear=True):
            source = GitHubActionsCredentialSource()
            assert not source.is_available()

    @patch.dict(
        os.environ,
        {
            "GITHUB_ACTIONS": "true",
            "AWS_ACCESS_KEY_ID": "gh_key",
            "AWS_ACCESS_KEY": "gh_secret",
        },
    )
    def test_get_aws_credentials(self):
        """Test getting AWS credentials in GitHub Actions."""
        source = GitHubActionsCredentialSource()
        creds = source.get_credentials("aws")

        assert creds is not None
        assert creds["access_key_id"] == "gh_key"
        assert creds["secret_access_key"] == "gh_secret"


class TestFlexibleCredentialManager:
    """Test FlexibleCredentialManager functionality."""

    def test_initialization(self):
        """Test that manager initializes correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            manager = FlexibleCredentialManager(config_dir)

            assert manager.config_dir == config_dir
            assert manager.env_file == config_dir / ".env"
            assert len(manager.sources) == 4  # All four credential sources

    def test_env_file_creation(self):
        """Test that .env file is created automatically."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            manager = FlexibleCredentialManager(config_dir)

            assert manager.env_file.exists()
            assert (
                manager.env_file.stat().st_mode & 0o777 == 0o600
            )  # Secure permissions

    def test_ensure_credential_success(self):
        """Test successful credential retrieval."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)

            # Create .env file with AWS credentials
            env_file = config_dir / ".env"
            env_file.parent.mkdir(exist_ok=True)
            env_file.write_text(
                "AWS_ACCESS_KEY_ID=test_key\nAWS_SECRET_ACCESS_KEY=test_secret\n"
            )

            with patch.dict(
                os.environ,
                {
                    "AWS_ACCESS_KEY_ID": "test_key",
                    "AWS_SECRET_ACCESS_KEY": "test_secret",
                },
            ):
                manager = FlexibleCredentialManager(config_dir)
                creds = manager.ensure_credential("aws")

                assert creds is not None
                assert "access_key_id" in creds
                assert "secret_access_key" in creds

    def test_ensure_credential_not_found(self):
        """Test credential retrieval when credentials don't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)

            with patch.dict(os.environ, {}, clear=True):
                manager = FlexibleCredentialManager(config_dir)
                creds = manager.ensure_credential("nonexistent")

                assert creds is None

    def test_get_credential_status(self):
        """Test getting comprehensive credential status."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            manager = FlexibleCredentialManager(config_dir)

            status = manager.get_credential_status()

            assert "config_directory" in status
            assert "env_file" in status
            assert "env_file_exists" in status
            assert "sources" in status
            assert "providers" in status

            # Should have all four sources
            assert len(status["sources"]) == 4

            # Should have all supported providers
            expected_providers = [
                "aws",
                "azure",
                "gcp",
                "ssh",
                "kubernetes",
                "huggingface",
                "lambda_cloud",
            ]
            for provider in expected_providers:
                assert provider in status["providers"]


class TestGlobalCredentialManager:
    """Test global credential manager singleton."""

    def test_get_credential_manager_singleton(self):
        """Test that get_credential_manager returns the same instance."""
        manager1 = get_credential_manager()
        manager2 = get_credential_manager()

        assert manager1 is manager2

    def test_get_credential_manager_default_location(self):
        """Test that default manager uses correct location."""
        manager = get_credential_manager()

        expected_dir = Path.home() / ".clustrix"
        assert manager.config_dir == expected_dir
