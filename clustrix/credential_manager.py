"""Flexible credential management with automatic .env file creation.

This module implements simplified credential management following the orchestrator pattern,
automatically creating and managing ~/.clustrix/.env with secure permissions and
multiple fallback sources.
"""

import os
import logging
from pathlib import Path
from typing import Dict, Mapping, Optional, List, Any
from abc import ABC, abstractmethod
from .config import get_config_dir, write_text_securely  # noqa: F401

# Try to import python-dotenv. ``dotenv_values`` is used rather than
# ``load_dotenv``: the latter copies the whole file into ``os.environ``,
# which is the process-wide export this module deliberately does not do.
try:
    from dotenv import dotenv_values

    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False

logger = logging.getLogger(__name__)

#: The port assumed when a credential set names a host but no port. Applied
#: only to a set that already holds something real: it used to be the
#: default of the ``SSH_PORT`` lookup itself, so an unconfigured machine
#: produced ``{"port": "22"}`` and ``ensure_credential("ssh")`` could never
#: be ``None``. Every caller testing for "not configured" therefore never
#: saw it.
DEFAULT_SSH_PORT = "22"

#: Which environment variable names carry which credential field, per
#: provider. One table rather than one per source, because the two used to
#: disagree -- only the environment source honoured the ``HUGGINGFACE_*``
#: aliases -- and a credential that resolves from the shell but not from
#: ~/.clustrix/.env is indistinguishable from a missing credential.
PROVIDER_ENV_NAMES: Dict[str, Dict[str, tuple]] = {
    "ssh": {
        "host": ("SSH_HOST",),
        "username": ("SSH_USERNAME",),
        "password": ("SSH_PASSWORD",),
        "private_key_path": ("SSH_PRIVATE_KEY_PATH",),
        "port": ("SSH_PORT",),
    },
    "huggingface": {
        "token": ("HF_TOKEN", "HUGGINGFACE_TOKEN"),
        "username": ("HF_USERNAME", "HUGGINGFACE_USERNAME"),
    },
}


def resolve_provider_credentials(
    values: Mapping[str, Optional[str]], provider: str
) -> Optional[Dict[str, str]]:
    """Credentials for ``provider`` read out of the mapping ``values``.

    ``values`` is any name-to-value mapping -- ``os.environ``, or the parsed
    contents of a ``.env`` file. Nothing is written back to it: reading a
    credential is a read, and a lookup that also exports the file into
    ``os.environ`` changes what every later import in the process sees.

    Returns ``None`` when nothing for the provider is configured, so that
    "not configured" is distinguishable from "configured with defaults".
    """
    if provider == "local":
        return {"type": "local"}  # local execution needs no real credentials

    names = PROVIDER_ENV_NAMES.get(provider)
    if names is None:
        return None

    credentials = {}
    for field, candidates in names.items():
        for candidate in candidates:
            value = values.get(candidate)
            if value:
                credentials[field] = value
                break

    if not credentials:
        return None
    if provider == "ssh":
        credentials.setdefault("port", DEFAULT_SSH_PORT)
    return credentials


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a ``.env`` file into a dictionary, touching nothing else.

    ``load_dotenv`` was used here, and it copies every key in the file into
    ``os.environ`` for the remaining life of the process. That leaked real
    AWS and HuggingFace credentials into the environment of every test that
    happened to run afterwards, and made one test pass in CI (no ``.env``
    present) while failing on any developer machine that had one -- an
    asymmetry CI cannot see.
    """
    if HAS_DOTENV:
        return {k: v for k, v in dotenv_values(path).items() if v is not None}

    logger.debug("python-dotenv not available, parsing %s manually", path)
    values: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip('"').strip("'")
    except Exception as e:
        logger.debug(f"Failed to read .env file: {e}")
    return values


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
        """Get credentials for a provider from .env file.

        The file is layered *under* the ambient environment, matching what
        ``load_dotenv`` did (it does not override an already-set variable),
        but resolved in a local dictionary so that nothing in the file
        becomes visible to the rest of the process.
        """
        if not self.is_available():
            return None

        values = {**parse_env_file(self.env_file_path), **os.environ}
        return resolve_provider_credentials(values, provider)

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


class EnvironmentCredentialSource(CredentialSource):
    """Credential source that reads from environment variables."""

    def is_available(self) -> bool:
        """Environment variables are always available."""
        return True

    def get_credentials(self, provider: str) -> Optional[Dict[str, str]]:
        """Get credentials from environment variables."""
        return resolve_provider_credentials(os.environ, provider)

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
