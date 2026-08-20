"""
Credential management for real-world tests.

Credentials come from exactly two places, in this order:

1. ``~/.clustrix/.env`` and the process environment, read through
   :mod:`clustrix.credential_manager` (``SSH_HOST``, ``SSH_USERNAME``,
   ``SSH_PASSWORD``, ``SSH_PRIVATE_KEY_PATH``, ``SSH_PORT``).
2. Exported variables, including GitHub Actions secrets
   (``CLUSTRIX_USERNAME``, ``CLUSTRIX_PASSWORD``, ``HF_TOKEN``).

1Password was removed in issue #97. Nothing here reads it, and no test may
grow a third credential path: add sources to
:class:`clustrix.credential_manager.FlexibleCredentialManager` instead.
"""

import os
import logging
from typing import Dict, Optional, Any
from pathlib import Path

# Only used by the require_* helpers below, which skip rather than fail when a
# target is unconfigured. This module lives under tests/, so pytest is always
# installed alongside it.
import pytest

# The supported credential path. Guarded so that this module still imports
# when clustrix itself cannot be; HAS_SECURE_CREDENTIALS gates every use.
try:
    from clustrix.credential_manager import (
        ensure_credential as _clustrix_ensure_credential,
    )
    from clustrix.secure_credentials import ValidationCredentials

    HAS_SECURE_CREDENTIALS = True
except ImportError:  # pragma: no cover - clustrix is a hard dependency of tests
    HAS_SECURE_CREDENTIALS = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Test cluster targets
#
# Nothing in this repository names a real machine. A developer points the
# real-world suite at their own clusters by exporting the variables below;
# with none of them set every test that needs a remote target skips, with the
# variable name in the skip reason so it is obvious what did not run and why.
#
# This is the ONE place a hostname, username or remote directory is resolved.
# Tests ask for a role ("ssh", "slurm", ...), never for a hostname.
# ---------------------------------------------------------------------------

#: Role -> environment variable naming the host that plays it. "ssh" is a
#: plain SSH box (the GPU machine, in this project's setup) and "slurm" is a
#: scheduler head node; the "_2" roles are second machines of the same kind.
#: These are the same names scripts/verify_cluster_usecases.py and
#: scripts/collect_execution_evidence.py read, deliberately -- one set of
#: variables points the whole repository at one developer's clusters.
#:
#: Deliberately NOT falling back to the older bare TEST_SSH_HOST /
#: TEST_SSH_USERNAME names: `setup_environment_variables()` writes those on
#: import, defaulting the host to "localhost". Reading them here would make
#: `configured_test_hosts()` always report a resolvable host, and every
#: network gate below would open on a machine with no cluster access at all.
#: Those names still work where they always did, in get_ssh_credentials().
HOST_ENV_VARS = {
    "ssh": ("CLUSTRIX_TEST_SSH_HOST",),
    "ssh2": ("CLUSTRIX_TEST_SSH_HOST_2",),
    "slurm": ("CLUSTRIX_TEST_SLURM_HOST",),
    "slurm2": ("CLUSTRIX_TEST_SLURM_HOST_2",),
}

#: Account to log in as on any of the above.
USERNAME_ENV_VARS = ("CLUSTRIX_TEST_USERNAME",)

#: Writable directory on the cluster, e.g. a shared home or scratch space.
REMOTE_WORK_DIR_ENV_VAR = "CLUSTRIX_TEST_SLURM_REMOTE_DIR"


def _first_env(names) -> Optional[str]:
    """First of `names` set to a non-empty value, else None."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def get_test_host(role: str) -> Optional[str]:
    """Hostname configured for `role`, or None if the developer set none."""
    try:
        names = HOST_ENV_VARS[role]
    except KeyError:
        raise ValueError(
            f"unknown cluster role {role!r}; expected one of "
            f"{sorted(HOST_ENV_VARS)}"
        )
    return _first_env(names)


def get_test_username() -> Optional[str]:
    """Account to use on the test clusters, or None if unset."""
    return _first_env(USERNAME_ENV_VARS)


def get_test_remote_work_dir(default: Optional[str] = None) -> Optional[str]:
    """Writable directory on the test clusters, or `default` if unset."""
    return _first_env((REMOTE_WORK_DIR_ENV_VAR,)) or default


def configured_test_hosts():
    """Every distinct host the developer has configured, in role order.

    Used to decide whether the private cluster network is reachable at all.
    """
    seen = []
    for role in HOST_ENV_VARS:
        host = get_test_host(role)
        if host and host not in seen:
            seen.append(host)
    return tuple(seen)


def require_test_host(role: str) -> str:
    """Hostname for `role`, or skip the test saying which variable to set."""
    host = get_test_host(role)
    if not host:
        pytest.skip(
            f"No {role} test cluster configured: set "
            f"{HOST_ENV_VARS[role][0]} to a host you have access to"
        )
    return host


def require_test_username() -> str:
    """Username for the test clusters, or skip saying which variable to set."""
    username = get_test_username()
    if not username:
        pytest.skip(f"No test cluster account configured: set {USERNAME_ENV_VARS[0]}")
    return username


def require_test_remote_work_dir() -> str:
    """Remote work dir, or skip saying which variable to set."""
    work_dir = get_test_remote_work_dir()
    if not work_dir:
        pytest.skip(
            f"No remote work directory configured: set "
            f"{REMOTE_WORK_DIR_ENV_VAR} to a writable path on the cluster"
        )
    return work_dir


#: What to do when no credentials are found. One string, so the pytest skip
#: reasons and the messages printed by the standalone debug scripts cannot
#: drift apart -- and so nobody has to guess, as they did while these messages
#: still said "add credentials to 1Password" (removed in #97, issue #153).
CREDENTIAL_SETUP_HINT = (
    "Put SSH_USERNAME and SSH_PASSWORD (or SSH_PRIVATE_KEY_PATH) in "
    "~/.clustrix/.env -- `clustrix credentials setup` creates that file -- or "
    "export them, and set the CLUSTRIX_TEST_*_HOST variable for the cluster "
    "you want to reach"
)


def _clustrix_ssh_credentials() -> Dict[str, str]:
    """SSH credentials from ~/.clustrix/.env or the environment.

    This is the only supported source; an empty dict means "none configured",
    never "substitute something plausible".

    The environment is snapshotted and restored around the lookup. Reading the
    .env file goes through `load_dotenv`, which *exports* every variable in it
    -- so without this, importing this module (conftest does, for the whole
    `pytest tests/` run) published the developer's real AWS, GCP and HF
    credentials into every unrelated test's environment. Two credential-source
    tests failed because of it, which is how it was caught; the values we
    actually need come back in the return value, not in os.environ.
    """
    if not HAS_SECURE_CREDENTIALS:
        return {}
    environment = dict(os.environ)
    try:
        return _clustrix_ensure_credential("ssh") or {}
    except Exception as e:  # a broken .env must not abort collection
        logger.debug(f"clustrix ssh credential lookup failed: {e}")
        return {}
    finally:
        os.environ.clear()
        os.environ.update(environment)


def _nonempty(value: Optional[str]) -> Optional[str]:
    """`value` stripped, or None if it is empty or unset."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def get_cluster_credentials(role: str) -> Optional[Dict[str, str]]:
    """Login details for the cluster playing `role`, or None if unconfigured.

    `role` is one of the keys of :data:`HOST_ENV_VARS` ("ssh", "slurm", ...).
    The returned dictionary has the same shape everything else in this package
    expects::

        {"host", "username", "password", "private_key_path", "port"}

    The host comes from CLUSTRIX_TEST_<ROLE>_HOST (falling back to SSH_HOST),
    the account from CLUSTRIX_TEST_USERNAME / SSH_USERNAME / CLUSTRIX_USERNAME,
    and the secret from SSH_PASSWORD / SSH_PRIVATE_KEY_PATH / CLUSTRIX_PASSWORD.

    None is returned unless a host, an account **and** a secret are all
    present: connecting with two of the three only produces an authentication
    failure several seconds later, which reads like a broken cluster rather
    than an unconfigured laptop.
    """
    ssh = _clustrix_ssh_credentials()

    host = get_test_host(role) or _nonempty(ssh.get("host"))
    username = (
        get_test_username()
        or _nonempty(ssh.get("username"))
        or _nonempty(os.environ.get("CLUSTRIX_USERNAME"))
    )
    password = _nonempty(ssh.get("password")) or _nonempty(
        os.environ.get("CLUSTRIX_PASSWORD")
    )
    private_key_path = _nonempty(ssh.get("private_key_path"))

    if not host or not username or not (password or private_key_path):
        return None

    credentials = {
        "host": host,
        "username": username,
        # Present even when unset: existing callers, including several under
        # tests/integration, index this key directly, and a KeyError on a
        # key-only setup would be a worse signal than the None they used to
        # get from the old credential shape.
        "password": password,
        "port": str(ssh.get("port") or "22"),
    }
    if private_key_path:
        credentials["private_key_path"] = private_key_path
    return credentials


def require_cluster_credentials(role: str) -> Dict[str, str]:
    """Credentials for `role`, or skip the test saying exactly what is missing."""
    credentials = get_cluster_credentials(role)
    if not credentials:
        pytest.skip(
            f"No credentials for the {role} test cluster. {CREDENTIAL_SETUP_HINT}"
        )
    return credentials


class RealWorldCredentialManager:
    """Manages credentials for real-world testing with multiple fallback options."""

    def __init__(self):
        """Initialize credential manager."""
        self.is_github_actions = os.getenv("GITHUB_ACTIONS") == "true"
        self.is_local_development = not self.is_github_actions

        self._validation_creds = None
        if HAS_SECURE_CREDENTIALS:
            try:
                self._validation_creds = ValidationCredentials()
            except Exception as e:
                logger.debug(f"Failed to initialize validation credentials: {e}")

    def is_1password_available(self) -> bool:
        """Always False: 1Password was removed from clustrix in issue #97.

        Retained only because `scripts/run_real_world_tests.py` still prints
        it, and that file is outside this package; delete both together.
        """
        return False

    def get_ssh_credentials(self) -> Optional[Dict[str, str]]:
        """Get SSH credentials from available sources."""
        # ~/.clustrix/.env and the environment, via clustrix itself.
        configured = get_cluster_credentials("ssh")
        if configured:
            return configured

        # No separate GitHub Actions branch: get_cluster_credentials already
        # reads CLUSTRIX_USERNAME / CLUSTRIX_PASSWORD alongside the .env file,
        # so a branch here could never be reached.

        # Fall back to environment variables
        host = os.getenv("TEST_SSH_HOST", "localhost")
        username = os.getenv("TEST_SSH_USERNAME", os.getenv("USER"))
        password = os.getenv("TEST_SSH_PASSWORD")
        private_key_path = os.getenv("TEST_SSH_PRIVATE_KEY_PATH")
        port = os.getenv("TEST_SSH_PORT", "22")

        return {
            "host": host,
            "username": username,
            "password": password,
            "private_key_path": private_key_path,
            "port": port,
        }

    def get_gpu_cluster_credentials(self) -> Optional[Dict[str, str]]:
        """Get plain-SSH ("ssh" role, the GPU box here) cluster credentials."""
        return get_cluster_credentials("ssh")

    def get_slurm_cluster_credentials(self) -> Optional[Dict[str, str]]:
        """Get SLURM head-node ("slurm" role) cluster credentials."""
        return get_cluster_credentials("slurm")

    def get_slurm_credentials(self) -> Optional[Dict[str, str]]:
        """Get SLURM credentials from available sources."""
        configured = get_cluster_credentials("slurm")
        if configured:
            return configured

        # No separate GitHub Actions branch; see get_ssh_credentials.

        # Fall back to environment variables
        host = os.getenv("TEST_SLURM_HOST", "localhost")
        username = os.getenv("TEST_SLURM_USERNAME", os.getenv("USER"))
        password = os.getenv("TEST_SLURM_PASSWORD")

        if username and password:
            return {
                "host": host,
                "username": username,
                "password": password,
                "port": "22",
            }

        return None

    def get_huggingface_credentials(self) -> Optional[Dict[str, str]]:
        """Get HuggingFace credentials from available sources."""
        # Exported variables only, deliberately. Reading the token out of
        # ~/.clustrix/.env here would be resolved at import time by
        # setup_environment_variables() below, which exports what it finds --
        # publishing a real HF token into every unrelated test's environment
        # for the whole `pytest tests/` run. Tests that need the .env token
        # ask clustrix for it directly instead.
        if self._validation_creds:
            try:
                validation_creds = self._validation_creds.get_huggingface_credentials()
                if validation_creds:
                    return validation_creds
            except Exception as e:
                logger.debug(f"Failed to read HuggingFace credentials: {e}")

        # Exported environment variables (including GitHub Actions secrets)
        token = os.getenv("HUGGINGFACE_TOKEN") or os.getenv("HF_TOKEN")
        username = os.getenv("HUGGINGFACE_USERNAME") or os.getenv("HF_USERNAME")

        if token:
            return {"token": token, "username": username}

        return None

    def get_credential_status(self) -> Dict[str, bool]:
        """Get status of all credential types."""
        return {
            "ssh": self.get_ssh_credentials() is not None,
            "slurm": self.get_slurm_credentials() is not None,
            "huggingface": self.get_huggingface_credentials() is not None,
        }

    def print_credential_status(self) -> None:
        """Print credential status for debugging."""
        print("\n🔑 Credential Status:")
        print(
            f"  Environment: {'GitHub Actions' if self.is_github_actions else 'Local Development'}"
        )
        print(f"  Source: ~/.clustrix/.env and the environment")

        status = self.get_credential_status()
        for service, available in status.items():
            icon = "✅" if available else "❌"
            print(f"  {service.upper()}: {icon}")

        if not all(status.values()):
            print(f"  ℹ️  {CREDENTIAL_SETUP_HINT}")

    def setup_environment_variables(self) -> None:
        """Set up environment variables from available credentials."""
        # Set SSH credentials
        ssh_creds = self.get_ssh_credentials()
        if ssh_creds:
            if ssh_creds.get("host"):
                os.environ["TEST_SSH_HOST"] = ssh_creds["host"]
            if ssh_creds.get("username"):
                os.environ["TEST_SSH_USERNAME"] = ssh_creds["username"]
            if ssh_creds.get("password"):
                os.environ["TEST_SSH_PASSWORD"] = ssh_creds["password"]
            if ssh_creds.get("private_key_path"):
                os.environ["TEST_SSH_PRIVATE_KEY_PATH"] = ssh_creds["private_key_path"]

        # Set SLURM credentials
        slurm_creds = self.get_slurm_credentials()
        if slurm_creds:
            os.environ["TEST_SLURM_HOST"] = slurm_creds["host"]
            os.environ["TEST_SLURM_USERNAME"] = slurm_creds["username"]
            if slurm_creds.get("password"):
                os.environ["TEST_SLURM_PASSWORD"] = slurm_creds["password"]

        # Set HuggingFace credentials
        hf_creds = self.get_huggingface_credentials()
        if hf_creds:
            os.environ["HUGGINGFACE_TOKEN"] = hf_creds["token"]
            os.environ["HF_TOKEN"] = hf_creds["token"]
            if hf_creds.get("username"):
                os.environ["HUGGINGFACE_USERNAME"] = hf_creds["username"]
                os.environ["HF_USERNAME"] = hf_creds["username"]


# Global credential manager instance
_credential_manager = None


def get_credential_manager() -> RealWorldCredentialManager:
    """Get the global credential manager instance."""
    global _credential_manager
    if _credential_manager is None:
        _credential_manager = RealWorldCredentialManager()
    return _credential_manager


def setup_test_credentials() -> None:
    """Set up test credentials from all available sources."""
    manager = get_credential_manager()
    manager.setup_environment_variables()


def get_credential_status() -> Dict[str, bool]:
    """Get status of all credential types."""
    manager = get_credential_manager()
    return manager.get_credential_status()


def print_credential_status() -> None:
    """Print credential status for debugging."""
    manager = get_credential_manager()
    manager.print_credential_status()


# Set up credentials when module is imported
setup_test_credentials()
