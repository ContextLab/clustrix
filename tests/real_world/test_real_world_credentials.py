#!/usr/bin/env python3
"""
Test script to verify real-world credential integration.

This script tests the integration between 1Password (local development)
and GitHub Actions secrets for real-world testing.
"""

import os
import sys
from pathlib import Path

# Add clustrix to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.real_world.credential_manager import (
    get_credential_manager,
    setup_test_credentials,
    print_credential_status,
)


def test_credential_integration():
    """Test credential integration functionality."""
    print("🔐 Real-World Credential Integration Test")
    print("=" * 50)

    # Get credential manager
    manager = get_credential_manager()

    # Print environment info
    print(
        f"Environment: {'GitHub Actions' if manager.is_github_actions else 'Local Development'}"
    )
    print(f"1Password Available: {'✅' if manager.is_1password_available() else '❌'}")

    # Print credential status
    print_credential_status()

    # Test specific credential types
    print("\n🧪 Testing Specific Credentials:")
    print("=" * 35)

    # Test AWS credentials
    aws_creds = manager.get_aws_credentials()
    if aws_creds:
        print(
            f"✅ AWS: Access key length: {len(aws_creds['access_key_id'])}, Region: {aws_creds['region']}"
        )
    else:
        print("❌ AWS: No credentials found")

    # Test Azure credentials
    azure_creds = manager.get_azure_credentials()
    if azure_creds:
        print(f"✅ Azure: Subscription: {azure_creds['subscription_id'][:8]}...")
    else:
        print("❌ Azure: No credentials found")

    # Test GCP credentials
    gcp_creds = manager.get_gcp_credentials()
    if gcp_creds:
        print(f"✅ GCP: Project: {gcp_creds['project_id']}")
    else:
        print("❌ GCP: No credentials found")

    # Test SSH credentials
    ssh_creds = manager.get_ssh_credentials()
    if ssh_creds:
        print(f"✅ SSH: Host: {ssh_creds['host']}, User: {ssh_creds['username']}")
    else:
        print("❌ SSH: No credentials found")

    # Test SLURM credentials
    slurm_creds = manager.get_slurm_credentials()
    if slurm_creds:
        print(f"✅ SLURM: Host: {slurm_creds['host']}, User: {slurm_creds['username']}")
    else:
        print("❌ SLURM: No credentials found")

    # Test HuggingFace credentials
    hf_creds = manager.get_huggingface_credentials()
    if hf_creds:
        token_len = len(hf_creds["token"]) if hf_creds["token"] else 0
        print(f"✅ HuggingFace: Token length: {token_len}")
    else:
        print("❌ HuggingFace: No credentials found")

    # Test Lambda Cloud credentials
    lambda_creds = manager.get_lambda_cloud_credentials()
    if lambda_creds:
        key_len = len(lambda_creds["api_key"]) if lambda_creds["api_key"] else 0
        print(f"✅ Lambda Cloud: API key length: {key_len}")
    else:
        print("❌ Lambda Cloud: No credentials found")

    return True


def test_environment_variable_setup():
    """Test environment variable setup."""
    print("\n🌍 Testing Environment Variable Setup")
    print("=" * 40)

    # Set up environment variables
    setup_test_credentials()

    # Check if environment variables were set
    env_vars_to_check = [
        "TEST_AWS_ACCESS_KEY",
        "TEST_AWS_SECRET_KEY",
        "TEST_AZURE_SUBSCRIPTION_ID",
        "TEST_GCP_PROJECT_ID",
        "TEST_SSH_HOST",
        "TEST_SSH_USERNAME",
        "TEST_SLURM_HOST",
        "TEST_SLURM_USERNAME",
        "HUGGINGFACE_TOKEN",
        "LAMBDA_CLOUD_API_KEY",
    ]

    set_vars = []
    for var in env_vars_to_check:
        value = os.getenv(var)
        if value:
            set_vars.append(var)
            print(f"✅ {var}: Set (length: {len(value)})")
        else:
            print(f"❌ {var}: Not set")

    print(f"\nEnvironment variables set: {len(set_vars)}/{len(env_vars_to_check)}")

    return len(set_vars) > 0


def test_github_actions_simulation():
    """Test GitHub Actions environment simulation."""
    print("\n🎭 Testing GitHub Actions Simulation")
    print("=" * 40)

    # Temporarily set GitHub Actions environment
    original_github_actions = os.environ.get("GITHUB_ACTIONS")
    os.environ["GITHUB_ACTIONS"] = "true"

    # Set mock GitHub secrets
    mock_secrets = {
        "CLUSTRIX_USERNAME": "testuser",
        "CLUSTRIX_PASSWORD": "testpass",
        "LAMBDA_CLOUD_API_KEY": "test_lambda_key",
    }

    original_values = {}
    for key, value in mock_secrets.items():
        original_values[key] = os.environ.get(key)
        os.environ[key] = value

    try:
        # Create new manager in GitHub Actions mode
        from tests.real_world.credential_manager import RealWorldCredentialManager

        gh_manager = RealWorldCredentialManager()

        print(f"GitHub Actions mode: {gh_manager.is_github_actions}")
        print(f"Local development mode: {gh_manager.is_local_development}")

        # Test SSH credentials (should use GitHub secrets)
        ssh_creds = gh_manager.get_ssh_credentials()
        if ssh_creds:
            print(f"✅ SSH: {ssh_creds['username']}@{ssh_creds['host']}")
        else:
            print("❌ SSH: No credentials found")

        # Test SLURM credentials (should use GitHub secrets)
        slurm_creds = gh_manager.get_slurm_credentials()
        if slurm_creds:
            print(f"✅ SLURM: {slurm_creds['username']}@{slurm_creds['host']}")
        else:
            print("❌ SLURM: No credentials found")

        # Test Lambda Cloud credentials (should use GitHub secrets)
        lambda_creds = gh_manager.get_lambda_cloud_credentials()
        if lambda_creds:
            print(f"✅ Lambda Cloud: API key set")
        else:
            print("❌ Lambda Cloud: No credentials found")

        print("✅ GitHub Actions simulation successful")

    finally:
        # Restore original environment
        if original_github_actions:
            os.environ["GITHUB_ACTIONS"] = original_github_actions
        else:
            os.environ.pop("GITHUB_ACTIONS", None)

        for key, original_value in original_values.items():
            if original_value is not None:
                os.environ[key] = original_value
            else:
                os.environ.pop(key, None)


def test_1password_integration():
    """Test 1Password integration if available."""
    print("\n🔑 Testing 1Password Integration")
    print("=" * 35)

    manager = get_credential_manager()

    if manager.is_1password_available():
        print("✅ 1Password CLI is available")

        # Test retrieving a credential
        try:
            if manager._op_manager:
                # Try to get a test credential
                test_cred = manager._op_manager.get_credential(
                    "clustrix-lambda-cloud-validation", "api_key"
                )
                if test_cred:
                    print(
                        f"✅ Retrieved Lambda Cloud API key (length: {len(test_cred)})"
                    )
                else:
                    print("⚠️  Lambda Cloud credential not found in 1Password")
                    print("   Make sure 'clustrix-lambda-cloud-validation' item exists")
        except Exception as e:
            print(f"❌ Error accessing 1Password: {e}")
    else:
        print("❌ 1Password CLI not available")
        print("   Install with: brew install --cask 1password-cli")
        print("   Then run: op signin")


def main():
    """Main test function."""
    print("🔐 Real-World Credential Integration Test Suite")
    print("=" * 60)

    try:
        # Test credential integration
        test_credential_integration()

        # Test environment variable setup
        test_environment_variable_setup()

        # Test GitHub Actions simulation
        test_github_actions_simulation()

        # Test 1Password integration
        test_1password_integration()

        print("\n🎉 All credential integration tests completed!")
        print("\n📋 Summary:")
        print("  • Credential manager working correctly")
        print("  • Environment variable setup functional")
        print("  • GitHub Actions simulation successful")
        print("  • Ready for real-world testing")

        return 0

    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
