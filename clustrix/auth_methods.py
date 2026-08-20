"""Authentication method implementations for enhanced cluster access."""

import os
import getpass
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass

from .config import ClusterConfig, config_source_is_trusted, get_config_source
from .credential_manager import get_credential_manager


@dataclass
class AuthResult:
    """Result of an authentication attempt."""

    success: bool
    method: Optional[str] = None
    password: Optional[str] = None
    key_path: Optional[str] = None
    error: Optional[str] = None
    guidance: Optional[str] = None


class AuthMethod(ABC):
    """Base class for authentication methods."""

    def __init__(self, config: ClusterConfig):
        self.config = config

    @abstractmethod
    def is_applicable(self, connection_params: Dict[str, Any]) -> bool:
        """Check if this auth method is applicable for the given connection."""
        pass

    @abstractmethod
    def attempt_auth(self, connection_params: Dict[str, Any]) -> AuthResult:
        """Attempt authentication using this method."""
        pass

    def is_available(self) -> bool:
        """Check if this auth method is available on the system."""
        return True


class SSHKeyAuthMethod(AuthMethod):
    """SSH key-based authentication."""

    def is_applicable(self, connection_params: Dict[str, Any]) -> bool:
        """SSH keys are always applicable."""
        return True

    def attempt_auth(self, connection_params: Dict[str, Any]) -> AuthResult:
        """Attempt SSH key authentication."""
        try:
            # Look for SSH keys in standard locations
            ssh_dir = os.path.expanduser("~/.ssh")
            key_patterns = ["id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"]

            # Also look for clustrix-specific keys and pattern-based keys
            hostname = connection_params.get("hostname", "")
            username = connection_params.get("username", "")

            if hostname and username:
                clustrix_patterns = [
                    f"id_ed25519_clustrix_{username}_{hostname}",
                    f"id_rsa_clustrix_{username}_{hostname}",
                ]
                key_patterns = clustrix_patterns + key_patterns

            # First try exact pattern matches
            for pattern in key_patterns:
                key_path = os.path.join(ssh_dir, pattern)
                if os.path.exists(key_path) and os.path.exists(f"{key_path}.pub"):
                    return AuthResult(success=True, method="ssh_key", key_path=key_path)

            # If no exact matches, look for keys containing hostname and username
            if hostname and username:
                try:
                    for filename in os.listdir(ssh_dir):
                        if (
                            filename.startswith(("id_ed25519", "id_rsa", "id_ecdsa"))
                            and not filename.endswith(".pub")
                            and username in filename
                            and hostname.split(".")[0] in filename
                        ):  # Match base hostname
                            key_path = os.path.join(ssh_dir, filename)
                            pub_path = f"{key_path}.pub"
                            if os.path.exists(pub_path):
                                return AuthResult(
                                    success=True, method="ssh_key", key_path=key_path
                                )
                except OSError:
                    pass  # Directory listing failed

            return AuthResult(
                success=False,
                error="No SSH keys found",
                guidance="Generate SSH keys with: ssh-keygen -t ed25519 -C 'your_email@example.com'",
            )

        except Exception as e:
            return AuthResult(success=False, error=f"SSH key check failed: {e}")


class EnvironmentPasswordMethod(AuthMethod):
    """Environment variable-based password authentication."""

    def is_applicable(self, connection_params: Dict[str, Any]) -> bool:
        """Check if environment variable password is configured."""
        return self.config.use_env_password and bool(self.config.password_env_var)

    def attempt_auth(self, connection_params: Dict[str, Any]) -> AuthResult:
        """Attempt to get password from environment variable."""
        if not self.config.password_env_var:
            return AuthResult(success=False, error="No environment variable specified")

        password = os.environ.get(self.config.password_env_var)

        if password:
            return AuthResult(success=True, method="environment", password=password)
        else:
            return AuthResult(
                success=False,
                error=f"Environment variable ${self.config.password_env_var} not set",
                guidance=f"Set password with: export {self.config.password_env_var}='your_password'",
            )


def _normalize_hostname(hostname: object) -> str:
    """The comparable form of a hostname, or ``""`` if there isn't one.

    Case is not significant in DNS and a trailing dot only marks a name as
    already absolute, so ``HPC.Example.Edu.`` and ``hpc.example.edu`` are
    the same host and must compare equal. Anything that is not a non-empty
    string -- ``None``, a stray ``0``, whitespace -- normalises to ``""``,
    which :func:`_hostname_matches` then refuses outright.
    """
    if not isinstance(hostname, str):
        return ""
    return hostname.strip().rstrip(".").lower()


def _hostname_matches(target: object, credential_host: object) -> bool:
    """Whether a credential stored for ``credential_host`` is for ``target``.

    **Exact, after normalisation.** Nothing else is safe, and the three
    relaxations this replaces were each exploitable:

    * ``credential_host in target`` -- substring containment. A ``.env``
      holding only ``SSH_PASSWORD`` yields ``credential_host == ""``, and
      the empty string is a substring of every hostname there is, so the
      cluster password was offered to *any* host that was asked for. Even
      with a real value it means a credential for ``hpc.example.edu`` is
      handed to ``hpc.example.edu.attacker.test``, a name anybody can
      register under a domain they control.
    * ``target in credential_host`` -- the same thing backwards.
    * ``target.split(".")[0] == credential_host.split(".")[0]`` -- first
      label only, so ``hpc.evil.test`` collects the password stored for
      ``hpc.example.edu``.

    A hostname is the identity of the party about to receive the secret, so
    a *partial* match is not a weaker check, it is a different check that
    answers a question nobody asked. Nor is suffix-on-a-dot-boundary right
    here: ``hpc.example.edu`` has no authority over ``node1.hpc.example.edu``
    and a credential for the parent is not a credential for the child.

    The cost of being strict is a credential that is simply not offered
    when the user spelled the host differently in ``.env`` than in their
    config -- at which point the fallback chain moves on and prompts, and
    the guidance in :meth:`FlexibleCredentialAuthMethod.attempt_auth` names
    the fix. That is a safe failure. Every relaxation above is an unsafe
    success.
    """
    normalized_target = _normalize_hostname(target)
    normalized_credential = _normalize_hostname(credential_host)
    if not normalized_target or not normalized_credential:
        # A credential that does not say which host it is for cannot be
        # checked against one, and "unchecked" may not read as "matches".
        # This is the same class of defect as the ``{"port": "22"}`` default
        # that made every unconfigured machine look like it had SSH
        # credentials: an absent value must never satisfy a test.
        return False
    return normalized_target == normalized_credential


def stored_credential_is_for_config(
    config: ClusterConfig, credentials: Dict[str, Any]
) -> Optional[str]:
    """Why a stored SSH credential may not be used for ``config``, or ``None``.

    ``FlexibleCredentialAuthMethod`` answers this question for a connection
    the auth chain is driving. ``ConnectionManager.setup_ssh_connection``
    reads ``ensure_credential("ssh")`` directly and used to answer it not at
    all: whatever came out of ``~/.clustrix/.env`` was applied to whatever
    ``config.cluster_host`` said, so the *file* decided who received the
    user's cluster password.

    That is a live exfiltration path rather than a theoretical one, because
    ``config.cluster_host`` is not necessarily the user's. The search of the
    standard locations includes ``./clustrix.yml``, so a repository that
    ships one names the host, and the working-directory candidates normally
    win outright (``~/.clustrix/clustrix.yml`` is not searched -- only
    ``config.yml`` is). ``git clone && cd && python -c "import clustrix..."``
    was enough to have the password sent to a host of the repository's
    choosing.

    Two rules, and the second is the one that keeps the documented setup
    working:

    1. **If the credential names a host, it must be that host.** Exactly,
       after normalisation -- :func:`_hostname_matches`, the same comparison
       and the same reasoning as the auth-chain path. Substring, suffix and
       first-label matches were each exploitable there and are no better
       here.
    2. **If the credential names no host, the host must come from a source
       the user chose.** A bare ``SSH_PASSWORD=...`` in ``.env`` with the
       host in a config file is the documented, supported setup and has to
       keep working, so requiring an ``SSH_HOST`` outright is not available.
       What separates it from the attack is not the credential at all --
       both look identical -- it is *who chose the hostname*. A host from
       ``~/.clustrix/config.yml``, from ``load_config(path)``, or from
       Python is the user's. A host from ``./clustrix.yml`` is whatever
       directory the process is in. See
       :func:`clustrix.config.config_source_is_trusted`.

    Returns the reason it may not be used, so the caller can say so; ``None``
    means it may.
    """
    credential_host = credentials.get("host", "")
    if _normalize_hostname(credential_host):
        if _hostname_matches(config.cluster_host, credential_host):
            return None
        return (
            f"the stored credential is for {credential_host!r} and this "
            f"connection is to {config.cluster_host!r}"
        )

    if config_source_is_trusted(config):
        return None

    return (
        f"the stored credential names no host, and cluster_host="
        f"{config.cluster_host!r} came from {get_config_source(config)} "
        f"(a ./clustrix.yml is chosen by the directory the process runs in, "
        f"not by you). Set SSH_HOST in the credential file, or put the host "
        f"somewhere you chose: the clustrix configuration directory, "
        f"load_config(path), or configure(cluster_host=...)"
    )


class FlexibleCredentialAuthMethod(AuthMethod):
    """Flexible credential authentication using the new credential manager."""

    def __init__(self, config: ClusterConfig):
        super().__init__(config)
        self.credential_manager = get_credential_manager()

    def is_applicable(self, connection_params: Dict[str, Any]) -> bool:
        """Always applicable as the new primary credential source."""
        return True

    def is_available(self) -> bool:
        """Always available as it's the new default system."""
        return True

    def attempt_auth(self, connection_params: Dict[str, Any]) -> AuthResult:
        """Hand over a stored credential only if it is stored for *this* host.

        A credential is released when the stored host and the requested
        host are the same host (:func:`_hostname_matches`) *and* the stored
        username and the requested username are the same non-empty
        username. Both halves have to name something: a stored credential
        with no username used to match a connection with no username,
        because ``"" == ""``, which is the same "absent satisfies the test"
        defect as the hostname case.
        """
        hostname = connection_params.get("hostname", "")
        username = connection_params.get("username", "")

        # Try SSH credentials first (most common for clusters)
        ssh_creds = self.credential_manager.ensure_credential("ssh")
        if ssh_creds:
            # Check if the SSH credentials match this connection
            cred_host = ssh_creds.get("host", "")
            cred_username = ssh_creds.get("username", "")

            host_match = _hostname_matches(hostname, cred_host)

            username_match = bool(username) and username == cred_username

            if host_match and username_match:
                # Return password if available
                if "password" in ssh_creds:
                    return AuthResult(
                        success=True,
                        method="flexible_credential",
                        password=ssh_creds["password"],
                    )
                # Return SSH key path if available
                elif "private_key_path" in ssh_creds:
                    return AuthResult(
                        success=True,
                        method="flexible_credential_key",
                        key_path=ssh_creds["private_key_path"],
                    )

        # Fallback: return no credentials found (let other methods try)
        return AuthResult(
            success=False,
            error="No matching SSH credentials found in credential manager",
            guidance=(
                "Add SSH credentials using 'clustrix credentials setup' or edit "
                "~/.clustrix/.env. A stored credential is only offered to the "
                "host it names, so SSH_HOST and SSH_USERNAME must both be set "
                "and must match "
                f"{username or '<username>'}@{hostname or '<hostname>'} exactly."
            ),
        )


class WidgetPasswordMethod(AuthMethod):
    """Widget password field authentication."""

    def __init__(self, config: ClusterConfig, widget_password: Optional[str] = None):
        super().__init__(config)
        self.widget_password = widget_password

    def set_password(self, password: str):
        """Set password from widget."""
        self.widget_password = password

    def is_applicable(self, connection_params: Dict[str, Any]) -> bool:
        """Check if widget password is available."""
        return self.widget_password is not None

    def attempt_auth(self, connection_params: Dict[str, Any]) -> AuthResult:
        """Use password from widget."""
        if self.widget_password:
            password = self.widget_password
            self.widget_password = None  # Clear after use for security

            return AuthResult(success=True, method="widget", password=password)

        return AuthResult(success=False, error="No widget password available")


def detect_environment() -> str:
    """Detect the current execution environment."""
    try:
        # Check for Google Colab
        import google.colab  # noqa: F401

        return "colab"
    except ImportError:
        pass

    try:
        # Check for Jupyter notebook
        from IPython import get_ipython

        if get_ipython() is not None:
            if get_ipython().__class__.__name__ == "ZMQInteractiveShell":
                return "notebook"
    except ImportError:
        pass

    # Check if running in a terminal
    if os.isatty(0):
        return "cli"
    else:
        return "script"


def is_colab() -> bool:
    """Check if running in Google Colab."""
    try:
        import google.colab  # noqa: F401

        return True
    except ImportError:
        return False


class InteractivePasswordMethod(AuthMethod):
    """Interactive password prompting with environment detection."""

    def is_applicable(self, connection_params: Dict[str, Any]) -> bool:
        """Interactive prompting is always applicable as last resort."""
        return True

    def attempt_auth(self, connection_params: Dict[str, Any]) -> AuthResult:
        """Prompt for password based on environment."""
        env_type = detect_environment()
        hostname = connection_params.get("hostname", "cluster")
        username = connection_params.get("username", "user")

        prompt = f"Password for {username}@{hostname}: "

        try:
            if env_type == "notebook" and not is_colab():
                # Use GUI popup for notebooks
                password = self._gui_password_prompt(prompt)
            elif env_type in ["cli", "script"]:
                # Use getpass for CLI
                password = getpass.getpass(prompt)
            elif env_type == "colab":
                # Google Colab special handling
                try:
                    from google.colab import auth

                    password = auth.getpass(prompt)
                except ImportError:
                    password = getpass.getpass(prompt)
            else:
                # Fallback to basic input
                password = getpass.getpass(prompt)

            if password:
                return AuthResult(success=True, method="interactive", password=password)
            else:
                return AuthResult(success=False, error="No password provided")

        except KeyboardInterrupt:
            return AuthResult(success=False, error="Password entry cancelled by user")
        except Exception as e:
            return AuthResult(
                success=False, error=f"Interactive password prompt failed: {e}"
            )

    def _gui_password_prompt(self, prompt: str) -> Optional[str]:
        """Show GUI password dialog."""
        try:
            import tkinter as tk
            from tkinter import simpledialog

            root = tk.Tk()
            root.withdraw()  # Hide main window

            # Create custom password dialog
            password = simpledialog.askstring(
                "Cluster Authentication", prompt, show="*"  # Mask password input
            )

            root.destroy()
            return password

        except Exception as e:
            # Fallback to terminal if GUI fails
            print(f"GUI unavailable ({e}), falling back to terminal input")
            return getpass.getpass(prompt)
