"""Unit tests for FlexibleCredentialManager."""

import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

from clustrix.credential_manager import (
    FlexibleCredentialManager,
    DotEnvCredentialSource,
    EnvironmentCredentialSource,
    GitHubActionsCredentialSource,
    get_credential_manager,
)
from clustrix.config import get_config_dir
import clustrix.credential_manager as credential_manager_module
from clustrix.credential_release import describe_credential


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

    def test_get_huggingface_credentials(self):
        """Test that HuggingFace credentials are read out of a real .env file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("HF_TOKEN=hf_test_token\n")
            f.write("HF_USERNAME=test-user\n")
            env_path = Path(f.name)

        try:
            # Clear the environment first so the values can only have come
            # from parsing the file on disk, not from the ambient shell.
            with patch.dict(os.environ, {}, clear=True):
                source = DotEnvCredentialSource(env_path)
                creds = source.get_credentials("huggingface")

                assert creds is not None
                assert creds["token"] == "hf_test_token"
                assert creds["username"] == "test-user"
        finally:
            env_path.unlink()

    def test_get_ssh_credentials(self):
        """Test that SSH credentials are read out of a real .env file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("SSH_HOST=cluster.example.edu\n")
            f.write("SSH_USERNAME=researcher\n")
            f.write("SSH_PORT=2222\n")
            env_path = Path(f.name)

        try:
            with patch.dict(os.environ, {}, clear=True):
                source = DotEnvCredentialSource(env_path)
                creds = source.get_credentials("ssh")

                assert creds is not None
                assert creds["host"] == "cluster.example.edu"
                assert creds["username"] == "researcher"
                assert creds["port"] == "2222"
        finally:
            env_path.unlink()

    @pytest.mark.parametrize(
        "provider", ["aws", "azure", "gcp", "kubernetes", "lambda_cloud"]
    )
    def test_removed_backends_have_no_credentials(self, provider):
        """Credentials for deleted, never-verified backends are not resolvable.

        The backends themselves were removed, so their credential entries went
        with them: asking for them must behave exactly like asking for any
        other unknown provider.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("AWS_ACCESS_KEY_ID=test_key\n")
            f.write("AWS_SECRET_ACCESS_KEY=test_secret\n")
            f.write("KUBECONFIG=/tmp/kubeconfig\n")
            f.write("LAMBDA_CLOUD_API_KEY=test_key\n")
            env_path = Path(f.name)

        try:
            source = DotEnvCredentialSource(env_path)
            assert source.get_credentials(provider) is None
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
        """Nothing configured must be reported as nothing configured.

        The assertion here used to be ``ssh_creds == {"port": "22"}``, and
        that was the bug rather than the specification: ``SSH_PORT`` was
        looked up with a ``"22"`` default, so the filtered dictionary was
        never empty and ``ensure_credential("ssh")`` could never be
        ``None``. Every caller that tests for ``None`` to mean "not
        configured" -- ``auth_methods.FlexibleCredentialAuth``,
        ``executor_connections`` -- therefore never saw it, and matched a
        blank host against the host it was asked to connect to. The default
        port now applies only to a credential set that already holds
        something real.
        """
        with patch.dict(os.environ, {}, clear=True):
            source = EnvironmentCredentialSource()

            assert source.get_credentials("ssh") is None

            # HuggingFace has no defaults, so with nothing in the environment
            # every field filters out and the whole provider returns None.
            hf_creds = source.get_credentials("huggingface")
            assert hf_creds is None

    def test_the_default_port_still_applies_to_a_real_credential(self):
        """The default must not have been removed, only narrowed."""
        with patch.dict(os.environ, {"SSH_HOST": "cluster.example.edu"}, clear=True):
            creds = EnvironmentCredentialSource().get_credentials("ssh")

        assert creds == {"host": "cluster.example.edu", "port": "22"}

    @patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "test_key",
            "AWS_SECRET_ACCESS_KEY": "test_secret",
            "KUBECONFIG": "/tmp/kubeconfig",
            "LAMBDA_CLOUD_API_KEY": "test_key",
        },
    )
    @pytest.mark.parametrize(
        "provider", ["aws", "azure", "gcp", "kubernetes", "lambda_cloud"]
    )
    def test_removed_backends_have_no_credentials(self, provider):
        """Deleted backends resolve to nothing even with their env vars set."""
        source = EnvironmentCredentialSource()
        assert source.get_credentials(provider) is None


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
            "HF_TOKEN": "hf_gh_token",
            "HF_USERNAME": "gh-user",
        },
    )
    def test_get_huggingface_credentials(self):
        """Test getting HuggingFace credentials in GitHub Actions."""
        source = GitHubActionsCredentialSource()
        creds = source.get_credentials("huggingface")

        assert creds is not None
        assert creds["token"] == "hf_gh_token"
        assert creds["username"] == "gh-user"

    @patch.dict(
        os.environ,
        {
            "GITHUB_ACTIONS": "true",
            "AWS_ACCESS_KEY_ID": "gh_key",
            "AWS_ACCESS_KEY": "gh_secret",
            "GCP_PROJECT_ID": "gh-project",
            "GCP_JSON": "{}",
        },
    )
    @pytest.mark.parametrize("provider", ["aws", "gcp"])
    def test_removed_backends_have_no_credentials(self, provider):
        """GitHub Actions secrets for deleted backends are no longer honored."""
        source = GitHubActionsCredentialSource()
        assert source.get_credentials(provider) is None
        assert source.list_available_providers() == []


class TestFlexibleCredentialManager:
    """Test FlexibleCredentialManager functionality."""

    def test_initialization(self):
        """Test that manager initializes correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            manager = FlexibleCredentialManager(config_dir)

            assert manager.config_dir == config_dir
            assert manager.env_file == config_dir / ".env"
            # Three credential sources: .env, environment variables, and
            # GitHub Actions secrets. There used to be a fourth (1Password),
            # deliberately removed in Issue #97 ("Remove all 1Password
            # integration -- use only .env, environment vars, and GitHub
            # secrets"); this assertion is stale from before that removal
            # (Issue #114).
            assert len(manager.sources) == 3

    def test_env_file_creation(self):
        """Test that .env file is created automatically."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            manager = FlexibleCredentialManager(config_dir)

            assert manager.env_file.exists()
            if os.name != "nt":
                # POSIX permission bits are a POSIX property: on Windows
                # readability is decided by NTFS ACLs and chmod only toggles
                # the read-only attribute, so there is no 0600 to assert.
                # Documented in docs/source/limitations.rst.
                assert (
                    manager.env_file.stat().st_mode & 0o777 == 0o600
                )  # Secure permissions

    def test_credential_retrieval_success(self):
        """Successful retrieval, asked for the way callers must now ask.

        ``ensure_credential`` was public and took no recipient; it is
        ``_ensure_credential_unchecked`` and raises for anyone but the gate
        (issue #167). The supported questions are "what is configured"
        (:func:`describe_credential`, no secret) and "may this host have it"
        (:func:`release_credential`, recipient first).
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)

            # Create .env file with SSH credentials
            env_file = config_dir / ".env"
            env_file.parent.mkdir(exist_ok=True)
            env_file.write_text(
                "SSH_HOST=cluster.example.edu\nSSH_USERNAME=researcher\n"
            )

            with patch.dict(
                os.environ, {"CLUSTRIX_CONFIG_DIR": str(config_dir)}, clear=True
            ):
                credential_manager_module._credential_manager = None
                described = describe_credential("ssh")

                assert described.available
                assert described.host == "cluster.example.edu"
                assert described.username == "researcher"

    def test_credential_retrieval_not_found(self):
        """Test credential retrieval when credentials don't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)

            with patch.dict(os.environ, {}, clear=True):
                manager = FlexibleCredentialManager(config_dir)

                assert manager._configured_fields("nonexistent") == (None, [])

    def test_the_store_refuses_a_caller_that_is_not_the_gate(self):
        """Lock 3, live rather than decorative.

        This test module is not ``clustrix.credential_release``, so the
        store raises. That is what makes an eighth route fail on its first
        run instead of at review.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = FlexibleCredentialManager(Path(temp_dir))

            with pytest.raises(RuntimeError) as raised:
                manager._ensure_credential_unchecked("ssh")

            assert "release_credential" in str(raised.value)

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

            # Should have all three sources (see test_initialization for why
            # it's three, not four -- Issue #114).
            assert len(status["sources"]) == 3

            # Should have all supported providers
            # Only the backends that are actually supported: pbs/sge/
            # kubernetes and every cloud VM backend were removed as
            # never-verified, and their credentials went with them.
            assert set(status["providers"]) == {"ssh", "huggingface", "local"}


class TestGlobalCredentialManager:
    """Test global credential manager singleton."""

    def test_get_credential_manager_singleton(self):
        """Test that get_credential_manager returns the same instance."""
        manager1 = get_credential_manager()
        manager2 = get_credential_manager()

        assert manager1 is manager2

    def test_get_credential_manager_default_location(self):
        """Test that default manager uses correct location.

        NOTE (Issue #114): tests/conftest.py's session-scoped autouse
        `isolate_config_dir` fixture points CLUSTRIX_CONFIG_DIR at a
        throwaway temp directory for the entire test run specifically so
        the suite never writes into a real ~/.clustrix. That means the
        real "default location" during tests is never
        Path.home() / ".clustrix" -- it's wherever get_config_dir()
        resolves to (which honors CLUSTRIX_CONFIG_DIR). Hardcoding
        Path.home() / ".clustrix" here encoded pre-isolation-fixture
        behavior; asserting against get_config_dir() instead is correct
        both with and without that env var set, and confirms no test
        touches the developer's real ~/.clustrix.
        """
        manager = get_credential_manager()

        assert manager.config_dir == get_config_dir()
