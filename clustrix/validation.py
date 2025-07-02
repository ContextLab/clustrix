"""Real cluster validation utilities for enhanced authentication."""

import os
import subprocess
import time
from typing import Dict
import paramiko

from .config import ClusterConfig


def validate_cluster_auth(config: ClusterConfig, password: str = None) -> bool:
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
        if password:
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

    except paramiko.AuthenticationException:
        print("❌ SSH key authentication failed - no valid keys found")
        return False
    except Exception as e:
        print(f"❌ SSH key auth error: {e}")
        return False


def validate_kerberos_auth(config: ClusterConfig) -> bool:
    """
    Validate Kerberos authentication if applicable.

    Args:
        config: Cluster configuration

    Returns:
        True if Kerberos auth successful or not required, False if required but failed
    """
    # Check if this is a Kerberos-enabled cluster
    kerberos_clusters = ["ndoli.dartmouth.edu", "discovery.dartmouth.edu"]

    is_kerberos_cluster = any(
        config.cluster_host.endswith(cluster) for cluster in kerberos_clusters
    )

    if not is_kerberos_cluster:
        print("ℹ️  Not a Kerberos-enabled cluster, skipping Kerberos validation")
        return True  # Not a Kerberos cluster

    print(f"🎫 Testing Kerberos authentication to {config.cluster_host}...")

    try:
        # Check for valid ticket
        result = subprocess.run(["klist", "-s"], capture_output=True, timeout=5)
        if result.returncode != 0:
            print("⚠️  No valid Kerberos ticket - run 'kinit' first")
            print("💡 To authenticate: kinit <username>@DARTMOUTH.EDU")
            return False

        # Check ticket details
        result = subprocess.run(["klist"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print("✅ Valid Kerberos ticket found")
            # Show ticket info (first few lines)
            lines = result.stdout.split("\n")[:3]
            for line in lines:
                if line.strip():
                    print(f"   {line}")

        # Try GSSAPI connection
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            client.connect(
                hostname=config.cluster_host,
                username=config.username,
                port=config.ssh_port,
                gss_auth=True,
                gss_kex=True,
                timeout=10,
            )

            # Test command execution
            stdin, stdout, stderr = client.exec_command("echo 'Kerberos auth working'")
            result = stdout.read().decode().strip()

            client.close()

            if result == "Kerberos auth working":
                print(f"✅ Kerberos authentication successful to {config.cluster_host}")
                return True
            else:
                print("⚠️  Kerberos connection established but command execution failed")
                return False

        except Exception as e:
            print(f"❌ Kerberos SSH connection failed: {e}")
            return False

    except subprocess.TimeoutExpired:
        print("⚠️  Kerberos ticket check timed out")
        return False
    except FileNotFoundError:
        print(
            "⚠️  Kerberos tools not found - install with: sudo apt-get install krb5-user"
        )
        return False
    except Exception as e:
        print(f"❌ Kerberos authentication error: {e}")
        return False


def validate_1password_integration(note_name: str = None) -> bool:
    """
    Validate 1Password CLI integration works.

    Args:
        note_name: Optional specific note to test

    Returns:
        True if 1Password CLI working, False otherwise
    """
    print("🔐 Testing 1Password CLI integration...")

    try:
        # Check if 1Password CLI is installed
        result = subprocess.run(
            ["op", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            print(f"✅ 1Password CLI found: {version}")
        else:
            print("❌ 1Password CLI not found")
            print("💡 Install with: brew install 1password-cli")
            return False

        # Check if signed in
        result = subprocess.run(
            ["op", "account", "list"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            print("✅ 1Password CLI authenticated")

            # Test note access if specified
            if note_name:
                result = subprocess.run(
                    ["op", "item", "get", note_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    print(f"✅ Successfully accessed note '{note_name}'")
                    return True
                else:
                    print(f"⚠️  Could not access note '{note_name}': {result.stderr}")
                    return False

            return True
        else:
            print("❌ 1Password CLI not authenticated")
            print("💡 Sign in with: op signin")
            return False

    except subprocess.TimeoutExpired:
        print("⚠️  1Password CLI check timed out")
        return False
    except FileNotFoundError:
        print("❌ 1Password CLI not found")
        print("💡 Install with: brew install 1password-cli")
        return False
    except Exception as e:
        print(f"❌ 1Password validation error: {e}")
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

    # Test 1Password if enabled
    if config.use_1password:
        results["1password"] = validate_1password_integration(config.onepassword_note)
    else:
        print("ℹ️  1Password integration disabled")
        results["1password"] = None

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
        results["env_password"] = None

    # Test SSH key authentication
    results["ssh_key"] = validate_ssh_key_auth(config)

    # Test Kerberos if applicable
    results["kerberos"] = validate_kerberos_auth(config)

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
        "supports_kerberos": False,
    },
    {
        "name": "ndoli",
        "host": "ndoli.dartmouth.edu",
        "type": "slurm",
        "description": "SLURM cluster with Kerberos/GSSAPI",
        "supports_kerberos": True,
    },
]


def validate_on_test_clusters(username: str = None) -> None:
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
            use_1password=True,
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
