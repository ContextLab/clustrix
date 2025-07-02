"""Authentication method implementations for enhanced cluster access."""

import os
import time
import getpass
import subprocess
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

from .config import ClusterConfig


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

            # Also look for clustrix-specific keys
            hostname = connection_params.get("hostname", "")
            username = connection_params.get("username", "")

            if hostname and username:
                clustrix_patterns = [
                    f"id_ed25519_clustrix_{username}_{hostname}",
                    f"id_rsa_clustrix_{username}_{hostname}",
                ]
                key_patterns = clustrix_patterns + key_patterns

            for pattern in key_patterns:
                key_path = os.path.join(ssh_dir, pattern)
                if os.path.exists(key_path) and os.path.exists(f"{key_path}.pub"):
                    return AuthResult(success=True, method="ssh_key", key_path=key_path)

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


class OnePasswordAuthMethod(AuthMethod):
    """1Password-based authentication."""

    def __init__(self, config: ClusterConfig):
        super().__init__(config)
        self.cache = {}  # In-memory credential cache

    def is_applicable(self, connection_params: Dict[str, Any]) -> bool:
        """Check if 1Password is configured and available."""
        return self.config.use_1password and self.is_available()

    def is_available(self) -> bool:
        """Check if 1Password CLI is available."""
        try:
            result = subprocess.run(["op", "--version"], capture_output=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def attempt_auth(self, connection_params: Dict[str, Any]) -> AuthResult:
        """Attempt to get password from 1Password."""
        hostname = connection_params.get("hostname", "")
        username = connection_params.get("username", "")

        # Check cache first
        if self.config.cache_credentials:
            cache_key = f"{hostname}:{username}"
            if cache_key in self.cache:
                entry = self.cache[cache_key]
                if entry["expires"] > time.time():
                    return AuthResult(
                        success=True, method="onepassword", password=entry["password"]
                    )

        # Get credentials from 1Password
        note_name = self.config.onepassword_note
        if not note_name:
            # Try default patterns
            note_name = f"clustrix-{hostname}"

        try:
            password = self._get_password_from_note(note_name)

            if password:
                # Cache the password
                if self.config.cache_credentials:
                    cache_key = f"{hostname}:{username}"
                    self.cache[cache_key] = {
                        "password": password,
                        "expires": time.time() + self.config.credential_cache_ttl,
                    }

                return AuthResult(success=True, method="onepassword", password=password)
            else:
                return AuthResult(
                    success=False,
                    error=f"No password found in 1Password note '{note_name}'",
                    guidance=(
                        f"Create a secure note in 1Password named '{note_name}' "
                        "with format:\n- password: your_password"
                    ),
                )

        except Exception as e:
            return AuthResult(
                success=False,
                error=f"1Password lookup failed: {e}",
                guidance="Ensure 1Password CLI is installed and you're signed in: op signin",
            )

    def _get_password_from_note(self, note_name: str) -> Optional[str]:
        """Get password from 1Password note."""
        try:
            # Try to get the note
            result = subprocess.run(
                ["op", "item", "get", note_name, "--format=json"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                import json

                note_data = json.loads(result.stdout)

                # Look for password in different formats
                # Check fields first (structured data)
                if "fields" in note_data:
                    for field in note_data["fields"]:
                        if field.get("label", "").lower() == "password":
                            return field.get("value")

                # Check notes section for text format
                if "fields" in note_data:
                    for field in note_data["fields"]:
                        if field.get("type") == "notes" and field.get("value"):
                            return self._extract_password_from_text(field["value"])

                # Check for notesPlain (older format)
                if "notesPlain" in note_data:
                    return self._extract_password_from_text(note_data["notesPlain"])

            return None

        except Exception:
            return None

    def _extract_password_from_text(self, text: str) -> Optional[str]:
        """Extract password from text note format."""
        import re

        # Look for "- password: <password>" format
        match = re.search(r"^- password:\s*(.+)$", text, re.MULTILINE)
        if match:
            return match.group(1).strip()

        # Look for "password: <password>" format
        match = re.search(r"^password:\s*(.+)$", text, re.MULTILINE)
        if match:
            return match.group(1).strip()

        return None

    def store_password(self, hostname: str, username: str, password: str) -> bool:
        """Store password in 1Password."""
        note_name = f"clustrix-{hostname}"

        # Create note content in standard format
        note_content = f"""Clustrix SSH Cluster Credentials

- hostname: {hostname}
- username: {username}
- password: {password}
- created: {datetime.now().isoformat()}

This note was automatically created by Clustrix for secure cluster access.
"""

        try:
            # Check if note already exists
            result = subprocess.run(
                ["op", "item", "get", note_name], capture_output=True, timeout=5
            )

            if result.returncode == 0:
                # Update existing note
                subprocess.run(
                    ["op", "item", "edit", note_name, f"notesPlain={note_content}"],
                    check=True,
                    timeout=10,
                )
            else:
                # Create new secure note
                subprocess.run(
                    [
                        "op",
                        "item",
                        "create",
                        "--category=SecureNote",
                        f"--title={note_name}",
                        f"notesPlain={note_content}",
                    ],
                    check=True,
                    timeout=10,
                )

            return True

        except Exception as e:
            print(f"Failed to store in 1Password: {e}")
            return False


class WidgetPasswordMethod(AuthMethod):
    """Widget password field authentication."""

    def __init__(self, config: ClusterConfig, widget_password: str = None):
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
