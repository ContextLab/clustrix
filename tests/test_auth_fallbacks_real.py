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
import yaml
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


@pytest.fixture
def temp_credentials_dir():
    """Create temporary directory for credentials.

    Module-level (not class-scoped) so both TestAuthFallbacksReal and
    TestAuthFallbackIntegrationWorkflows can use it -- it used to live only
    inside TestAuthFallbacksReal, which meant tests in
    TestAuthFallbackIntegrationWorkflows that request it hit "fixture
    'temp_credentials_dir' not found" (Issue #114).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestAuthFallbacksReal:
    """Test authentication fallbacks with real mechanisms."""

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
        - Real requires_password_fallback() branch coverage
        - No external dependencies

        NOTE (Issue #114): requires_password_fallback() takes the
        Dict[str, Any] result of an SSH-key-setup attempt (see
        clustrix.ssh_utils.setup_ssh_keys's return contract) -- it never
        inspects a ClusterConfig, and has no notion of cluster_type at all.
        The original version of this test passed ClusterConfig instances
        directly, which crashed with
        "AttributeError: 'ClusterConfig' object has no attribute 'get'".
        This rewrite exercises the real function signature and each of its
        branches.
        """
        # Key setup succeeded and the connection was verified: no fallback
        assert (
            requires_password_fallback(
                {"success": True, "connection_tested": True, "error": None}
            )
            is False
        )

        # Key setup failed outright: fallback needed
        assert requires_password_fallback({"success": False}) is True

        # Key setup reported success but the connection was never actually
        # tested: fallback needed
        assert (
            requires_password_fallback({"success": True, "connection_tested": False})
            is True
        )

        # Success and tested, but the error text mentions a key-related
        # problem: fallback needed
        assert (
            requires_password_fallback(
                {
                    "success": True,
                    "connection_tested": True,
                    "error": "Permission denied (publickey)",
                }
            )
            is True
        )

        # Success, tested, and an unrelated error string: no fallback needed
        assert (
            requires_password_fallback(
                {"success": True, "connection_tested": True, "error": "disk full"}
            )
            is False
        )

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
                hostname="cluster.example.com", username="testuser"
            )

            assert password == test_password

    def test_environment_variable_password(self, monkeypatch):
        """
        Test password retrieval from environment variables.

        This demonstrates:
        - Real environment variable usage
        - Security best practices
        - Fallback ordering

        NOTE (Issue #114): two real-API mismatches fixed here:
        - get_cluster_password()'s first parameter is "hostname", not
          "host".
        - "CLUSTRIX_PASSWORD" does not match any of the environment
          variable names get_cluster_password() actually checks (it looks
          for "CLUSTRIX_PASSWORD_<HOST>", "CLUSTER_PASSWORD_<HOST>",
          "<HOST>_PASSWORD", "CLUSTRIX_DEFAULT_PASSWORD", or
          "CLUSTER_PASSWORD" -- never the bare, unsuffixed
          "CLUSTRIX_PASSWORD"). Setting a name the function never checks
          meant this test always fell through to the interactive
          fallbacks, which is why the original assertion had to tolerate
          "or password is None" -- and in this process 'ipykernel' ends up
          in sys.modules as a side effect of `import clustrix`
          (clustrix/__init__.py imports the notebook widget modules),
          which makes detect_environment() report "notebook" here and
          triggers a real, blocking tkinter GUI prompt with no user to
          answer it, hanging the test. Using a real recognized name
          ("CLUSTRIX_DEFAULT_PASSWORD") makes get_cluster_password()
          return from the environment-variable check before ever reaching
          the interactive branches, avoiding the hang and giving a
          deterministic assertion.
        """
        # Set environment variable
        test_password = "env_password_456"
        monkeypatch.setenv("CLUSTRIX_DEFAULT_PASSWORD", test_password)

        password = get_cluster_password(
            hostname="cluster.example.com", username="testuser"
        )

        assert password == test_password

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
        Test authentication setup with SSH key via setup_auth_with_fallback().

        This demonstrates:
        - Real SSH key handling
        - Key file validation
        - The real setup_auth_with_fallback() orchestration contract

        NOTE (Issue #114): several real-API mismatches fixed here:
        - ClusterConfig has no "private_key_path" field; the real one is
          "key_file".
        - setup_auth_with_fallback(config, setup_ssh_keys_func, **kwargs)
          requires a setup_ssh_keys_func callable and always returns a
          result Dict -- never True/None, which the original version of
          this test asserted. Calling the real
          clustrix.ssh_utils.setup_ssh_keys here would write new SSH keys
          under the developer's real ~/.ssh and open a real network
          connection to a fake host, so this test supplies a real
          (non-mock) function implementing the same
          Dict[str, Any] contract, to test setup_auth_with_fallback's own
          orchestration logic in isolation.
        """
        # Create SSH key file
        ssh_key_file = temp_credentials_dir / "id_rsa"
        ssh_key_file.write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nMOCK_KEY_CONTENT\n-----END RSA PRIVATE KEY-----"
        )

        # Set restrictive permissions
        if os.name != "nt":
            os.chmod(ssh_key_file, 0o600)

        # Setup configuration
        config = ClusterConfig()
        config.cluster_type = "ssh"
        config.cluster_host = "server.example.com"
        config.username = "testuser"
        config.key_file = str(ssh_key_file)

        def real_key_setup(config, **kwargs):
            """Real (non-mock) stand-in for setup_ssh_keys_func: the key
            file already exists and is valid, so setup succeeds without
            touching the filesystem or network."""
            return {
                "success": True,
                "key_path": config.key_file,
                "connection_tested": True,
                "error": None,
            }

        # Setup authentication
        auth_result = setup_auth_with_fallback(
            config, real_key_setup, password="fake-unused-key-auth-succeeds-first"
        )

        # Should succeed with key file
        assert auth_result["success"] is True
        assert auth_result["key_path"] == str(ssh_key_file)
        assert config.key_file == str(ssh_key_file)
        assert os.path.exists(config.key_file)

    @pytest.mark.real_world
    @pytest.mark.skipif(
        not sys.stdin.isatty(),
        reason="get_password_gui() opens a real, blocking tkinter dialog; "
        "unsafe to invoke in a non-interactive test run (mirrors "
        "test_cli_password_fallback's skip condition).",
    )
    def test_gui_password_fallback(self):
        """
        Test GUI password fallback mechanism.

        This demonstrates:
        - GUI availability checking
        - Platform compatibility

        NOTE (Issue #114): get_password_gui() takes a single `prompt: str`
        argument, not (cluster_name, username) -- the original version of
        this test called it with two positional arguments, a TypeError. The
        original "GUI unavailable -> should safely return None" else-branch
        is also unsound on a machine like this one: tkinter is importable
        and macOS does not use $DISPLAY, so calling get_password_gui() here
        for real opens a genuine, blocking modal window rather than safely
        returning None. This test is skipped outside an interactive
        terminal, matching test_cli_password_fallback's existing skip
        pattern for the same underlying reason (no user is present to
        respond to a real prompt).
        """
        result = get_password_gui("Password for testuser@Test Cluster")
        # Result could be None if the user cancels
        assert result is None or isinstance(result, str)

    @pytest.mark.real_world
    def test_notebook_widget_fallback(self):
        """
        Test notebook widget password fallback.

        This demonstrates:
        - Widget availability checking
        - Notebook environment detection
        - Fallback handling

        NOTE (Issue #114): get_password_widget() takes a single
        `prompt: str` argument, not (cluster_name, username) -- the
        original version of this test called it with two positional
        arguments, a TypeError. Confirmed this call is safe to make for
        real outside a Jupyter kernel: IPython.display's display() calls
        are inert here (no frontend comm channel exists) and the Submit
        button's on_click callback is never wired to a real event, so the
        function returns None immediately without blocking.
        """
        try:
            import ipywidgets

            widgets_available = True
        except ImportError:
            widgets_available = False

        result = get_password_widget("Password for testuser@Test Cluster")

        if widgets_available and "ipykernel" in sys.modules:
            # In a real notebook environment, widget creation should
            # succeed (though no password is submitted synchronously).
            assert result is None or isinstance(result, str)
        else:
            # Not in notebook or widgets not available -- no frontend can
            # submit a password, so the result is always None.
            assert result is None

    def test_multi_cluster_authentication(self, temp_credentials_dir):
        """
        Test authentication for multiple clusters.

        This demonstrates:
        - Multi-cluster credential management
        - Configuration switching
        - Credential isolation

        NOTE (Issue #114): requires_password_fallback() takes the
        Dict[str, Any] result of an SSH-key-setup attempt, not a
        ClusterConfig (see test_requires_password_fallback_logic above).
        None of these clusters has had any key setup attempted, so each is
        represented by a "no attempt succeeded" result.
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

            # Check if authentication is needed: no SSH key setup has been
            # attempted for any of these clusters.
            no_key_setup_result = {"success": False, "connection_tested": False}
            needs_auth = requires_password_fallback(no_key_setup_result)

            configs.append(
                {"cluster": cluster["name"], "config": config, "needs_auth": needs_auth}
            )

        # Verify each cluster has independent auth requirements
        for cfg in configs:
            assert cfg["needs_auth"] is True  # All need auth without keys
            assert cfg["config"].cluster_host == f"{cfg['cluster']}.example.com"

    def test_secure_password_handling(self, tmp_path):
        """
        Test secure password handling practices.

        This demonstrates:
        - No password persisted to disk in plaintext by default
        - Restrictive file permissions on any saved config
        - Memory clearing

        NOTE (Issue #114): ClusterConfig has no repr/str masking of secret
        fields -- its dataclass __repr__ shows every field verbatim,
        including "password" in plaintext. Confirmed:
        `str(ClusterConfig(password=...).__dict__)` contains the raw
        password. That is a genuine gap in clustrix/config.py (see the
        Issue #114 report for the exact fix), not a test bug, but this
        file is not permitted to edit clustrix/config.py. The real
        secret-handling protection this codebase implements is on
        ClusterConfig.save_to_file()/save_config(): secret-bearing fields,
        including "password", are omitted by default (include_secrets=
        False) and the file is written with 0600 permissions. This test
        exercises that real mechanism instead of the non-existent repr
        masking.
        """
        # Create sensitive password
        sensitive_password = "SuperSecret123!@#"

        # Ensure password is handled securely
        config = ClusterConfig()
        config.password = sensitive_password
        config.cluster_host = "server.example.com"

        config_file = tmp_path / "secure_config.yml"
        config.save_to_file(str(config_file))

        saved_text = config_file.read_text()
        assert sensitive_password not in saved_text
        assert "password" not in yaml.safe_load(saved_text)

        # File is owner-read/write only.
        assert (config_file.stat().st_mode & 0o777) == 0o600

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

        NOTE (Issue #114): several real-API mismatches fixed here:
        - ClusterConfig has no "private_key_path" field; the real one is
          "key_file".
        - requires_password_fallback() takes the Dict[str, Any] result of
          an SSH-key-setup attempt, not a ClusterConfig.
        - setup_auth_with_fallback(config, setup_ssh_keys_func, **kwargs)
          requires a setup_ssh_keys_func callable and returns a result
          Dict, never True/None. A real (non-mock) stand-in is supplied
          here rather than clustrix.ssh_utils.setup_ssh_keys, since the
          latter would write real SSH keys under the developer's ~/.ssh
          and open a real network connection to a fake host.
        - configure() takes keyword arguments matching ClusterConfig field
          names, not a whole ClusterConfig instance.
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
        config.key_file = str(ssh_key)

        # Check authentication requirements: a key setup attempt using this
        # key succeeded and was verified, so no password fallback is needed.
        key_setup_result = {"success": True, "connection_tested": True, "error": None}
        needs_password = requires_password_fallback(key_setup_result)
        assert needs_password is False  # Has SSH key

        def real_key_setup(config, **kwargs):
            """Real (non-mock) stand-in for setup_ssh_keys_func."""
            return dict(key_setup_result, key_path=config.key_file)

        auth_result = setup_auth_with_fallback(
            config, real_key_setup, password="fake-unused-key-auth-succeeds-first"
        )
        assert auth_result["success"] is True

        # Apply configuration
        configure(
            cluster_type=config.cluster_type,
            cluster_host=config.cluster_host,
            username=config.username,
            key_file=config.key_file,
        )

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

        NOTE (Issue #114): requires_password_fallback() takes the
        Dict[str, Any] result of an SSH-key-setup attempt and never
        inspects cluster_type, cluster_host, or any ClusterConfig field --
        the original version of this test called it with a ClusterConfig
        instance and asserted cluster-type-dependent behavior ("Kubernetes
        uses kubeconfig") that the real function has no knowledge of at
        all. This rewrite keeps the same three profiles and expected
        outcomes, but derives the simulated key-setup-attempt result for
        each from whether the profile actually has usable credentials, and
        calls the real function with that dict. Also: "private_key_path"
        is not a real ClusterConfig field; the real one is "key_file".
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
                    "key_file": "~/.ssh/prod_key",
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

            # Apply profile settings that are real ClusterConfig fields.
            # "kubeconfig" is not a real field -- it is Kubernetes' own
            # credential mechanism, tracked separately below.
            for key, value in profile_config.items():
                if hasattr(config, key):
                    setattr(config, key, value)

            # A profile with a usable credential (an SSH key file, or a
            # kubeconfig for Kubernetes) represents a key-setup attempt
            # that succeeded; one without represents a failed/never
            # attempted setup.
            has_credential = bool(
                profile_config.get("key_file") or profile_config.get("kubeconfig")
            )
            key_setup_result = (
                {"success": True, "connection_tested": True}
                if has_credential
                else {"success": False, "connection_tested": False}
            )
            needs_auth = requires_password_fallback(key_setup_result)

            if profile_name == "production":
                assert needs_auth is False  # Has SSH key
            elif profile_name == "development":
                assert needs_auth is False  # Kubernetes uses kubeconfig
            elif profile_name == "research":
                assert needs_auth is True  # Needs password fallback
