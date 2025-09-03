"""Unified authentication management with fallback support."""

from typing import Optional, List, Dict, Any

from .config import ClusterConfig
from .credential_manager import get_credential_manager
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
        """Offer to store credentials in .env file."""
        hostname = self.config.cluster_host
        username = self.config.username

        if not hostname or not username:
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

        # Check environment variable if enabled
        if self.config.use_env_password:
            env_password = self.config.get_env_password()
            results["env_var_set"] = env_password is not None

            if env_password:
                print(
                    f"   ✅ Environment variable ${self.config.password_env_var} is set"
                )
            else:
                print(
                    f"   ❌ Environment variable ${self.config.password_env_var} not set"
                )

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
