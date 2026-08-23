"""Legacy secure credential management module.

This module previously provided 1Password CLI integration for Clustrix.
1Password support has been removed as of Issue #97.

For credential management, please use:
- ~/.clustrix/.env file for environment-based credentials
- clustrix.credential_manager for the new flexible credential system
- clustrix.cli_credentials for command-line credential management
"""

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
    """HuggingFace credentials for external service validation.

    HuggingFace only. There used to be a ``get_ssh_credentials`` here that
    returned ``None`` unconditionally, which is worse than not having one:
    a caller reads the ``None`` as "no SSH credentials are configured"
    rather than "this class never had any to give". SSH credentials come
    from :mod:`clustrix.credential_manager`.
    """

    def __init__(self):
        logger.info("Using the clustrix credential manager for validation credentials")

    def get_huggingface_credentials(self) -> Optional[Dict[str, str]]:
        """Get HuggingFace credentials from every supported source.

        This read ``os.environ`` directly, which worked only by accident:
        some earlier lookup in the same process called ``load_dotenv`` and
        exported ``~/.clustrix/.env`` into the environment, so a token that
        lived *only* in that file appeared to be an environment variable.
        Removing that process-wide export (issue #153) made the accident
        visible as a regression -- ``tests/real_world/test_credential_access.py``
        and ``scripts/debug_huggingface_auth.py`` both stopped finding a
        token they were correctly configured to have.

        Going through
        :func:`clustrix.credential_release.release_credential` fixes it
        properly rather than by re-exporting: that is the supported lookup,
        it consults the environment *and* ``~/.clustrix/.env``, and it
        honours the ``HUGGINGFACE_*``/``HF_*`` aliases from one table so the
        two sources cannot disagree about which names count.

        The recipient is ``huggingface.co``, and it is a
        :meth:`~clustrix.credential_release.CredentialTarget.fixed_service`
        because no configuration file can move it: unlike ``cluster_host``,
        nothing untrusted can have chosen who receives this token.

        That was not true while it was written here, and route 13b is why:
        the *decision* named ``huggingface.co``, but every client built
        around the released token was ``HfApi(token=...)`` with no
        ``endpoint=``, which ``huggingface_hub`` fills in from
        ``$HF_ENDPOINT``. An inherited environment variable chose where the
        token actually went. It is true now because
        :func:`clustrix.credential_release.huggingface_client_kwargs` pins
        the client to the host the gate decided about.
        """
        from .credential_release import (
            CredentialTarget,
            describe_credential,
            release_credential,
        )

        target = CredentialTarget.fixed_service(
            "huggingface.co",
            why="the HuggingFace Hub API, which is compiled in rather than configured",
        )
        release = release_credential(target, provider="huggingface")
        if not release.token:
            return None
        return {
            "token": release.token,
            # Kept as "" rather than absent: every caller indexes it.
            "username": describe_credential("huggingface").username,
        }
