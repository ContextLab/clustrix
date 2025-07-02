"""
Authentication fallback methods for cluster access.

This module provides password fallback functionality when SSH key authentication fails,
with environment-specific handling for different execution contexts.
"""

import os
import sys
import getpass
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


def detect_environment() -> str:
    """
    Detect current execution environment.

    Returns:
        'colab', 'notebook', 'cli', or 'script'
    """
    if "google.colab" in sys.modules:
        return "colab"
    elif "ipykernel" in sys.modules:
        return "notebook"
    elif sys.stdin.isatty():
        return "cli"
    else:
        return "script"


def get_password_gui(prompt: str) -> Optional[str]:
    """
    Get password via GUI popup in notebook environment.

    Args:
        prompt: Password prompt message

    Returns:
        Password string or None if cancelled
    """
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk.Tk()
        root.withdraw()  # Hide main window
        root.attributes("-topmost", True)  # Bring to front

        password = simpledialog.askstring("Cluster Authentication", prompt, show="*")
        root.destroy()
        return password

    except ImportError:
        logger.debug("tkinter not available, trying ipywidgets fallback")
        return get_password_widget(prompt)


def get_password_widget(prompt: str) -> Optional[str]:
    """
    Get password via ipywidgets in notebook environment.

    Args:
        prompt: Password prompt message

    Returns:
        Password string or None if not available
    """
    try:
        import ipywidgets as widgets
        from IPython.display import display, clear_output

        password_widget = widgets.Password(
            description="Password:",
            placeholder="Enter cluster password",
            style={"description_width": "initial"},
        )

        submit_button = widgets.Button(description="Submit", button_style="primary")

        output_widget = widgets.Output()

        result = {"password": None}

        def on_submit(button):
            with output_widget:
                clear_output()
                result["password"] = password_widget.value
                print("✅ Password received")

        submit_button.on_click(on_submit)

        display(widgets.HTML(f"<b>{prompt}</b>"))
        display(widgets.VBox([password_widget, submit_button, output_widget]))

        # Wait for user input (this is a simplified version)
        # In practice, this would need proper async handling
        return result["password"]

    except ImportError:
        logger.debug("ipywidgets not available")
        return None


def get_cluster_password(hostname: str, username: str) -> Optional[str]:
    """
    Get cluster password with environment-specific fallbacks.

    Args:
        hostname: Cluster hostname
        username: Username for authentication

    Returns:
        Password string or None if not available
    """
    env = detect_environment()

    # 1. Colab environment - use secrets
    if env == "colab":
        try:
            from google.colab import userdata

            # Try multiple key formats
            key_variants = [
                f"CLUSTER_PASSWORD_{hostname}",
                f'CLUSTRIX_PASSWORD_{hostname.upper().replace(".", "_")}',
                f'{hostname.upper().replace(".", "_")}_PASSWORD',
                "CLUSTER_PASSWORD",  # Generic fallback
            ]

            for key in key_variants:
                try:
                    password = userdata.get(key)
                    if password:
                        logger.info(f"Retrieved password from Colab secrets: {key}")
                        return password
                except Exception:
                    continue

        except ImportError:
            logger.debug("google.colab not available")

    # 2. Local environment - check environment variables
    env_vars = [
        f'CLUSTRIX_PASSWORD_{hostname.upper().replace(".", "_")}',
        f'CLUSTER_PASSWORD_{hostname.upper().replace(".", "_")}',
        f'{hostname.upper().replace(".", "_")}_PASSWORD',
        "CLUSTRIX_DEFAULT_PASSWORD",
        "CLUSTER_PASSWORD",
    ]

    for var in env_vars:
        password = os.getenv(var)
        if password:
            logger.info(f"Retrieved password from environment variable: {var}")
            return password

    # 3. Interactive fallbacks based on environment
    prompt = f"Password for {username}@{hostname}"

    if env == "notebook":
        # GUI popup for notebook environments
        logger.info("Attempting GUI password prompt for notebook environment")
        password = get_password_gui(prompt)
        if password:
            return password

        # Fallback to widget if GUI fails
        password = get_password_widget(prompt)
        if password:
            return password

    elif env == "cli":
        # Terminal input for CLI
        logger.info("Using terminal password prompt for CLI environment")
        try:
            return getpass.getpass(f"{prompt}: ")
        except (KeyboardInterrupt, EOFError):
            logger.info("Password prompt cancelled by user")
            return None

    elif env == "script":
        # Python script fallback
        logger.info("Using input prompt for script environment")
        try:
            return input(f"{prompt}: ")
        except (KeyboardInterrupt, EOFError):
            logger.info("Password prompt cancelled by user")
            return None

    logger.warning("No password retrieval method available for current environment")
    return None


def requires_password_fallback(auth_result: Dict[str, Any]) -> bool:
    """
    Check if password fallback should be used based on SSH key setup result.

    Args:
        auth_result: Result from SSH key setup attempt

    Returns:
        True if password fallback should be attempted
    """
    if not auth_result.get("success", False):
        return True

    if not auth_result.get("connection_tested", False):
        return True

    # Check for specific error conditions that suggest password auth might work
    error = auth_result.get("error", "")
    if any(
        keyword in error.lower()
        for keyword in ["publickey", "key", "authentication", "gssapi", "kerberos"]
    ):
        return True

    return False


def setup_auth_with_fallback(config, setup_ssh_keys_func, **kwargs) -> Dict[str, Any]:
    """
    Attempt SSH key setup with password fallback.

    Args:
        config: ClusterConfig object
        setup_ssh_keys_func: SSH key setup function to call
        **kwargs: Additional arguments for SSH key setup

    Returns:
        Authentication result with fallback information
    """

    # Try SSH key setup first
    if "password" in kwargs and kwargs["password"]:
        logger.info("Attempting SSH key setup with provided password")
        result = setup_ssh_keys_func(config, **kwargs)

        if result.get("success") and result.get("connection_tested"):
            logger.info("SSH key setup successful")
            return result

    # If SSH key setup failed or no password provided, try password fallback
    logger.info("SSH key setup failed or incomplete, attempting password fallback")

    fallback_password = get_cluster_password(config.cluster_host, config.username)

    if fallback_password:
        logger.info("Password retrieved via fallback method, retrying SSH key setup")
        kwargs["password"] = fallback_password

        try:
            result = setup_ssh_keys_func(config, **kwargs)
            result["details"] = result.get("details", {})
            result["details"]["used_password_fallback"] = True
            return result
        finally:
            # Clear password from memory
            fallback_password = None
            if "password" in kwargs:
                kwargs["password"] = None
    else:
        logger.warning("No password available via fallback methods")
        return {
            "success": False,
            "error": "SSH key setup failed and no password fallback available",
            "details": {"fallback_attempted": True, "fallback_available": False},
        }
