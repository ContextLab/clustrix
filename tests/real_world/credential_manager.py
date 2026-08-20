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
from typing import Dict, Optional
from pathlib import Path

# Only used by the require_* helpers below, which skip rather than fail when a
# target is unconfigured. This module lives under tests/, so pytest is always
# installed alongside it.
import pytest

# The supported credential path. Guarded so that this module still imports
# when clustrix itself cannot be; HAS_SECURE_CREDENTIALS gates every use.
try:
    from clustrix.config import CONFIG_SOURCE_RUNTIME, get_config_dir
    from clustrix.credential_release import (
        CredentialTarget,
        describe_credential,
        release_credential,
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
#: TEST_SSH_USERNAME names, which name a single SSH target rather than a role.
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


def env_file_path() -> Optional[Path]:
    """Path of the credential file clustrix reads, or None if it cannot say.

    The location follows CLUSTRIX_CONFIG_DIR, so this asks clustrix rather
    than rebuilding `~/.clustrix/.env` here.
    """
    if not HAS_SECURE_CREDENTIALS:
        return None
    try:
        return get_config_dir() / ".env"
    except Exception as e:  # pragma: no cover - Path.home() with no home dir
        logger.debug(f"could not locate the clustrix config directory: {e}")
        return None


def unreadable_env_file() -> Optional[Path]:
    """The credential file that exists but cannot be read, if that is the case.

    `chmod 000 ~/.clustrix/.env` used to be indistinguishable from having no
    .env at all: the read fails inside clustrix, is logged at debug level, and
    every skip reason then says "no credentials configured" -- sending the
    developer off to re-enter credentials that are already on disk.
    """
    path = env_file_path()
    if path is None:
        return None
    try:
        if path.is_file() and not os.access(path, os.R_OK):
            return path
    except OSError as e:  # pragma: no cover - unreadable parent directory
        logger.debug(f"could not stat {path}: {e}")
    return None


def credential_setup_hint() -> str:
    """What to tell the developer about credentials, given this machine.

    Same text as :data:`CREDENTIAL_SETUP_HINT` unless the .env file is present
    and unreadable, which is a different problem with a different fix.
    """
    unreadable = unreadable_env_file()
    if unreadable is not None:
        return (
            f"{unreadable} exists but cannot be read (permission denied), so "
            f"the credentials in it were not loaded: run `chmod 600 "
            f"{unreadable}` to fix it"
        )
    return CREDENTIAL_SETUP_HINT


def _clustrix_credentials(
    provider: str, hostname: Optional[str] = None
) -> Dict[str, str]:
    """Credentials for `provider` from ~/.clustrix/.env or the environment.

    This is the only supported source; an empty dict means "none configured",
    never "substitute something plausible".

    A secret only comes out of clustrix through
    `clustrix.credential_release.release_credential`, which requires the host
    about to receive it -- so this names one. For SSH that is the host the
    developer's own test configuration points at (`CLUSTRIX_TEST_*_HOST`) or,
    failing that, the `SSH_HOST` in the credential file; both are the
    developer naming a target on their own machine, which is `runtime`. For
    HuggingFace it is `huggingface.co`, which nothing can configure.

    The environment is snapshotted and restored around the lookup: a
    credential belongs to the caller that asked for it, never to os.environ.
    The lookup itself no longer exports anything, but this module is the
    place a re-introduced export would do the most damage, so the guard
    stays -- and it is cheap.
    """
    if not HAS_SECURE_CREDENTIALS:
        return {}
    environment = dict(os.environ)
    try:
        described = describe_credential(provider)
        if not described.available:
            return {}
        if provider == "huggingface":
            target = CredentialTarget.fixed_service(
                "huggingface.co", why="the HuggingFace Hub API"
            )
        else:
            host = _nonempty(hostname) or _nonempty(described.host)
            if not host:
                return {}
            target = CredentialTarget(
                hostname=host,
                username=described.username,
                provenance=CONFIG_SOURCE_RUNTIME,
                described_as=f"{host}, named by this machine's test configuration",
            )
        release = release_credential(target, provider=provider)
        if release.refusal is not None:
            logger.warning(
                "clustrix %s credential was not released: %s",
                provider,
                release.refusal,
            )
            return {}
        resolved: Dict[str, str] = {}
        for key, value in (
            ("host", described.host),
            ("username", described.username),
            ("port", described.port),
            ("password", release.password),
            ("private_key_path", release.key_path),
            ("token", release.token),
        ):
            if value:
                resolved[key] = value
        return resolved
    except Exception as e:  # a broken .env must not abort collection
        logger.warning(f"clustrix {provider} credential lookup failed: {e}")
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


def _usable_key_path(value: Optional[str]) -> Optional[str]:
    """`value` if it names a key file that exists, else None.

    A SSH_PRIVATE_KEY_PATH pointing at a file that is not there is not a
    credential: paramiko raises on open, several seconds into a connection,
    and the failure reads like a cluster problem. Treat it as missing so the
    caller skips with the setup hint instead.
    """
    path = _nonempty(value)
    if path is None:
        return None
    expanded = Path(path).expanduser()
    if not expanded.is_file():
        logger.warning(
            "ignoring SSH private key %s: no such file (set SSH_PRIVATE_KEY_PATH "
            "to a key that exists, or use a password)",
            path,
        )
        return None
    return str(expanded)


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
    ssh = _clustrix_credentials("ssh", hostname=get_test_host(role))

    host = get_test_host(role) or _nonempty(ssh.get("host"))
    username = (
        get_test_username()
        or _nonempty(ssh.get("username"))
        or _nonempty(os.environ.get("CLUSTRIX_USERNAME"))
    )
    password = _nonempty(ssh.get("password")) or _nonempty(
        os.environ.get("CLUSTRIX_PASSWORD")
    )
    private_key_path = _usable_key_path(ssh.get("private_key_path"))

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
            f"No credentials for the {role} test cluster. {credential_setup_hint()}"
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

    def get_ssh_credentials(self) -> Optional[Dict[str, str]]:
        """Get SSH credentials, or None when no SSH target is configured.

        None means "nothing configured". It used to mean nothing at all: the
        fallback below defaulted the host to "localhost" and the account to
        $USER, so this returned a truthy dictionary on a machine with no test
        cluster whatsoever. Every `if not ssh_creds: pytest.skip(...)` gate
        was therefore dead, and the tests behind them pointed clustrix at the
        developer's own laptop over SSH instead of skipping.
        """
        # ~/.clustrix/.env and the environment, via clustrix itself.
        configured = get_cluster_credentials("ssh")
        if configured:
            return configured

        # No separate GitHub Actions branch: get_cluster_credentials already
        # reads CLUSTRIX_USERNAME / CLUSTRIX_PASSWORD alongside the .env file,
        # so a branch here could never be reached.

        # Explicitly exported TEST_SSH_* variables, for a target that is not
        # in the .env file. All three parts must be present: a host with no
        # secret only produces an authentication failure later on.
        host = _nonempty(os.getenv("TEST_SSH_HOST"))
        username = _nonempty(os.getenv("TEST_SSH_USERNAME"))
        password = _nonempty(os.getenv("TEST_SSH_PASSWORD"))
        private_key_path = _usable_key_path(os.getenv("TEST_SSH_PRIVATE_KEY_PATH"))

        if not host or not username or not (password or private_key_path):
            return None

        return {
            "host": host,
            "username": username,
            "password": password,
            "private_key_path": private_key_path,
            "port": os.getenv("TEST_SSH_PORT", "22"),
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

        # Explicitly exported TEST_SLURM_* variables. The host used to default
        # to "localhost" and the account to $USER, which pointed the SLURM
        # tests at the developer's own machine as soon as a password was
        # readable from anywhere.
        host = _nonempty(os.getenv("TEST_SLURM_HOST"))
        username = _nonempty(os.getenv("TEST_SLURM_USERNAME"))
        password = _nonempty(os.getenv("TEST_SLURM_PASSWORD"))

        if host and username and password:
            return {
                "host": host,
                "username": username,
                "password": password,
                "port": "22",
            }

        return None

    def get_huggingface_credentials(self) -> Optional[Dict[str, str]]:
        """Get HuggingFace credentials from available sources."""
        # ~/.clustrix/.env and the environment, via clustrix itself. This used
        # to read exported variables only, because the import-time export
        # below would have republished a .env token into every unrelated
        # test's environment; with that export gone, the token can be read
        # where the setup instructions tell people to put it.
        token = _nonempty(_clustrix_credentials("huggingface").get("token"))
        if token:
            return {
                "token": token,
                "username": _nonempty(os.environ.get("HUGGINGFACE_USERNAME"))
                or _nonempty(os.environ.get("HF_USERNAME")),
            }

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
            print(f"  ℹ️  {credential_setup_hint()}")


# Global credential manager instance
_credential_manager = None


def get_credential_manager() -> RealWorldCredentialManager:
    """Get the global credential manager instance."""
    global _credential_manager
    if _credential_manager is None:
        _credential_manager = RealWorldCredentialManager()
    return _credential_manager


def get_credential_status() -> Dict[str, bool]:
    """Get status of all credential types."""
    manager = get_credential_manager()
    return manager.get_credential_status()


def print_credential_status() -> None:
    """Print credential status for debugging."""
    manager = get_credential_manager()
    manager.print_credential_status()


# Importing this module deliberately has no effect on os.environ.
#
# There used to be a `setup_test_credentials()` call here, which resolved
# every credential and exported the results as TEST_SSH_PASSWORD,
# TEST_SLURM_PASSWORD, HUGGINGFACE_TOKEN and friends. Since #153 wired
# ~/.clustrix/.env into that lookup, the exported values were the developer's
# real cluster password -- published into the environment of the whole pytest
# process and every subprocess it spawns, on an ordinary `pytest tests/` run
# (tests/unit/test_cluster_network_detection.py imports this module).
#
# Nothing consumed those exports: the only readers of TEST_SSH_HOST treat it
# as a variable the *developer* exported, and no reader of TEST_SSH_PASSWORD
# exists at all. A credential is returned to the caller that asked for it;
# tests/unit/test_real_world_credentials_are_not_exported.py holds that line.
