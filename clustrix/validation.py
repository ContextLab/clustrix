"""Real cluster validation utilities for enhanced authentication."""

import logging
import os
import time
from typing import Dict, Optional
import paramiko

from .config import ClusterConfig
from .credential_release import (
    CredentialTarget,
    hostless_secret_refusal,
    release_credential,
)
from .ssh_security import configure_host_key_policy

logger = logging.getLogger(__name__)


def validate_cluster_auth(
    config: ClusterConfig, password: Optional[str] = None
) -> bool:
    """
    Validate authentication works on real cluster.

    Args:
        config: Cluster configuration
        password: Password to test (if provided)

    Returns:
        True if authentication successful, False otherwise
    """
    print(f"🔐 Testing authentication to {config.cluster_host}...")

    try:
        # Try to establish SSH connection
        client = paramiko.SSHClient()
        configure_host_key_policy(client, config)

        # Try password auth if provided
        if password and config.cluster_host:
            client.connect(
                hostname=config.cluster_host,
                username=config.username,
                password=password,
                port=config.ssh_port,
                timeout=10,
                look_for_keys=False,  # Don't try keys yet
                allow_agent=False,  # Don't use SSH agent
            )
            print(f"✅ Password authentication successful to {config.cluster_host}")

            # Test basic command execution
            stdin, stdout, stderr = client.exec_command("echo 'Auth test successful'")
            result = stdout.read().decode().strip()
            error = stderr.read().decode().strip()

            if result == "Auth test successful":
                print("✅ Command execution working")
                client.close()
                return True
            else:
                print(f"⚠️  Command execution issue: {error}")

        client.close()
        return False

    except paramiko.AuthenticationException as e:
        print(f"❌ Password authentication failed to {config.cluster_host}: {e}")
        return False
    except paramiko.SSHException as e:
        print(f"⚠️  SSH connection error to {config.cluster_host}: {e}")
        return False
    except Exception as e:
        print(f"⚠️  Connection error: {e}")
        return False


def validate_ssh_key_auth(config: ClusterConfig) -> bool:
    """
    Validate SSH key authentication works.

    **Route 13, and the user-reachable one.** This asked paramiko to search
    ``~/.ssh`` and the ssh-agent -- ``look_for_keys=True, allow_agent=True``
    -- for whatever ``config.cluster_host`` said, with no gate anywhere in
    the path. ``run_comprehensive_validation`` does consult the gate, but in
    a *different function*, and the notebook widget's "Test connection"
    button calls this one directly
    (``modern_notebook_widget.ModernClustrixWidget``), so a ``clustrix.yml``
    in the directory the notebook was started from was enough to have the
    victim's own key offered to the host that file named. Measured:
    ``('victim', 'publickey')``.

    Those identities name no host, so they are rule 2 like every other
    hostless secret, and the answer is
    :func:`clustrix.credential_release.hostless_secret_refusal` -- the same
    rule the two connection paths read off
    :attr:`~clustrix.credential_release.CredentialRelease.local_identities`,
    rather than a third copy of it.

    Args:
        config: Cluster configuration

    Returns:
        True if SSH key auth successful, False otherwise
    """
    print(f"🔑 Testing SSH key authentication to {config.cluster_host}...")

    try:
        client = paramiko.SSHClient()
        configure_host_key_policy(client, config)

        # Try SSH key auth
        if config.cluster_host:
            try:
                target = CredentialTarget.for_config(config)
            except ValueError as exc:
                print(f"❌ No SSH key can be offered: {exc}")
                return False
            refusal = hostless_secret_refusal(target, config)
            if refusal:
                print(
                    f"❌ Not offering your SSH keys or agent to "
                    f"{config.cluster_host}: {refusal}"
                )
                return False
            client.connect(
                hostname=config.cluster_host,
                username=config.username,
                port=config.ssh_port,
                timeout=10,
                look_for_keys=True,
                allow_agent=True,
            )

            # Run simple command to verify
            stdin, stdout, stderr = client.exec_command("echo 'SSH key auth working'")
            result = stdout.read().decode().strip()
            error = stderr.read().decode().strip()

            client.close()

            if result == "SSH key auth working":
                print("✅ SSH key authentication successful")
                return True
            else:
                print(f"⚠️  SSH key command execution issue: {error}")
                return False
        else:
            print("❌ No cluster host configured")
            return False

    except paramiko.AuthenticationException:
        print("❌ SSH key authentication failed - no valid keys found")
        return False
    except Exception as e:
        print(f"❌ SSH key auth error: {e}")
        return False


def run_comprehensive_validation(config: ClusterConfig) -> Dict[str, bool]:
    """
    Run comprehensive validation of all authentication methods.

    Args:
        config: Cluster configuration to validate

    Returns:
        Dictionary of validation results
    """
    print(f"\n{'=' * 60}")
    print("🧪 Comprehensive Authentication Validation")
    print(f"Cluster: {config.username}@{config.cluster_host}")
    print("=" * 60)

    results = {}

    # Test environment variable if enabled.
    #
    # This used to be ``config.get_env_password()``, which had no host check
    # and no provenance check, and its result went straight into
    # ``validate_cluster_auth`` -> ``paramiko.connect(hostname=
    # config.cluster_host)``. With a working-directory ``clustrix.yml`` the
    # whole method was the repository's: the file names ``password_env_var``
    # as well as ``cluster_host``, so it chose which of the victim's
    # environment variables to read *and* where to send it. That was route 6
    # of issue #167 and it is why ``get_env_password`` no longer exists.
    if config.use_env_password:
        try:
            target = CredentialTarget.for_config(config)
        except ValueError as exc:
            print(f"❌ {exc}")
            results["env_password"] = False
        else:
            release = release_credential(
                target, provider="ssh", config=config, sources=("environment",)
            )
            if release.refusal is not None:
                print(f"❌ ${config.password_env_var} was not used: {release.refusal}")
                results["env_password"] = False
            else:
                print(
                    f"✅ Environment variable {config.password_env_var} "
                    f"contains password"
                )
                results["env_password"] = validate_cluster_auth(
                    config, release.password
                )
    else:
        print("ℹ️  Environment variable password disabled")
        results["env_password"] = False

    # Test SSH key authentication
    results["ssh_key"] = validate_ssh_key_auth(config)

    # Summary
    print(f"\n{'=' * 60}")
    print("📊 Validation Summary:")

    for method, result in results.items():
        if result is None:
            status = "SKIPPED"
            icon = "⏭️ "
        elif result:
            status = "PASSED"
            icon = "✅"
        else:
            status = "FAILED"
            icon = "❌"

        print(f"{icon} {method.replace('_', ' ').title()}: {status}")

    print("=" * 60)

    return results


#: Clusters this validation pass should try, read from the environment.
#:
#: These were hardcoded to a particular institution's hostnames, which meant
#: the shipped package named someone's real infrastructure and was useless to
#: anyone else. Set CLUSTRIX_VALIDATION_SSH_HOST and/or
#: CLUSTRIX_VALIDATION_SLURM_HOST to point it at your own; with neither set,
#: validation reports that it has nothing to check rather than trying to
#: connect to hosts you do not own.
def _validation_clusters():
    """Build the validation target list from the environment."""
    clusters = []
    ssh_host = os.environ.get("CLUSTRIX_VALIDATION_SSH_HOST")
    if ssh_host:
        clusters.append(
            {
                "name": os.environ.get("CLUSTRIX_VALIDATION_SSH_NAME", ssh_host),
                "host": ssh_host,
                "type": "ssh",
                "description": "SSH cluster for basic validation",
            }
        )
    slurm_host = os.environ.get("CLUSTRIX_VALIDATION_SLURM_HOST")
    if slurm_host:
        clusters.append(
            {
                "name": os.environ.get("CLUSTRIX_VALIDATION_SLURM_NAME", slurm_host),
                "host": slurm_host,
                "type": "slurm",
                "description": "SLURM cluster for scheduler validation",
            }
        )
    return clusters


def validate_on_test_clusters(username: Optional[str] = None) -> None:
    """
    Run validation on all test clusters.

    Args:
        username: Username to use for testing (defaults to current user)
    """
    if username is None:
        username = os.environ.get("USER", "testuser")

    print(f"\n{'=' * 80}")
    print("🏗️  CLUSTRIX AUTHENTICATION VALIDATION SUITE")
    print("=" * 80)

    validation_clusters = _validation_clusters()
    if not validation_clusters:
        logger.warning(
            "No validation clusters configured. Set "
            "CLUSTRIX_VALIDATION_SSH_HOST and/or "
            "CLUSTRIX_VALIDATION_SLURM_HOST to run this against your own "
            "cluster; nothing will be checked otherwise."
        )
    for cluster_info in validation_clusters:
        print(f"\n🧪 Testing cluster: {cluster_info['name']}")
        print(f"   Host: {cluster_info['host']}")
        print(f"   Type: {cluster_info['type']}")
        print(f"   Description: {cluster_info['description']}")

        config = ClusterConfig(
            cluster_type=cluster_info["type"],
            cluster_host=cluster_info["host"],
            username=username,
            use_env_password=True,
            password_env_var="CLUSTER_PASSWORD",
        )

        run_comprehensive_validation(config)

        # Wait a moment between clusters
        time.sleep(1)

    print(f"\n{'=' * 80}")
    print("✅ Validation suite complete!")
    print("=" * 80)


if __name__ == "__main__":
    # Run validation on test clusters
    validate_on_test_clusters()
