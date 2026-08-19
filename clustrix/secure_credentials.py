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
from typing import Dict, Optional

logger = logging.getLogger(__name__)

#: What a caller of the removed 1Password API should do instead. Kept as one
#: string so the deprecation error and the module docstring cannot drift.
REPLACEMENT_GUIDANCE = (
    "1Password support was removed from Clustrix in issue #97. "
    "Store credentials in ~/.clustrix/.env (see `clustrix credentials setup`) "
    "and read them with clustrix.credential_manager instead."
)


class SecureCredentialManager:
    """Legacy credential manager - 1Password support removed.

    Every retrieval method reports "no credential" because the backing store
    is gone; :meth:`store_credential` raises instead, because a write that
    silently reports failure looks identical to a credential that was saved
    and then lost.
    """

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
        """Always raises: there is no store to write to.

        Raises:
            NotImplementedError: always, naming the supported alternative.
        """
        raise NotImplementedError(
            f"SecureCredentialManager.store_credential cannot store {item_name!r}: "
            + REPLACEMENT_GUIDANCE
        )


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
