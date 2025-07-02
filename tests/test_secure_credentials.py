"""Comprehensive tests for secure credential management."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch, call
import pytest

from clustrix.secure_credentials import (
    SecureCredentialManager,
    ValidationCredentials,
    ensure_secure_environment,
)


class TestSecureCredentialManager:
    """Test SecureCredentialManager class."""

    def test_init(self):
        """Test initialization."""
        manager = SecureCredentialManager()
        assert manager.vault_name == "Private"
        assert manager._op_available is None

        manager = SecureCredentialManager(vault_name="TestVault")
        assert manager.vault_name == "TestVault"

    @patch("subprocess.run")
    def test_is_op_available_success(self, mock_run):
        """Test successful 1Password CLI availability check."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        manager = SecureCredentialManager()
        assert manager.is_op_available() is True
        assert manager._op_available is True

        # Should not call subprocess again if cached
        assert manager.is_op_available() is True
        mock_run.assert_called_once_with(
            ["op", "account", "list"], capture_output=True, text=True, timeout=5
        )

    @patch("subprocess.run")
    def test_is_op_available_failure(self, mock_run):
        """Test failed 1Password CLI availability check."""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        manager = SecureCredentialManager()
        assert manager.is_op_available() is False
        assert manager._op_available is False

    @patch("subprocess.run")
    def test_is_op_available_timeout(self, mock_run):
        """Test timeout during availability check."""
        mock_run.side_effect = subprocess.TimeoutExpired(["op", "account", "list"], 5)

        manager = SecureCredentialManager()
        assert manager.is_op_available() is False
        assert manager._op_available is False

    @patch("subprocess.run")
    def test_is_op_available_file_not_found(self, mock_run):
        """Test FileNotFoundError during availability check."""
        mock_run.side_effect = FileNotFoundError()

        manager = SecureCredentialManager()
        assert manager.is_op_available() is False
        assert manager._op_available is False

    @patch("subprocess.run")
    def test_get_credential_success(self, mock_run):
        """Test successful credential retrieval."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "secret_value\n"
        mock_run.return_value = mock_result

        manager = SecureCredentialManager()
        manager._op_available = True  # Mock availability

        result = manager.get_credential("test-item", "api_key")
        assert result == "secret_value"

        mock_run.assert_called_with(
            [
                "op",
                "item",
                "get",
                "test-item",
                "--field=api_key",
                "--vault=Private",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

    @patch("subprocess.run")
    def test_get_credential_not_available(self, mock_run):
        """Test credential retrieval when 1Password not available."""
        manager = SecureCredentialManager()
        manager._op_available = False

        result = manager.get_credential("test-item")
        assert result is None
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_get_credential_failure(self, mock_run):
        """Test failed credential retrieval."""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "Item not found"
        mock_run.return_value = mock_result

        manager = SecureCredentialManager()
        manager._op_available = True

        result = manager.get_credential("nonexistent-item")
        assert result is None

    @patch("subprocess.run")
    def test_get_credential_timeout(self, mock_run):
        """Test timeout during credential retrieval."""
        mock_run.side_effect = subprocess.TimeoutExpired(["op", "item", "get"], 10)

        manager = SecureCredentialManager()
        manager._op_available = True

        result = manager.get_credential("test-item")
        assert result is None

    @patch("subprocess.run")
    def test_get_credential_exception(self, mock_run):
        """Test exception during credential retrieval."""
        mock_run.side_effect = Exception("Unexpected error")

        manager = SecureCredentialManager()
        manager._op_available = True

        result = manager.get_credential("test-item")
        assert result is None

    @patch("subprocess.run")
    def test_get_structured_credential_success(self, mock_run):
        """Test successful structured credential retrieval."""
        mock_data = {
            "fields": [
                {"id": "username", "label": "Username", "value": "test_user"},
                {"id": "password", "label": "", "value": "test_pass"},
                {
                    "id": "notesPlain",
                    "label": "Notes",
                    "value": "api_key: secret123\nregion: us-east-1",
                },
            ]
        }
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_data)
        mock_run.return_value = mock_result

        manager = SecureCredentialManager()
        manager._op_available = True

        result = manager.get_structured_credential("test-item")

        expected = {
            "Username": "test_user",
            "password": "test_pass",
            "Notes": "api_key: secret123\nregion: us-east-1",
            "api_key": "secret123",
            "region": "us-east-1",
        }
        assert result == expected

    @patch("subprocess.run")
    def test_get_structured_credential_not_available(self, mock_run):
        """Test structured credential retrieval when not available."""
        manager = SecureCredentialManager()
        manager._op_available = False

        result = manager.get_structured_credential("test-item")
        assert result is None
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_get_structured_credential_failure(self, mock_run):
        """Test failed structured credential retrieval."""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "Item not found"
        mock_run.return_value = mock_result

        manager = SecureCredentialManager()
        manager._op_available = True

        result = manager.get_structured_credential("nonexistent-item")
        assert result is None

    @patch("subprocess.run")
    def test_get_structured_credential_timeout(self, mock_run):
        """Test timeout during structured credential retrieval."""
        mock_run.side_effect = subprocess.TimeoutExpired(["op", "item", "get"], 10)

        manager = SecureCredentialManager()
        manager._op_available = True

        result = manager.get_structured_credential("test-item")
        assert result is None

    @patch("subprocess.run")
    def test_get_structured_credential_json_error(self, mock_run):
        """Test JSON decode error during structured credential retrieval."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "invalid json"
        mock_run.return_value = mock_result

        manager = SecureCredentialManager()
        manager._op_available = True

        result = manager.get_structured_credential("test-item")
        assert result is None

    def test_parse_notes_content_yaml_style(self):
        """Test parsing YAML-style content from notes."""
        manager = SecureCredentialManager()

        notes_content = """- api_key: secret123
- region: us-east-1
- timeout: 30"""

        result = manager._parse_notes_content(notes_content)
        expected = {
            "api_key": "secret123",
            "region": "us-east-1",
            "timeout": "30",
        }
        assert result == expected

    def test_parse_notes_content_simple_format(self):
        """Test parsing simple key:value format from notes."""
        manager = SecureCredentialManager()

        notes_content = """api_key: secret123
region: us-east-1
# This is a comment
timeout: 30"""

        result = manager._parse_notes_content(notes_content)
        expected = {
            "api_key": "secret123",
            "region": "us-east-1",
            "timeout": "30",
        }
        assert result == expected

    def test_parse_notes_content_error_handling(self):
        """Test error handling in notes content parsing."""
        manager = SecureCredentialManager()

        # Invalid format that causes errors
        notes_content = "invalid format with no colons"

        result = manager._parse_notes_content(notes_content)
        assert result == {}

    @patch("subprocess.run")
    def test_store_credential_success(self, mock_run):
        """Test successful credential storage."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        manager = SecureCredentialManager()
        manager._op_available = True

        credential_data = {"username": "test_user", "password": "test_pass"}
        result = manager.store_credential("test-item", credential_data)

        assert result is True
        mock_run.assert_called_with(
            [
                "op",
                "item",
                "create",
                "--category=API_CREDENTIAL",
                "--vault=Private",
                "--title=test-item",
                "username=test_user",
                "password=test_pass",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

    @patch("subprocess.run")
    def test_store_credential_not_available(self, mock_run):
        """Test credential storage when 1Password not available."""
        manager = SecureCredentialManager()
        manager._op_available = False

        result = manager.store_credential("test-item", {"key": "value"})
        assert result is False
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_store_credential_failure(self, mock_run):
        """Test failed credential storage."""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "Failed to create item"
        mock_run.return_value = mock_result

        manager = SecureCredentialManager()
        manager._op_available = True

        result = manager.store_credential("test-item", {"key": "value"})
        assert result is False

    @patch("subprocess.run")
    def test_store_credential_timeout(self, mock_run):
        """Test timeout during credential storage."""
        mock_run.side_effect = subprocess.TimeoutExpired(["op", "item", "create"], 15)

        manager = SecureCredentialManager()
        manager._op_available = True

        result = manager.store_credential("test-item", {"key": "value"})
        assert result is False

    @patch("subprocess.run")
    def test_store_credential_exception(self, mock_run):
        """Test exception during credential storage."""
        mock_run.side_effect = Exception("Unexpected error")

        manager = SecureCredentialManager()
        manager._op_available = True

        result = manager.store_credential("test-item", {"key": "value"})
        assert result is False


class TestValidationCredentials:
    """Test ValidationCredentials class."""

    def test_init(self):
        """Test initialization."""
        creds = ValidationCredentials()
        assert isinstance(creds.cred_manager, SecureCredentialManager)

    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_aws_credentials_from_1password(self, mock_get_cred):
        """Test AWS credentials retrieval from 1Password."""
        mock_get_cred.return_value = {
            "aws_access_key_id": "AKIATEST",
            "aws_secret_access_key": "secret123",
            "region": "us-west-2",
        }

        creds = ValidationCredentials()
        result = creds.get_aws_credentials()

        expected = {
            "aws_access_key_id": "AKIATEST",
            "aws_secret_access_key": "secret123",
            "aws_region": "us-west-2",
        }
        assert result == expected
        mock_get_cred.assert_called_once_with("clustrix-aws-validation")

    @patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "AKIATEST",
            "AWS_SECRET_ACCESS_KEY": "secret123",
            "AWS_DEFAULT_REGION": "us-west-2",
        },
    )
    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_aws_credentials_from_env(self, mock_get_cred):
        """Test AWS credentials retrieval from environment."""
        mock_get_cred.return_value = None

        creds = ValidationCredentials()
        result = creds.get_aws_credentials()

        expected = {
            "aws_access_key_id": "AKIATEST",
            "aws_secret_access_key": "secret123",
            "aws_region": "us-west-2",
        }
        assert result == expected

    @patch.dict(os.environ, {}, clear=True)
    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_aws_credentials_none(self, mock_get_cred):
        """Test AWS credentials when none available."""
        mock_get_cred.return_value = None

        creds = ValidationCredentials()
        result = creds.get_aws_credentials()

        assert result is None

    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_gcp_credentials_from_1password(self, mock_get_cred):
        """Test GCP credentials retrieval from 1Password."""
        mock_get_cred.return_value = {
            "project_id": "test-project",
            "service_account_json": "/path/to/service.json",
            "region": "us-central1",
        }

        creds = ValidationCredentials()
        result = creds.get_gcp_credentials()

        expected = {
            "project_id": "test-project",
            "service_account_json": "/path/to/service.json",
            "region": "us-central1",
        }
        assert result == expected

    @patch.dict(
        os.environ,
        {
            "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/service.json",
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_REGION": "us-west1",
        },
    )
    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_gcp_credentials_from_env(self, mock_get_cred):
        """Test GCP credentials retrieval from environment."""
        mock_get_cred.return_value = None

        creds = ValidationCredentials()
        result = creds.get_gcp_credentials()

        expected = {
            "project_id": "test-project",
            "service_account_json": "/path/to/service.json",
            "region": "us-west1",
        }
        assert result == expected

    @patch.dict(os.environ, {}, clear=True)
    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_gcp_credentials_none(self, mock_get_cred):
        """Test GCP credentials when none available."""
        mock_get_cred.return_value = None

        creds = ValidationCredentials()
        result = creds.get_gcp_credentials()

        assert result is None

    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_lambda_cloud_credentials_from_1password(self, mock_get_cred):
        """Test Lambda Cloud credentials retrieval from 1Password."""
        mock_get_cred.return_value = {
            "api_key": "lambda_key_123",
            "endpoint": "https://custom.endpoint.com/api/v1",
        }

        creds = ValidationCredentials()
        result = creds.get_lambda_cloud_credentials()

        expected = {
            "api_key": "lambda_key_123",
            "endpoint": "https://custom.endpoint.com/api/v1",
        }
        assert result == expected

    @patch.dict(os.environ, {"LAMBDA_CLOUD_API_KEY": "lambda_key_123"})
    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_lambda_cloud_credentials_from_env(self, mock_get_cred):
        """Test Lambda Cloud credentials retrieval from environment."""
        mock_get_cred.return_value = None

        creds = ValidationCredentials()
        result = creds.get_lambda_cloud_credentials()

        expected = {
            "api_key": "lambda_key_123",
            "endpoint": "https://cloud.lambdalabs.com/api/v1",
        }
        assert result == expected

    @patch.dict(os.environ, {}, clear=True)
    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_lambda_cloud_credentials_none(self, mock_get_cred):
        """Test Lambda Cloud credentials when none available."""
        mock_get_cred.return_value = None

        creds = ValidationCredentials()
        result = creds.get_lambda_cloud_credentials()

        assert result is None

    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_huggingface_credentials_from_1password(self, mock_get_cred):
        """Test HuggingFace credentials retrieval from 1Password."""
        mock_get_cred.return_value = {
            "token": "hf_token_123",
            "username": "test_user",
        }

        creds = ValidationCredentials()
        result = creds.get_huggingface_credentials()

        expected = {
            "token": "hf_token_123",
            "username": "test_user",
        }
        assert result == expected

    @patch.dict(
        os.environ,
        {
            "HUGGINGFACE_TOKEN": "hf_token_123",
            "HUGGINGFACE_USERNAME": "test_user",
        },
    )
    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_huggingface_credentials_from_env_huggingface_token(
        self, mock_get_cred
    ):
        """Test HuggingFace credentials retrieval from HUGGINGFACE_TOKEN."""
        mock_get_cred.return_value = None

        creds = ValidationCredentials()
        result = creds.get_huggingface_credentials()

        expected = {
            "token": "hf_token_123",
            "username": "test_user",
        }
        assert result == expected

    @patch.dict(os.environ, {"HF_TOKEN": "hf_token_456"})
    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_huggingface_credentials_from_env_hf_token(self, mock_get_cred):
        """Test HuggingFace credentials retrieval from HF_TOKEN."""
        mock_get_cred.return_value = None

        creds = ValidationCredentials()
        result = creds.get_huggingface_credentials()

        expected = {
            "token": "hf_token_456",
            "username": "",
        }
        assert result == expected

    @patch.dict(os.environ, {}, clear=True)
    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_huggingface_credentials_none(self, mock_get_cred):
        """Test HuggingFace credentials when none available."""
        mock_get_cred.return_value = None

        creds = ValidationCredentials()
        result = creds.get_huggingface_credentials()

        assert result is None

    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_docker_credentials_from_1password(self, mock_get_cred):
        """Test Docker credentials retrieval from 1Password."""
        mock_get_cred.return_value = {
            "username": "docker_user",
            "password": "docker_pass",
            "registry": "custom.registry.com",
        }

        creds = ValidationCredentials()
        result = creds.get_docker_credentials()

        expected = {
            "username": "docker_user",
            "password": "docker_pass",
            "registry": "custom.registry.com",
        }
        assert result == expected

    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_docker_credentials_none(self, mock_get_cred):
        """Test Docker credentials when none available."""
        mock_get_cred.return_value = None

        creds = ValidationCredentials()
        result = creds.get_docker_credentials()

        assert result is None

    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_ssh_credentials_from_1password(self, mock_get_cred):
        """Test SSH credentials retrieval from 1Password."""
        mock_get_cred.return_value = {
            "hostname": "test.cluster.edu",
            "username": "testuser",
            "private_key": "-----BEGIN PRIVATE KEY-----\n...",
            "port": "2222",
        }

        creds = ValidationCredentials()
        result = creds.get_ssh_credentials()

        expected = {
            "hostname": "test.cluster.edu",
            "username": "testuser",
            "private_key": "-----BEGIN PRIVATE KEY-----\n...",
            "port": "2222",
        }
        assert result == expected

    @patch.object(SecureCredentialManager, "get_structured_credential")
    def test_get_ssh_credentials_none(self, mock_get_cred):
        """Test SSH credentials when none available."""
        mock_get_cred.return_value = None

        creds = ValidationCredentials()
        result = creds.get_ssh_credentials()

        assert result is None


class TestEnsureSecureEnvironment:
    """Test ensure_secure_environment function."""

    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("pathlib.Path.open")
    @patch("pathlib.Path.chmod")
    def test_ensure_secure_environment_new_gitignore(
        self, mock_chmod, mock_open, mock_read_text, mock_exists, mock_mkdir
    ):
        """Test ensure_secure_environment with new .gitignore."""
        mock_exists.return_value = True
        mock_read_text.return_value = "existing content"
        mock_file = Mock()
        mock_open.return_value.__enter__.return_value = mock_file

        result = ensure_secure_environment()

        # Check that directories were created
        assert mock_mkdir.call_count == 2  # ~/.clustrix and ~/.clustrix/credentials

        # Check that .gitignore was updated
        mock_file.write.assert_called_once()
        written_content = mock_file.write.call_args[0][0]
        assert "# Clustrix security" in written_content
        assert "**/.clustrix/credentials/**" in written_content

        # Check that permissions were set
        mock_chmod.assert_called_once_with(0o700)

        # Check return value (cross-platform path check)
        assert result.name == "credentials"
        assert result.parent.name == ".clustrix"

    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("pathlib.Path.chmod")
    def test_ensure_secure_environment_existing_gitignore_with_security(
        self, mock_chmod, mock_read_text, mock_exists, mock_mkdir
    ):
        """Test ensure_secure_environment with existing security in .gitignore."""
        mock_exists.return_value = True
        mock_read_text.return_value = "existing content\n# Clustrix security\n"

        result = ensure_secure_environment()

        # Should not try to write to .gitignore since security section exists
        assert result.name == "credentials"
        assert result.parent.name == ".clustrix"

    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.chmod")
    def test_ensure_secure_environment_no_gitignore(
        self, mock_chmod, mock_exists, mock_mkdir
    ):
        """Test ensure_secure_environment with no .gitignore file."""
        mock_exists.return_value = False

        result = ensure_secure_environment()

        # Should still create directories and set permissions
        assert mock_mkdir.call_count == 2
        mock_chmod.assert_called_once_with(0o700)
        assert result.name == "credentials"
        assert result.parent.name == ".clustrix"

    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.chmod")
    def test_ensure_secure_environment_chmod_exception(
        self, mock_chmod, mock_exists, mock_mkdir
    ):
        """Test ensure_secure_environment when chmod fails."""
        mock_exists.return_value = False
        mock_chmod.side_effect = Exception("Permission denied")

        # Should not raise exception even if chmod fails
        result = ensure_secure_environment()
        assert result.name == "credentials"
        assert result.parent.name == ".clustrix"
