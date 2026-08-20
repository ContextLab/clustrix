"""Unified authentication management with fallback support."""

from typing import Optional, List, Dict, Any

from .config import TRUSTED_CONFIG_SOURCES, ClusterConfig
from .credential_manager import get_credential_manager
from .credential_release import CredentialTarget, release_credential
from .auth_methods import (
    AuthMethod,
    AuthResult,
    SSHKeyAuthMethod,
    EnvironmentPasswordMethod,
    FlexibleCredentialAuthMethod,
    WidgetPasswordMethod,
    InteractivePasswordMethod,
    detect_environment,
    is_colab,
)


class AuthenticationManager:
    """Unified authentication management with configurable fallback chain."""

    def __init__(self, config: ClusterConfig):
        self.config = config
        self.widget_password_method = WidgetPasswordMethod(config)
        self.auth_methods = self._initialize_auth_methods()

    def set_widget_password(self, password: str):
        """Set password from widget interface."""
        self.widget_password_method.set_password(password)

    def get_password_for_setup(self) -> Optional[str]:
        """
        Get password for SSH key setup using configured methods.

        Returns:
            Password string if found, None if not available
        """
        print("🔐 Attempting to obtain password for SSH setup...")

        # Try methods in order based on configuration
        connection_params = {
            "hostname": self.config.cluster_host,
            "username": self.config.username,
            "port": self.config.ssh_port,
        }

        # 1. Check widget password first (immediate use)
        if self.widget_password_method.is_applicable(connection_params):
            print("   • Checking widget password field...")
            result = self.widget_password_method.attempt_auth(connection_params)
            if result.success:
                print("   ✅ Using password from widget")
                return result.password

        # 2. Try FlexibleCredentialManager (NEW PRIMARY METHOD)
        print("   • Checking flexible credential manager...")
        flexible_method = FlexibleCredentialAuthMethod(self.config)
        if flexible_method.is_applicable(connection_params):
            result = flexible_method.attempt_auth(connection_params)
            if result.success:
                print("   ✅ Retrieved password from credential manager")
                return result.password
            else:
                print(f"   ⚠️  Credential manager lookup failed: {result.error}")

        # 3. Try environment variable if configured
        if self.config.use_env_password:
            print(
                f"   • Checking environment variable ${self.config.password_env_var}..."
            )
            env_method = EnvironmentPasswordMethod(self.config)
            if env_method.is_applicable(connection_params):
                result = env_method.attempt_auth(connection_params)
                if result.success:
                    print(f"   ✅ Using password from ${self.config.password_env_var}")
                    return result.password
                else:
                    print(f"   ⚠️  Environment variable not set: {result.error}")

        # 4. Fall back to interactive prompt
        print("   • Prompting for password...")
        interactive_method = InteractivePasswordMethod(self.config)
        result = interactive_method.attempt_auth(connection_params)

        if result.success:
            print("   ✅ Password entered interactively")

            # Offer to store in .env file
            if result.password:
                self._offer_credential_storage(result.password)

            return result.password
        else:
            print(f"   ❌ Interactive prompt failed: {result.error}")
            return None

    def authenticate(self, connection_params: Dict[str, Any]) -> AuthResult:
        """
        Try authentication methods in priority order.

        Args:
            connection_params: Connection parameters for authentication

        Returns:
            AuthResult with success status and method used
        """
        print(f"🔐 Authenticating to {connection_params.get('hostname', 'cluster')}...")

        for method in self.auth_methods:
            if method.is_applicable(connection_params):
                method_name = method.__class__.__name__.replace(
                    "AuthMethod", ""
                ).lower()
                print(f"   • Trying {method_name}...")

                result = method.attempt_auth(connection_params)
                if result.success:
                    print(f"   ✅ {method_name} authentication successful")

                    return result
                else:
                    print(f"   ⚠️  {method_name} failed: {result.error}")

        return AuthResult(success=False, error="All authentication methods failed")

    def _initialize_auth_methods(self) -> List[AuthMethod]:
        """Initialize authentication methods with FlexibleCredentialAuthMethod as primary."""
        methods: List[AuthMethod] = [
            SSHKeyAuthMethod(self.config),
            FlexibleCredentialAuthMethod(self.config),  # NEW: Primary credential source
        ]

        # Add environment variable if configured
        if self.config.use_env_password:
            methods.append(EnvironmentPasswordMethod(self.config))

        # Add widget password method
        methods.append(self.widget_password_method)

        # Add interactive prompt as last resort
        methods.append(InteractivePasswordMethod(self.config))

        return methods

    def _offer_credential_storage(self, password: str):
        """Offer to store credentials in .env file -- for a host you chose.

        Route 7 of issue #167, and the only one on the *write* side. This
        offered to write ``SSH_HOST=<whatever cluster_host says>`` plus the
        password the user had just typed into ``~/.clustrix/.env``. The user
        is shown the hostname first, so it was never a silent leak -- but a
        ``./clustrix.yml`` names ``cluster_host``, and a credential file
        naming a host *exactly* is rule 1 of the release rules: it is
        released unconditionally, in every future process, forever. The
        taint model is per-process and append-only; this route wrote around
        it, onto disk, into the one file every remedy text tells the user to
        trust.

        So a write is a release decision too, and it is refused for a host
        nobody chose. The remedy is the one that actually works: move the
        host somewhere you chose, and run this again.
        """
        hostname = self.config.cluster_host
        username = self.config.username

        if not hostname or not username:
            return

        try:
            target = CredentialTarget.for_config(self.config)
        except ValueError as exc:
            print(f"   ⚠️  Not storing the credential: {exc}")
            return

        if target.provenance not in TRUSTED_CONFIG_SOURCES:
            print(
                f"   ⚠️  Not storing these credentials in ~/.clustrix/.env: "
                f"cluster_host={hostname!r} came from {target.provenance} -- "
                f"a file chosen by where this process runs or by an "
                f"inherited environment variable, not by you. Writing "
                f"SSH_HOST={hostname!r} there would authorise that host "
                f"permanently, in every future process, which is a stronger "
                f"statement than the one you just made by typing a password "
                f"once. Move the host into the clustrix configuration "
                f"directory (config.yml), remove the file it came from, "
                f"start a new process, and this offer will be made again."
            )
            return

        # Offer to store in .env file
        if self._should_store_in_env_file():
            self._store_in_env_file(password, hostname, username)

    def _should_store_in_env_file(self) -> bool:
        """Prompt user to store credentials in .env file."""
        env_type = detect_environment()

        if env_type == "notebook" and not is_colab():
            # Use GUI dialog
            try:
                import tkinter as tk
                from tkinter import messagebox

                root = tk.Tk()
                root.withdraw()
                result = messagebox.askyesno(
                    "Store in ~/.clustrix/.env?",
                    f"Would you like to store credentials for "
                    f"{self.config.username}@{self.config.cluster_host} "
                    f"in ~/.clustrix/.env for future use?\n\n"
                    f"This will enable automatic authentication without prompts.",
                )
                root.destroy()
                return result
            except Exception:
                # Fall back to terminal
                pass

        if env_type in ["cli", "script"] or env_type == "notebook":
            # Use terminal prompt
            try:
                response = input(
                    f"\nStore credentials for {self.config.cluster_host} in ~/.clustrix/.env for future use? [y/N]: "
                )
                return response.lower() in ["y", "yes"]
            except KeyboardInterrupt:
                return False

        return False

    def _store_in_env_file(self, password: str, hostname: str, username: str) -> bool:
        """Store credentials in .env file using credential manager."""
        try:
            credential_manager = get_credential_manager()

            # Prepare SSH credentials
            ssh_credentials = {
                "SSH_HOST": hostname,
                "SSH_USERNAME": username,
                "SSH_PASSWORD": password,
                "SSH_PORT": str(self.config.ssh_port or 22),
            }

            # Use the credential manager's file writing function
            from .cli_credentials import _write_credentials_to_env_file

            success = _write_credentials_to_env_file(
                credential_manager.env_file, ssh_credentials
            )

            if success:
                print("✅ Credentials stored in ~/.clustrix/.env")
                print("   You can now use passwordless authentication!")
                return True
            else:
                print("❌ Failed to store credentials in .env file")
                return False

        except Exception as e:
            print(f"❌ Error storing credentials in .env file: {e}")
            return False

    def validate_configuration(self) -> Dict[str, Optional[bool]]:
        """Validate the current authentication configuration."""
        results: Dict[str, Optional[bool]] = {}

        print("🔍 Validating authentication configuration...")

        # Check environment variable if enabled.
        #
        # "Is it set" is not the question the user needs answered -- an
        # environment password that is set but would never be released to
        # this ``cluster_host`` is not a working configuration, and reporting
        # it as one is how route 6 stayed invisible. So this asks the gate
        # the same question the connection path asks.
        if self.config.use_env_password:
            try:
                target = CredentialTarget.for_config(self.config)
            except ValueError as exc:
                print(f"   ❌ {exc}")
                results["env_var_set"] = False
            else:
                release = release_credential(
                    target,
                    provider="ssh",
                    config=self.config,
                    sources=("environment",),
                )
                results["env_var_set"] = bool(release)
                if release:
                    print(
                        f"   ✅ Environment variable "
                        f"${self.config.password_env_var} is set"
                    )
                else:
                    print(f"   ❌ {release.refusal}")

        # Check SSH keys
        ssh_method = SSHKeyAuthMethod(self.config)
        connection_params = {
            "hostname": self.config.cluster_host,
            "username": self.config.username,
        }
        ssh_result = ssh_method.attempt_auth(connection_params)
        results["ssh_keys_available"] = ssh_result.success

        if ssh_result.success:
            print(f"   ✅ SSH keys found: {ssh_result.key_path}")
        else:
            print("   ⚠️  No SSH keys found")

        return results
