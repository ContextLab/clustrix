"""Legacy secure credential management module.

This module previously provided 1Password CLI integration for Clustrix.
1Password support has been removed as of Issue #97.

For credential management, please use:
- ~/.clustrix/.env file for environment-based credentials
- clustrix.credential_manager for the new flexible credential system
- clustrix.cli_credentials for command-line credential management
"""

import os
import logging
from pathlib import Path
from typing import Dict, Optional
from .config import get_config_dir

logger = logging.getLogger(__name__)


class SecureCredentialManager:
    """Legacy credential manager - 1Password support removed."""

    def __init__(self, vault_name: str = "Private"):
        """Initialize legacy credential manager."""
        logger.warning(
            "1Password support has been removed from Clustrix. "
            "Please use ~/.clustrix/.env or the new credential system instead."
        )
        self.vault_name = vault_name

    def is_op_available(self) -> bool:
        """1Password CLI support removed."""
        return False

    def get_credential(
        self, item_name: str, field_name: str = "password"
    ) -> Optional[str]:
        """1Password credential retrieval no longer supported."""
        logger.warning("1Password credential retrieval is no longer supported")
        return None

    def get_structured_credential(self, item_name: str) -> Optional[Dict]:
        """1Password structured credential retrieval no longer supported."""
        logger.warning("1Password credential retrieval is no longer supported")
        return None

    def store_credential(
        self,
        item_name: str,
        credential_data: Dict[str, str],
        category: str = "API_CREDENTIAL",
    ) -> bool:
        """1Password credential storage no longer supported."""
        logger.warning("1Password credential storage is no longer supported")
        return False


class ValidationCredentials:
    """Provides credentials for external service validation using environment variables only."""

    def __init__(self):
        logger.info("Using environment variable fallback for validation credentials")

    def get_huggingface_credentials(self) -> Optional[Dict[str, str]]:
        """Get HuggingFace credentials from environment variables."""
        token = os.getenv("HUGGINGFACE_TOKEN") or os.getenv("HF_TOKEN")
        if token:
            return {"token": token, "username": os.getenv("HUGGINGFACE_USERNAME", "")}
        return None

    def get_ssh_credentials(self) -> Optional[Dict[str, str]]:
        """SSH credentials no longer available - use ~/.clustrix/.env instead."""
        return None


def ensure_secure_environment():
    """Ensure environment is set up securely for credential handling."""
    clustrix_dir = get_config_dir()
    clustrix_dir.mkdir(exist_ok=True)

    # Create .gitignore patterns to prevent credential leaks
    gitignore_patterns = [
        "# Clustrix security",
        "**/.clustrix/credentials/**",
        "**/.clustrix/keys/**",
        "**/clustrix-credentials.json",
        "**/clustrix-*.pem",
        "**/clustrix-*.key",
        "**/*-credentials.json",
        "**/*-service-account.json",
        ".env.local",
        ".env.validation",
    ]

    # Add patterns to project .gitignore if not already present
    gitignore_path = Path.cwd() / ".gitignore"
    if gitignore_path.exists():
        existing_content = gitignore_path.read_text()
        if "# Clustrix security" not in existing_content:
            with gitignore_path.open("a") as f:
                f.write("\n" + "\n".join(gitignore_patterns) + "\n")

    # Create secure credentials directory
    cred_dir = clustrix_dir / "credentials"
    cred_dir.mkdir(exist_ok=True)

    # Set restrictive permissions (Unix-like systems)
    try:
        cred_dir.chmod(0o700)  # rwx------
    except Exception:
        pass  # Windows or other systems

    return cred_dir
