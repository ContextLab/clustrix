"""Authentication method implementations for enhanced cluster access."""

import os
import getpass
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass

from .config import ClusterConfig

# ``_hostname_matches`` and ``stored_credential_is_for_config`` moved to
# ``clustrix.credential_release`` unchanged -- same names, same docstrings,
# same behaviour -- because the decision they encode now has one home rather
# than four call sites. Re-exported here so that every importer of the names
# keeps working and there is still exactly one definition of each.
from .credential_release import (  # noqa: F401
    CredentialTarget,
    _hostname_matches,
    describe_stored_credential,
    release_credential,
    stored_credential_is_for_config,
)


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
    """Environment variable-based password authentication.

    Gated by the same rule as every other credential source here, because
    it was the one that had no gate at all: it read
    ``os.environ[config.password_env_var]`` and handed it to whoever asked,
    with no check on ``cluster_host`` and no check on where that host came
    from. Nothing in-tree connects with its result today -- only
    ``AuthenticationManager`` reaches it -- so this was a hole waiting for a
    caller rather than a live leak, which is exactly the moment to close it:
    verified by asking ``AuthenticationManager`` to authenticate against a
    host from a ``./clustrix.yml``, which used to get the secret back.

    Note that with a working-directory config the *whole* method is the
    attacker's: the file names ``password_env_var`` as well as
    ``cluster_host``, so an ungated version reads an environment variable of
    the repository's choosing and sends it to a host of the repository's
    choosing.
    """

    def is_applicable(self, connection_params: Dict[str, Any]) -> bool:
        """Check if environment variable password is configured."""
        return self.config.use_env_password and bool(self.config.password_env_var)

    def attempt_auth(self, connection_params: Dict[str, Any]) -> AuthResult:
        """Ask the gate for the environment branch, and report what it said.

        The two checks that used to live here -- rule 2 of
        ``stored_credential_is_for_config`` and an exact match against
        ``config.cluster_host`` -- are now inside
        :func:`clustrix.credential_release.release_credential`, reached with
        a target that names the recipient. This method is what is left of
        it: build the target, ask, translate.
        """
        if not self.config.password_env_var:
            return AuthResult(success=False, error="No environment variable specified")

        try:
            target = _target_for(self.config, connection_params)
        except ValueError as exc:
            return AuthResult(success=False, error=str(exc))

        release = release_credential(
            target, provider="ssh", config=self.config, sources=("environment",)
        )
        if release.refusal is not None:
            return AuthResult(
                success=False,
                error=f"${self.config.password_env_var}: {release.refusal}",
                guidance=(
                    f"Set password with: export "
                    f"{self.config.password_env_var}='your_password', and set "
                    f"cluster_host to the host you are connecting to."
                ),
            )
        return AuthResult(success=True, method="environment", password=release.password)


def _target_for(
    config: ClusterConfig, connection_params: Dict[str, Any]
) -> CredentialTarget:
    """The recipient of the connection ``connection_params`` describes.

    The auth chain is driving a connection that need not be
    ``config.cluster_host`` at all, so the target names what is actually
    being connected to -- and falls back to the config when the caller gave
    nothing, which is what ``AuthenticationManager`` does for SSH key setup.
    """
    hostname = connection_params.get("hostname") or None
    username = connection_params.get("username")
    return CredentialTarget.for_config(config, hostname=hostname, username=username)


class FlexibleCredentialAuthMethod(AuthMethod):
    """Flexible credential authentication using the new credential manager."""

    def is_applicable(self, connection_params: Dict[str, Any]) -> bool:
        """Always applicable as the new primary credential source."""
        return True

    def is_available(self) -> bool:
        """Always available as it's the new default system."""
        return True

    def attempt_auth(self, connection_params: Dict[str, Any]) -> AuthResult:
        """Hand over a stored credential only if it is stored for *this* host.

        The trust decision -- may a secret go to this host, given who chose
        it -- belongs to
        :func:`clustrix.credential_release.release_credential` and is made
        there. What stays here is the auth chain's *applicability* test: of
        the credentials that may be released, is this one the credential for
        this connection? It answers yes only when the stored credential
        itself names both the host and the username, both non-empty and both
        equal after normalisation. A stored credential with no username used
        to match a connection with no username, because ``"" == ""``, which
        is the same "absent satisfies the test" defect as the hostname case.

        This filter can only *refuse* something the gate allowed; it can
        never release something the gate refused, so it is not a second
        trust decision with a second way to be wrong. The comparison it uses
        is the gate's own :func:`_hostname_matches`.
        """
        hostname = connection_params.get("hostname", "")
        username = connection_params.get("username", "")

        try:
            target = _target_for(self.config, connection_params)
        except ValueError as exc:
            return AuthResult(success=False, error=str(exc))

        release = release_credential(
            target,
            provider="ssh",
            config=self.config,
            sources=("stored-credential",),
        )
        if release.refusal is None:
            stored = describe_stored_credential("ssh")
            host_match = _hostname_matches(hostname, stored.get("host", ""))
            username_match = bool(username) and username == stored.get("username", "")
            if host_match and username_match:
                if release.password:
                    return AuthResult(
                        success=True,
                        method="flexible_credential",
                        password=release.password,
                    )
                if release.key_path:
                    return AuthResult(
                        success=True,
                        method="flexible_credential_key",
                        key_path=release.key_path,
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
