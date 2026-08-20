"""Flexible credential management with automatic .env file creation.

This module implements simplified credential management following the orchestrator pattern,
automatically creating and managing ~/.clustrix/.env with secure permissions and
multiple fallback sources.
"""

import os
import logging
from pathlib import Path
from typing import Dict, Optional, List, Any
from abc import ABC, abstractmethod
from .config import get_config_dir, write_text_securely  # noqa: F401

# Try to import python-dotenv
try:
    from dotenv import load_dotenv

    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False

logger = logging.getLogger(__name__)


class CredentialSource(ABC):
    """Abstract base class for credential sources."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this credential source is available."""
        pass

    @abstractmethod
    def get_credentials(self, provider: str) -> Optional[Dict[str, str]]:
        """Get credentials for a specific provider."""
        pass

    @abstractmethod
    def list_available_providers(self) -> List[str]:
        """List all providers available from this source."""
        pass


class DotEnvCredentialSource(CredentialSource):
    """Credential source that reads from .env files."""

    def __init__(self, env_file_path: Path):
        self.env_file_path = env_file_path

    def is_available(self) -> bool:
        """Check if .env file exists and is readable."""
        return self.env_file_path.exists() and self.env_file_path.is_file()

    def get_credentials(self, provider: str) -> Optional[Dict[str, str]]:
        """Get credentials for a provider from .env file."""
        if not self.is_available():
            return None

        # Load environment variables from .env file
        if HAS_DOTENV:
            load_dotenv(self.env_file_path)
        else:
            logger.warning(
                "python-dotenv not available, falling back to manual parsing"
            )
            self._load_env_manual()

        # Map providers to their environment variable patterns
        provider_mappings: Dict[str, Dict[str, Optional[str]]] = {
            "ssh": {
                "host": os.getenv("SSH_HOST"),
                "username": os.getenv("SSH_USERNAME"),
                "password": os.getenv("SSH_PASSWORD"),
                "private_key_path": os.getenv("SSH_PRIVATE_KEY_PATH"),
                "port": os.getenv("SSH_PORT", "22"),
            },
            "huggingface": {
                "token": os.getenv("HF_TOKEN"),
                "username": os.getenv("HF_USERNAME"),
            },
            "local": {
                "type": "local",  # Local provider needs no real credentials
            },
        }

        if provider not in provider_mappings:
            return None

        credentials = provider_mappings[provider]

        # Filter out None values and return only if we have some credentials
        filtered_credentials = {k: v for k, v in credentials.items() if v is not None}
        return filtered_credentials if filtered_credentials else None

    def list_available_providers(self) -> List[str]:
        """List providers that have credentials available in .env file."""
        available = []
        providers = [
            "ssh",
            "huggingface",
            "local",
        ]

        for provider in providers:
            if self.get_credentials(provider):
                available.append(provider)

        return available

    def _load_env_manual(self):
        """Manually load .env file if python-dotenv is not available."""
        try:
            with open(self.env_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        os.environ[key] = value
        except Exception as e:
            logger.debug(f"Failed to manually load .env file: {e}")


class EnvironmentCredentialSource(CredentialSource):
    """Credential source that reads from environment variables."""

    def is_available(self) -> bool:
        """Environment variables are always available."""
        return True

    def get_credentials(self, provider: str) -> Optional[Dict[str, str]]:
        """Get credentials from environment variables."""
        # Use same mapping as DotEnv but read directly from current environment
        provider_mappings: Dict[str, Dict[str, Optional[str]]] = {
            "ssh": {
                "host": os.getenv("SSH_HOST"),
                "username": os.getenv("SSH_USERNAME"),
                "password": os.getenv("SSH_PASSWORD"),
                "private_key_path": os.getenv("SSH_PRIVATE_KEY_PATH"),
                "port": os.getenv("SSH_PORT", "22"),
            },
            "huggingface": {
                "token": os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN"),
                "username": os.getenv("HF_USERNAME")
                or os.getenv("HUGGINGFACE_USERNAME"),
            },
            "local": {
                "type": "local",  # Local provider needs no real credentials
            },
        }

        if provider not in provider_mappings:
            return None

        credentials = provider_mappings[provider]

        # Filter out None values and return only if we have some credentials
        filtered_credentials = {k: v for k, v in credentials.items() if v is not None}
        return filtered_credentials if filtered_credentials else None

    def list_available_providers(self) -> List[str]:
        """List providers that have credentials available in environment."""
        available = []
        providers = [
            "ssh",
            "huggingface",
            "local",
        ]

        for provider in providers:
            if self.get_credentials(provider):
                available.append(provider)

        return available


class GitHubActionsCredentialSource(CredentialSource):
    """Credential source for GitHub Actions environment."""

    def is_available(self) -> bool:
        """Check if running in GitHub Actions."""
        return os.getenv("GITHUB_ACTIONS") == "true"

    def get_credentials(self, provider: str) -> Optional[Dict[str, str]]:
        """Get credentials from GitHub Actions secrets."""
        if not self.is_available():
            return None

        # GitHub Actions specific environment variable patterns
        if provider == "huggingface":
            token = os.getenv("HF_TOKEN")
            if token:
                username = os.getenv("HF_USERNAME")
                result = {"token": token}
                if username:
                    result["username"] = username
                return result

        return None

    def list_available_providers(self) -> List[str]:
        """List providers available in GitHub Actions."""
        if not self.is_available():
            return []

        available = []
        providers = ["huggingface"]

        for provider in providers:
            if self.get_credentials(provider):
                available.append(provider)

        return available


class FlexibleCredentialManager:
    """Main credential manager with automatic .env file creation and multiple sources."""

    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize credential manager with automatic setup."""
        self.config_dir = config_dir or get_config_dir()
        self.env_file = self.config_dir / ".env"

        # Initialize credential sources in priority order
        self.sources = [
            DotEnvCredentialSource(self.env_file),
            EnvironmentCredentialSource(),
            GitHubActionsCredentialSource(),
        ]

        # Ensure setup is complete
        self._ensure_setup()

    def _ensure_setup(self):
        """Ensure .clustrix directory and .env file exist with proper setup."""
        try:
            # Create directory with secure permissions (owner only)
            self.config_dir.mkdir(mode=0o700, exist_ok=True)

            # Create .env template if missing
            if not self.env_file.exists():
                self._create_env_template()
                logger.info(f"Created credential template: {self.env_file}")
                logger.info("Edit this file to add your credentials")

        except Exception as e:
            logger.warning(f"Failed to set up credential files: {e}")

    def _create_env_template(self):
        """Create .env file with comprehensive template."""
        template = self._generate_env_template()

        try:
            # Write template with secure permissions
            # Explicit UTF-8: the template contains non-ASCII characters,
            # and the default locale encoding on Windows (cp1252) cannot
            # encode them -- which left a zero-byte .env behind.
            write_text_securely(self.env_file, template)

        except Exception as e:
            logger.warning(f"Failed to create .env template: {e}")

    def _generate_env_template(self) -> str:
        """Generate comprehensive .env template with all supported providers."""
        return """# Clustrix Credential Configuration
# Created automatically - uncomment and add your credentials below
# File permissions: 600 (owner read/write only)
#
# Priority order: .env file → environment variables → GitHub Actions

# ============================================================================
# SSH Cluster Credentials (for ssh and slurm clusters)
# ============================================================================
# SSH_HOST=your-cluster.university.edu
# SSH_USERNAME=your_username
# SSH_PASSWORD=your_password
# SSH_PRIVATE_KEY_PATH=/path/to/your/private/key
# SSH_PORT=22

# ============================================================================
# HuggingFace Credentials (for HuggingFace Jobs execution)
# ============================================================================
# HF_TOKEN=hf_your_token_here
# HF_USERNAME=your-huggingface-username

# ============================================================================
# Additional Notes
# ============================================================================
# 1. Uncomment (remove #) and fill in the credentials you need
# 2. Keep unused credentials commented out for security
# 3. Never commit this file to version control
# 4. Use 'clustrix credentials test' to validate your setup
# 5. Use 'clustrix credentials edit' to safely edit this file
"""

    def load_credentials_optional(
        self, provider: Optional[str] = None
    ) -> Dict[str, Dict[str, str]]:
        """Load available credentials from all sources.

        Args:
            provider: Specific provider to load, or None for all providers

        Returns:
            Dictionary mapping provider names to their credentials
        """
        credentials = {}

        if provider:
            # Load credentials for specific provider
            for source in self.sources:
                try:
                    creds = source.get_credentials(provider)
                    if creds:
                        credentials[provider] = creds
                        logger.debug(
                            f"Loaded {provider} credentials from {source.__class__.__name__}"
                        )
                        break  # Use first successful source
                except Exception as e:
                    logger.debug(
                        f"Failed to load {provider} from {source.__class__.__name__}: {e}"
                    )
        else:
            # Load all available credentials
            all_providers = ["ssh", "huggingface"]

            for prov in all_providers:
                for source in self.sources:
                    try:
                        creds = source.get_credentials(prov)
                        if creds and prov not in credentials:
                            credentials[prov] = creds
                            logger.debug(
                                f"Loaded {prov} credentials from {source.__class__.__name__}"
                            )
                            break  # Use first successful source
                    except Exception as e:
                        logger.debug(
                            f"Failed to load {prov} from {source.__class__.__name__}: {e}"
                        )

        return credentials

    def ensure_credential(self, provider: str) -> Optional[Dict[str, str]]:
        """Get credentials for a specific provider with detailed feedback.

        Args:
            provider: Provider name (ssh, huggingface, local)

        Returns:
            Credentials dictionary or None if not available
        """
        logger.debug(f"Looking up {provider} credentials...")

        for source in self.sources:
            source_name = source.__class__.__name__

            try:
                if not source.is_available():
                    logger.debug(f"  • {source_name}: Not available")
                    continue

                logger.debug(f"  • {source_name}: Checking...")
                credentials = source.get_credentials(provider)

                if credentials:
                    logger.info(
                        f"  ✅ {provider} credentials loaded from {source_name}"
                    )
                    return credentials
                else:
                    logger.debug(f"  • {source_name}: No {provider} credentials found")

            except Exception as e:
                logger.debug(f"  • {source_name}: Error - {e}")

        logger.warning(f"  ❌ No {provider} credentials found in any source")

        # Provide helpful guidance
        if provider in ["ssh", "huggingface"]:
            logger.info(f"  💡 Add {provider} credentials to: {self.env_file}")
            logger.info("  💡 Or use: clustrix credentials setup")

        return None

    def get_missing_providers(self, required: List[str]) -> List[str]:
        """Identify which required providers are missing credentials.

        Args:
            required: List of required provider names

        Returns:
            List of missing provider names
        """
        missing = []

        for provider in required:
            if not self.ensure_credential(provider):
                missing.append(provider)

        return missing

    def list_available_providers(self) -> Dict[str, str]:
        """List all providers with available credentials and their sources.

        Returns:
            Dictionary mapping provider names to their credential source
        """
        available = {}

        for provider in ["ssh", "huggingface"]:
            for source in self.sources:
                try:
                    if source.is_available() and source.get_credentials(provider):
                        available[provider] = source.__class__.__name__
                        break
                except Exception:
                    continue

        return available

    def get_credential_status(self) -> Dict[str, Any]:
        """Get comprehensive status of all credential sources and providers.

        Returns:
            Status dictionary with source availability and provider credentials
        """
        status: Dict[str, Any] = {
            "config_directory": str(self.config_dir),
            "env_file": str(self.env_file),
            "env_file_exists": self.env_file.exists(),
            "sources": {},
            "providers": {},
        }

        # Check each source
        for source in self.sources:
            source_name = source.__class__.__name__
            try:
                source_status: Dict[str, Any] = {
                    "available": source.is_available(),
                    "providers": source.list_available_providers(),
                }
                status["sources"][source_name] = source_status
            except Exception as e:
                error_status: Dict[str, Any] = {
                    "available": False,
                    "error": str(e),
                    "providers": [],
                }
                status["sources"][source_name] = error_status

        # Check each provider
        providers = [
            "ssh",
            "huggingface",
            "local",
        ]
        for provider in providers:
            credentials = self.ensure_credential(provider)
            if credentials:
                # Find which source provided the credentials
                source_name = "unknown"
                for source in self.sources:
                    try:
                        if source.is_available() and source.get_credentials(provider):
                            source_name = source.__class__.__name__
                            break
                    except Exception:
                        continue

                provider_status: Dict[str, Any] = {
                    "available": True,
                    "source": source_name,
                    "fields": list(credentials.keys()),
                }
                status["providers"][provider] = provider_status
            else:
                empty_status: Dict[str, Any] = {
                    "available": False,
                    "source": None,
                    "fields": [],
                }
                status["providers"][provider] = empty_status

        return status


# Global credential manager instance
_credential_manager: Optional[FlexibleCredentialManager] = None


def get_credential_manager() -> FlexibleCredentialManager:
    """Get the global flexible credential manager instance."""
    global _credential_manager
    if _credential_manager is None:
        _credential_manager = FlexibleCredentialManager()
    return _credential_manager


# Convenience functions for common credential operations
def load_credentials_optional(
    provider: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """Load available credentials from all sources."""
    manager = get_credential_manager()
    return manager.load_credentials_optional(provider)


def ensure_credential(provider: str) -> Optional[Dict[str, str]]:
    """Get credentials for a specific provider with fallbacks."""
    manager = get_credential_manager()
    return manager.ensure_credential(provider)


def get_missing_providers(required: List[str]) -> List[str]:
    """Identify which required providers are missing credentials."""
    manager = get_credential_manager()
    return manager.get_missing_providers(required)


def list_available_providers() -> Dict[str, str]:
    """List all providers with available credentials."""
    manager = get_credential_manager()
    return manager.list_available_providers()


def get_credential_status() -> Dict[str, Any]:
    """Get comprehensive credential system status."""
    manager = get_credential_manager()
    return manager.get_credential_status()
