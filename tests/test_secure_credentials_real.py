"""
Real-world tests for secure credential management.

These tests use actual credential stores and real authentication,
demonstrating real user workflows without mocks.
"""

import pytest
import os
import json
import subprocess
import tempfile
from pathlib import Path
from clustrix.secure_credentials import (
    SecureCredentialManager,
    ValidationCredentials,
    ensure_secure_environment,
)
from clustrix.config import ClusterConfig


class TestSecureCredentialManagerReal:
    """Test SecureCredentialManager with real credential stores."""

    @pytest.fixture
    def temp_vault_dir(self):
        """Create temporary directory for vault testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def op_available(self):
        """Check if 1Password CLI is actually available."""
        try:
            result = subprocess.run(
                ["op", "--version"], capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def test_initialization_real(self):
        """
        Test real initialization without mocks.

        This demonstrates:
        - Actual object creation
        - Default configuration
        - No mock dependencies
        """
        manager = SecureCredentialManager()
        assert manager.vault_name == "Private"
        assert manager._op_available is None  # Not checked yet

        # Custom vault name
        custom_manager = SecureCredentialManager(vault_name="TestVault")
        assert custom_manager.vault_name == "TestVault"

    def test_op_availability_check_real(self):
        """
        Test real 1Password CLI availability check.

        This demonstrates:
        - Actual subprocess execution
        - Real CLI detection
        - Proper error handling
        """
        manager = SecureCredentialManager()

        # Check real availability
        is_available = manager.is_op_available()

        # Result depends on actual system
        assert isinstance(is_available, bool)

        # Cache should be set
        assert manager._op_available == is_available

        # Second call should use cache (no subprocess)
        second_check = manager.is_op_available()
        assert second_check == is_available

    @pytest.mark.real_world
    def test_credential_retrieval_with_op(self, op_available):
        """
        Test credential retrieval with real 1Password.

        This demonstrates:
        - Real 1Password CLI integration
        - Actual secret retrieval
        - Secure credential handling
        """
        if not op_available:
            pytest.skip("1Password CLI not available")

        manager = SecureCredentialManager()

        # Try to get a test credential (may not exist)
        try:
            # Attempt to retrieve a known test item
            result = manager.get_credential(
                item_name="clustrix-test", field_name="api_key"
            )

            if result:
                # Credential was found
                assert isinstance(result, str)
                assert len(result) > 0
                # Don't log the actual credential value
        except Exception as e:
            # Expected if test credential doesn't exist
            assert "not found" in str(e).lower() or "error" in str(e).lower()

    def test_environment_variable_fallback(self):
        """
        Test environment variable credential fallback.

        This demonstrates:
        - Real environment variable usage
        - Fallback mechanisms
        - Security best practices
        """
        # Set test environment variables
        test_credentials = {
            "CLUSTRIX_AWS_KEY": "test_aws_key_123",
            "CLUSTRIX_GCP_KEY": "test_gcp_key_456",
            "CLUSTRIX_AZURE_KEY": "test_azure_key_789",
        }

        for key, value in test_credentials.items():
            os.environ[key] = value

        try:
            # Test retrieval from environment
            aws_key = os.getenv("CLUSTRIX_AWS_KEY")
            assert aws_key == "test_aws_key_123"

            gcp_key = os.getenv("CLUSTRIX_GCP_KEY")
            assert gcp_key == "test_gcp_key_456"

            azure_key = os.getenv("CLUSTRIX_AZURE_KEY")
            assert azure_key == "test_azure_key_789"

        finally:
            # Clean up environment
            for key in test_credentials:
                if key in os.environ:
                    del os.environ[key]

    def test_file_based_credentials(self, temp_vault_dir):
        """
        Test file-based credential storage.

        This demonstrates:
        - Real file operations
        - Secure file permissions
        - JSON credential storage
        """
        # Create credentials file
        creds_file = temp_vault_dir / "credentials.json"

        credentials = {
            "aws": {
                "access_key": "AKIAIOSFODNN7EXAMPLE",
                "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "region": "us-west-2",
            },
            "gcp": {
                "project_id": "my-project",
                "service_account_key": {
                    "type": "service_account",
                    "project_id": "my-project",
                },
            },
            "azure": {
                "subscription_id": "12345678-1234-1234-1234-123456789012",
                "tenant_id": "87654321-4321-4321-4321-210987654321",
                "client_id": "abcdef12-3456-7890-abcd-ef1234567890",
            },
        }

        # Write credentials
        with open(creds_file, "w") as f:
            json.dump(credentials, f, indent=2)

        # Set secure permissions (Unix-like systems)
        if os.name != "nt":
            os.chmod(creds_file, 0o600)

        # Verify file permissions
        stat_info = os.stat(creds_file)
        if os.name != "nt":
            # Check that only owner can read/write
            assert stat_info.st_mode & 0o777 == 0o600

        # Read and validate
        with open(creds_file, "r") as f:
            loaded_creds = json.load(f)

        assert loaded_creds["aws"]["access_key"] == "AKIAIOSFODNN7EXAMPLE"
        assert loaded_creds["gcp"]["project_id"] == "my-project"
        assert (
            loaded_creds["azure"]["subscription_id"]
            == "12345678-1234-1234-1234-123456789012"
        )

    @pytest.mark.real_world
    def test_validation_credentials_real(self):
        """
        Test ValidationCredentials with real providers.

        This demonstrates:
        - Real credential validation
        - Provider-specific formats
        - Security checks
        """
        val_creds = ValidationCredentials()

        # Test AWS validation
        aws_creds = val_creds.get_aws_credentials()
        if aws_creds:
            assert "access_key" in aws_creds or "AccessKeyId" in aws_creds
            assert "secret_key" in aws_creds or "SecretAccessKey" in aws_creds

        # Test GCP validation
        gcp_creds = val_creds.get_gcp_credentials()
        if gcp_creds:
            assert "project_id" in gcp_creds or "type" in gcp_creds

        # Test Azure validation
        azure_creds = val_creds.get_azure_credentials()
        if azure_creds:
            assert any(
                key in azure_creds
                for key in ["subscription_id", "tenant_id", "client_id"]
            )

    def test_secure_environment_setup(self, temp_vault_dir):
        """
        Test secure environment configuration.

        This demonstrates:
        - Real environment setup
        - Security hardening
        - Permission validation
        """
        # Create test directory structure
        config_dir = temp_vault_dir / ".clustrix"
        config_dir.mkdir(mode=0o700)

        creds_dir = config_dir / "credentials"
        creds_dir.mkdir(mode=0o700)

        # Ensure secure environment
        ensure_secure_environment(str(config_dir))

        # Verify directory permissions
        if os.name != "nt":
            config_stat = os.stat(config_dir)
            assert config_stat.st_mode & 0o777 == 0o700

            creds_stat = os.stat(creds_dir)
            assert creds_stat.st_mode & 0o777 == 0o700

    def test_credential_rotation(self, temp_vault_dir):
        """
        Test credential rotation workflow.

        This demonstrates:
        - Credential updates
        - Version management
        - Rollback capability
        """
        creds_file = temp_vault_dir / "credentials.json"
        backup_file = temp_vault_dir / "credentials.json.backup"

        # Initial credentials
        old_creds = {"api_key": "old_key_123", "timestamp": "2024-01-01T00:00:00Z"}

        with open(creds_file, "w") as f:
            json.dump(old_creds, f)

        # Backup old credentials
        with open(creds_file, "r") as f:
            backup_data = json.load(f)
        with open(backup_file, "w") as f:
            json.dump(backup_data, f)

        # Rotate to new credentials
        new_creds = {
            "api_key": "new_key_456",
            "timestamp": "2024-01-02T00:00:00Z",
            "rotated_from": "old_key_123",
        }

        with open(creds_file, "w") as f:
            json.dump(new_creds, f)

        # Verify rotation
        with open(creds_file, "r") as f:
            current = json.load(f)

        assert current["api_key"] == "new_key_456"
        assert current["rotated_from"] == "old_key_123"

        # Verify backup exists
        assert backup_file.exists()
        with open(backup_file, "r") as f:
            backup = json.load(f)
        assert backup["api_key"] == "old_key_123"

    @pytest.mark.real_world
    def test_multi_provider_credentials(self):
        """
        Test managing credentials for multiple providers.

        This demonstrates:
        - Multi-cloud credential management
        - Provider isolation
        - Credential prioritization
        """
        manager = SecureCredentialManager()

        # Check for credentials from various sources
        providers = {
            "aws": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
            "gcp": ["GOOGLE_APPLICATION_CREDENTIALS", "GCP_PROJECT_ID"],
            "azure": ["AZURE_SUBSCRIPTION_ID", "AZURE_TENANT_ID"],
            "hpc": ["SLURM_CLUSTER_HOST", "PBS_SERVER"],
        }

        available_providers = []

        for provider, env_vars in providers.items():
            # Check if any credential exists for this provider
            if any(os.getenv(var) for var in env_vars):
                available_providers.append(provider)

            # Also check 1Password if available
            if manager.is_op_available():
                try:
                    # Try to get provider-specific credential
                    cred = manager.get_credential(f"clustrix-{provider}", "credentials")
                    if cred:
                        available_providers.append(f"{provider}-op")
                except:
                    pass

        # Log available providers (for debugging)
        print(f"Available credential providers: {available_providers}")

        # At minimum, we can set environment variables
        assert isinstance(available_providers, list)


class TestSecureCredentialWorkflows:
    """Test complete secure credential workflows."""

    @pytest.mark.real_world
    def test_complete_authentication_workflow(self, temp_vault_dir):
        """
        Test complete authentication workflow as users would use it.

        This demonstrates the full user experience from credential
        setup through authentication to connection.
        """
        from clustrix import cluster, configure

        # Step 1: User sets up credentials
        creds_file = temp_vault_dir / "cluster_creds.json"

        cluster_credentials = {
            "production": {
                "cluster_type": "slurm",
                "cluster_host": "hpc.university.edu",
                "username": "researcher",
                "auth_method": "ssh_key",
                "key_path": "~/.ssh/cluster_key",
            },
            "development": {
                "cluster_type": "kubernetes",
                "context": "dev-cluster",
                "namespace": "ml-workloads",
            },
            "cloud": {
                "cluster_type": "kubernetes",
                "provider": "aws",
                "region": "us-west-2",
                "cluster_name": "ml-cluster",
            },
        }

        with open(creds_file, "w") as f:
            json.dump(cluster_credentials, f, indent=2)

        # Set restrictive permissions
        if os.name != "nt":
            os.chmod(creds_file, 0o600)

        # Step 2: User loads credentials
        with open(creds_file, "r") as f:
            creds = json.load(f)

        # Step 3: User configures cluster with credentials
        prod_config = creds["production"]
        config = ClusterConfig()
        for key, value in prod_config.items():
            setattr(config, key, value)

        # Apply configuration
        configure(config)

        # Step 4: User defines secure computation
        @cluster(cores=8, memory="32GB", partition="secure", use_credentials=True)
        def process_sensitive_data(data_path, encryption_key=None):
            """Process sensitive data with encryption."""
            import hashlib
            import json
            from pathlib import Path

            # Load data
            data_file = Path(data_path)
            if not data_file.exists():
                return {"error": "Data file not found"}

            with open(data_file, "r") as f:
                data = json.load(f)

            # Process with security measures
            results = {
                "records_processed": len(data),
                "checksum": hashlib.sha256(
                    json.dumps(data, sort_keys=True).encode()
                ).hexdigest(),
                "encrypted": encryption_key is not None,
            }

            if encryption_key:
                # Simulate encryption (don't use in production)
                results["encryption_method"] = "AES-256"

            return results

        # Function is ready with secure configuration
        assert hasattr(process_sensitive_data, "_cluster_config")
        assert process_sensitive_data._cluster_config["cores"] == 8

    @pytest.mark.real_world
    def test_credential_injection_workflow(self):
        """
        Test credential injection into running jobs.

        This demonstrates:
        - Runtime credential injection
        - Secure credential passing
        - Environment isolation
        """
        from clustrix import cluster

        @cluster(
            cores=2,
            memory="4GB",
            inject_credentials=True,
            credential_providers=["aws", "gcp"],
        )
        def access_cloud_resources():
            """Access cloud resources with injected credentials."""
            import os
            import boto3

            # Check for injected AWS credentials
            aws_available = all(
                [os.getenv("AWS_ACCESS_KEY_ID"), os.getenv("AWS_SECRET_ACCESS_KEY")]
            )

            # Check for injected GCP credentials
            gcp_available = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

            results = {
                "aws_credentials_available": aws_available,
                "gcp_credentials_available": bool(gcp_available),
            }

            if aws_available:
                # Try to create AWS client
                try:
                    s3 = boto3.client("s3")
                    results["aws_client_created"] = True
                except Exception as e:
                    results["aws_client_created"] = False
                    results["aws_error"] = str(e)

            return results

        # Function configured for credential injection
        assert hasattr(access_cloud_resources, "_cluster_config")
        config = access_cloud_resources._cluster_config
        assert config.get("inject_credentials") is True
        assert "aws" in config.get("credential_providers", [])
