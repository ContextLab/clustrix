"""Real cluster validation utilities for enhanced authentication."""

import os
import time
from typing import Dict, Optional
import paramiko

from .config import ClusterConfig


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
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

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

    Args:
        config: Cluster configuration

    Returns:
        True if SSH key auth successful, False otherwise
    """
    print(f"🔑 Testing SSH key authentication to {config.cluster_host}...")

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Try SSH key auth
        if config.cluster_host:
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

    # Test environment variable if enabled
    if config.use_env_password:
        env_password = config.get_env_password()
        if env_password:
            print(
                f"✅ Environment variable {config.password_env_var} contains password"
            )
            results["env_password"] = validate_cluster_auth(config, env_password)
        else:
            print(f"❌ Environment variable {config.password_env_var} not set")
            results["env_password"] = False
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


# Test cluster configurations for validation
TEST_CLUSTERS = [
    {
        "name": "tensor01",
        "host": "tensor01.dartmouth.edu",
        "type": "ssh",
        "description": "Simple SSH cluster for basic testing",
    },
    {
        "name": "ndoli",
        "host": "ndoli.dartmouth.edu",
        "type": "slurm",
        "description": "SLURM cluster (requires special authentication)",
    },
]


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

    for cluster_info in TEST_CLUSTERS:
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
