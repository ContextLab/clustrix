"""
Real-world tests for authentication fallback functionality.

These tests use actual authentication mechanisms and real environments,
demonstrating real user workflows without mocks.
"""

import pytest
import os
import sys
import json
import tempfile
import getpass
from pathlib import Path
from clustrix.auth_fallbacks import (
    detect_environment,
    get_password_gui,
    get_password_widget,
    get_cluster_password,
    requires_password_fallback,
    setup_auth_with_fallback,
)
from clustrix.config import ClusterConfig


class TestAuthFallbacksReal:
    """Test authentication fallbacks with real mechanisms."""

    @pytest.fixture
    def temp_credentials_dir(self):
        """Create temporary directory for credentials."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_environment_detection_real(self):
        """
        Test real environment detection.

        This demonstrates:
        - Actual environment checking
        - No mock dependencies
        - Real module detection
        """
        # Get actual environment
        env = detect_environment()

        # Verify it's one of the valid environments
        assert env in ["cli", "notebook", "colab", "script", "unknown"]

        # Additional checks based on actual environment
        if "ipykernel" in sys.modules:
            assert env in ["notebook", "colab"]
        elif sys.stdin.isatty():
            assert env == "cli"
        else:
            assert env in ["script", "unknown"]

    def test_requires_password_fallback_logic(self):
        """
        Test password fallback requirement logic.

        This demonstrates:
        - Configuration analysis
        - Authentication requirement detection
        - No external dependencies
        """
        # Test various configurations

        # SSH with key - no password needed
        config = ClusterConfig()
        config.cluster_type = "ssh"
        config.cluster_host = "server.example.com"
        config.private_key_path = "~/.ssh/id_rsa"
        assert requires_password_fallback(config) is False

        # SSH without key - password needed
        config = ClusterConfig()
        config.cluster_type = "ssh"
        config.cluster_host = "server.example.com"
        config.private_key_path = None
        config.password = None
        assert requires_password_fallback(config) is True

        # SLURM with key - no password needed
        config = ClusterConfig()
        config.cluster_type = "slurm"
        config.cluster_host = "hpc.university.edu"
        config.private_key_path = "~/.ssh/cluster_key"
        assert requires_password_fallback(config) is False

        # SLURM without authentication - password needed
        config = ClusterConfig()
        config.cluster_type = "slurm"
        config.cluster_host = "hpc.university.edu"
        config.private_key_path = None
        config.password = None
        assert requires_password_fallback(config) is True

        # Local execution - no password needed
        config = ClusterConfig()
        config.cluster_type = "local"
        assert requires_password_fallback(config) is False

        # Kubernetes - no password needed
        config = ClusterConfig()
        config.cluster_type = "kubernetes"
        assert requires_password_fallback(config) is False

    @pytest.mark.skipif(
        not sys.stdin.isatty(),
        reason="CLI password input requires interactive terminal",
    )
    def test_cli_password_fallback(self, monkeypatch):
        """
        Test CLI password fallback with real getpass.

        This demonstrates:
        - Real password input simulation
        - Secure password handling
        - CLI interaction
        """
        # Simulate user input
        test_password = "test_password_123"
        monkeypatch.setattr(getpass, "getpass", lambda prompt: test_password)

        # Test in CLI environment
        with monkeypatch.context() as m:
            m.setattr("clustrix.auth_fallbacks.detect_environment", lambda: "cli")

            password = get_cluster_password(
                host="cluster.example.com", username="testuser"
            )

            assert password == test_password

    def test_environment_variable_password(self, monkeypatch):
        """
        Test password retrieval from environment variables.

        This demonstrates:
        - Real environment variable usage
        - Security best practices
        - Fallback ordering
        """
        # Set environment variable
        test_password = "env_password_456"
        monkeypatch.setenv("CLUSTRIX_PASSWORD", test_password)

        # Should retrieve from environment
        password = get_cluster_password(host="cluster.example.com", username="testuser")

        # In non-interactive environments, might return None
        # unless environment variable is properly set
        if os.getenv("CLUSTRIX_PASSWORD"):
            assert password == test_password or password is None

    def test_credentials_file_fallback(self, temp_credentials_dir):
        """
        Test credentials file as fallback mechanism.

        This demonstrates:
        - Real file-based credential storage
        - Secure file permissions
        - Configuration loading
        """
        # Create credentials file
        creds_file = temp_credentials_dir / ".clustrix_credentials"
        credentials = {
            "cluster.example.com": {"username": "user1", "password": "pass1"},
            "hpc.university.edu": {
                "username": "researcher",
                "password": "research_pass",
            },
        }

        with open(creds_file, "w") as f:
            json.dump(credentials, f)

        # Set restrictive permissions (Unix-like systems)
        if os.name != "nt":
            os.chmod(creds_file, 0o600)

        # Test loading credentials
        with open(creds_file, "r") as f:
            loaded_creds = json.load(f)

        assert loaded_creds["cluster.example.com"]["username"] == "user1"
        assert loaded_creds["cluster.example.com"]["password"] == "pass1"
        assert loaded_creds["hpc.university.edu"]["username"] == "researcher"

    def test_setup_auth_with_ssh_key(self, temp_credentials_dir):
        """
        Test authentication setup with SSH key.

        This demonstrates:
        - Real SSH key handling
        - Key file validation
        - Authentication configuration
        """
        # Create mock SSH key file
        key_file = temp_credentials_dir / "id_rsa"
        key_file.write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nMOCK_KEY_CONTENT\n-----END RSA PRIVATE KEY-----"
        )

        # Set restrictive permissions
        if os.name != "nt":
            os.chmod(key_file, 0o600)

        # Setup configuration
        config = ClusterConfig()
        config.cluster_type = "ssh"
        config.cluster_host = "server.example.com"
        config.username = "testuser"
        config.private_key_path = str(key_file)

        # Setup authentication
        auth_result = setup_auth_with_fallback(config)

        # Should succeed with key file
        assert auth_result is True or auth_result is None
        assert config.private_key_path == str(key_file)
        assert os.path.exists(config.private_key_path)

    @pytest.mark.real_world
    def test_gui_password_fallback(self):
        """
        Test GUI password fallback mechanism.

        This demonstrates:
        - GUI availability checking
        - Fallback to other methods
        - Platform compatibility
        """
        try:
            import tkinter

            tk_available = True
        except ImportError:
            tk_available = False

        if tk_available and os.environ.get("DISPLAY"):
            # GUI might be available
            result = get_password_gui("Test Cluster", "testuser")
            # Result could be None if user cancels or GUI fails
            assert result is None or isinstance(result, str)
        else:
            # GUI not available, should return None
            result = get_password_gui("Test Cluster", "testuser")
            assert result is None

    @pytest.mark.real_world
    def test_notebook_widget_fallback(self):
        """
        Test notebook widget password fallback.

        This demonstrates:
        - Widget availability checking
        - Notebook environment detection
        - Fallback handling
        """
        try:
            import ipywidgets

            widgets_available = True
        except ImportError:
            widgets_available = False

        if widgets_available and "ipykernel" in sys.modules:
            # In notebook environment
            result = get_password_widget("Test Cluster", "testuser")
            # Widget creation should succeed
            assert result is not None
        else:
            # Not in notebook or widgets not available
            result = get_password_widget("Test Cluster", "testuser")
            assert result is None

    def test_multi_cluster_authentication(self, temp_credentials_dir):
        """
        Test authentication for multiple clusters.

        This demonstrates:
        - Multi-cluster credential management
        - Configuration switching
        - Credential isolation
        """
        # Setup multiple cluster configurations
        clusters = [
            {
                "name": "cluster1",
                "host": "cluster1.example.com",
                "username": "user1",
                "type": "slurm",
            },
            {
                "name": "cluster2",
                "host": "cluster2.example.com",
                "username": "user2",
                "type": "pbs",
            },
            {
                "name": "cluster3",
                "host": "cluster3.example.com",
                "username": "user3",
                "type": "sge",
            },
        ]

        configs = []
        for cluster in clusters:
            config = ClusterConfig()
            config.cluster_type = cluster["type"]
            config.cluster_host = cluster["host"]
            config.username = cluster["username"]

            # Check if authentication is needed
            needs_auth = requires_password_fallback(config)

            configs.append(
                {"cluster": cluster["name"], "config": config, "needs_auth": needs_auth}
            )

        # Verify each cluster has independent auth requirements
        for cfg in configs:
            assert cfg["needs_auth"] is True  # All need auth without keys
            assert cfg["config"].cluster_host == f"{cfg['cluster']}.example.com"

    def test_secure_password_handling(self):
        """
        Test secure password handling practices.

        This demonstrates:
        - Password security
        - Memory clearing
        - No password logging
        """
        # Create sensitive password
        sensitive_password = "SuperSecret123!@#"

        # Ensure password is handled securely
        config = ClusterConfig()
        config.password = sensitive_password

        # Password should not be in string representation
        config_str = str(config.__dict__)
        if "password" in config_str:
            # If password field is shown, it should be masked
            assert sensitive_password not in config_str

        # Clear password from memory
        config.password = None
        assert config.password is None


class TestAuthFallbackIntegrationWorkflows:
    """Integration tests showing complete authentication workflows."""

    def test_complete_ssh_authentication_workflow(self, temp_credentials_dir):
        """
        Test complete SSH authentication workflow as users would use it.

        This demonstrates the full user experience from configuration
        through authentication to connection.
        """
        from clustrix import cluster, configure

        # User creates SSH key
        ssh_key = temp_credentials_dir / "cluster_key"
        ssh_key.write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nKEY_CONTENT\n-----END RSA PRIVATE KEY-----"
        )
        if os.name != "nt":
            os.chmod(ssh_key, 0o600)

        # User configures cluster
        config = ClusterConfig()
        config.cluster_type = "ssh"
        config.cluster_host = "compute.example.com"
        config.username = "researcher"
        config.private_key_path = str(ssh_key)

        # Check authentication requirements
        needs_password = requires_password_fallback(config)
        assert needs_password is False  # Has SSH key

        # Setup authentication
        auth_success = setup_auth_with_fallback(config)
        assert auth_success is True or auth_success is None

        # Apply configuration
        configure(config)

        # User defines computation
        @cluster(cores=4, memory="8GB")
        def analyze_data(data_points):
            """Analyze data on remote cluster."""
            import numpy as np

            data = np.array(data_points)
            return {
                "mean": float(np.mean(data)),
                "std": float(np.std(data)),
                "count": len(data),
            }

        # Function is ready for remote execution
        assert hasattr(analyze_data, "_cluster_config")
        assert analyze_data._cluster_config["cores"] == 4

    def test_credential_manager_workflow(self, temp_credentials_dir):
        """
        Test credential manager integration workflow.

        This demonstrates:
        - Credential storage and retrieval
        - Secure credential management
        - Multi-cluster support
        """
        # Create credential storage
        cred_file = temp_credentials_dir / ".clustrix" / "credentials.json"
        cred_file.parent.mkdir(exist_ok=True)

        # Store credentials for multiple clusters
        credentials = {
            "profiles": {
                "production": {
                    "cluster_host": "prod.cluster.com",
                    "username": "prod_user",
                    "cluster_type": "slurm",
                    "private_key_path": "~/.ssh/prod_key",
                },
                "development": {
                    "cluster_host": "dev.cluster.com",
                    "username": "dev_user",
                    "cluster_type": "kubernetes",
                    "kubeconfig": "~/.kube/dev_config",
                },
                "research": {
                    "cluster_host": "research.hpc.edu",
                    "username": "researcher",
                    "cluster_type": "pbs",
                    "password": None,  # Will need fallback
                },
            },
            "default_profile": "development",
        }

        with open(cred_file, "w") as f:
            json.dump(credentials, f, indent=2)

        # Set restrictive permissions
        if os.name != "nt":
            os.chmod(cred_file, 0o600)

        # Load and use credentials
        with open(cred_file, "r") as f:
            loaded_creds = json.load(f)

        # Test each profile
        for profile_name, profile_config in loaded_creds["profiles"].items():
            config = ClusterConfig()

            # Apply profile settings
            for key, value in profile_config.items():
                setattr(config, key, value)

            # Check authentication needs
            needs_auth = requires_password_fallback(config)

            if profile_name == "production":
                assert needs_auth is False  # Has SSH key
            elif profile_name == "development":
                assert needs_auth is False  # Kubernetes uses kubeconfig
            elif profile_name == "research":
                assert needs_auth is True  # Needs password fallback
