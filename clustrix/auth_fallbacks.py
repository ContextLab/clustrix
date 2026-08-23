"""
Authentication fallback methods for cluster access.

This module provides password fallback functionality when SSH key authentication fails,
with environment-specific handling for different execution contexts.
"""

import sys
import getpass
from typing import TYPE_CHECKING, Optional, Dict, Any
import logging

from .credential_release import (
    HOSTLESS_PASSWORD_VARIABLES,
    HOST_NAMED_PASSWORD_VARIABLES,
    CredentialTarget,
    environment_password_variable,
    hostless_secret_refusal,
    release_credential,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import ClusterConfig

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


def _colab_password(
    target: CredentialTarget, config: Optional["ClusterConfig"]
) -> Optional[str]:
    """A Colab userdata secret for ``target``, or ``None``.

    Colab's store is not one clustrix keeps, so it cannot go through
    ``release_credential``'s branches -- but it is the same two kinds of
    name, and it gets the same two rules. The host-named keys are the user
    naming the host that may have the secret. The generic ``CLUSTER_PASSWORD``
    names no host, so it is rule 2, asked of the one definition of that rule.
    """
    try:
        from google.colab import userdata  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("google.colab not available")
        return None

    named = [
        environment_password_variable(template, target.hostname)
        for template in HOST_NAMED_PASSWORD_VARIABLES
    ]
    for key in named:
        try:
            password = userdata.get(key)
        except Exception:
            continue
        if password:
            logger.info(f"Retrieved password from Colab secrets: {key}")
            return password

    for key in HOSTLESS_PASSWORD_VARIABLES:
        try:
            password = userdata.get(key)
        except Exception:
            continue
        if not password:
            continue
        refusal = hostless_secret_refusal(target, config)
        if refusal:
            logger.warning(
                "Colab secret %s was not offered to %s: %s",
                key,
                target.hostname,
                refusal,
            )
            return None
        logger.info(f"Retrieved password from Colab secrets: {key}")
        return password
    return None


def get_cluster_password(
    target: CredentialTarget, *, config: Optional["ClusterConfig"] = None
) -> Optional[str]:
    """A password for ``target``, asked of the gate before the user.

    **Route 9 was an unconverted call site, not an argument.** This took a
    bare ``hostname`` and scanned five environment variables for it, two of
    which -- ``CLUSTRIX_DEFAULT_PASSWORD`` and ``CLUSTER_PASSWORD`` -- name
    no host at all, and handed whatever it found to whatever hostname it was
    passed. On the ``setup_auth_with_fallback`` path that hostname is
    ``config.cluster_host``, so a cloned repository's ``clustrix.yml``
    collected the user's default cluster password while
    ``release_credential`` was refusing that same host in the same process.
    Lock 3 could never have caught it: it read ``os.environ`` directly and
    never touched the store.

    So it takes a recipient, like everything else that hands out a secret,
    and the environment branch is ``release_credential``'s with the source
    narrowed to it. What is left here is the interactive prompt, which is a
    person reading the hostname and deciding.

    Args:
        target: who is about to receive the password.
        config: the configuration ``target`` was built from, which is what
            provenance is derived from.

    Returns:
        Password string or None if not available
    """
    env = detect_environment()

    # 1. Colab environment - use secrets
    if env == "colab":
        password = _colab_password(target, config)
        if password:
            return password

    # 2. The environment, through the one gate.
    release = release_credential(
        target,
        provider="ssh",
        config=config,
        sources=("fallback-environment",),
    )
    if release.password:
        logger.info("Retrieved password from the environment for %s", target.hostname)
        return release.password
    if release.refusal:
        logger.warning(
            "No environment password for %s: %s", target.hostname, release.refusal
        )

    # 3. Interactive fallbacks based on environment
    prompt = f"Password for {target.username}@{target.hostname}"

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

    # Check for specific error conditions that suggest password auth might work.
    # ``.get("error", "")`` alone is not enough: setup_ssh_keys() always sets
    # the "error" key, defaulting it to None (not absent) on success, so the
    # dict-default never kicks in and `.lower()` below raised
    # AttributeError on the ordinary success path (Issue #114).
    error = auth_result.get("error") or ""
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

    try:
        target = CredentialTarget.for_config(config)
    except ValueError as exc:
        logger.warning("No password fallback is possible: %s", exc)
        return {
            "success": False,
            "error": f"SSH key setup failed and no credential target exists: {exc}",
            "details": {"fallback_attempted": True, "fallback_available": False},
        }

    fallback_password = get_cluster_password(target, config=config)

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
