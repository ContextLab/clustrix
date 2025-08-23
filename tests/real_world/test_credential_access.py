#!/usr/bin/env python3
"""Test script to verify credential access methods."""

import os
import sys
from pathlib import Path

# Add clustrix to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import SecureCredentialManager, ValidationCredentials


def test_1password_access():
    """Test 1Password CLI access."""
    print("🔐 Testing 1Password CLI Access")
    print("=" * 40)

    cred_manager = SecureCredentialManager()

    print(f"1Password CLI available: {cred_manager.is_op_available()}")

    if not cred_manager.is_op_available():
        print("\n❌ 1Password CLI not authenticated")
        print("\nTo enable 1Password CLI:")
        print("1. Open 1Password app")
        print("2. Go to Settings → Developer")
        print("3. Enable 'Connect with 1Password CLI'")
        print("4. Or run: op signin")
        return False

    print("✅ 1Password CLI is available and authenticated")

    # Test retrieving a credential
    try:
        test_cred = cred_manager.get_credential(
            "clustrix-huggingface-validation", "token"
        )
        if test_cred:
            print(
                f"✅ Successfully retrieved HuggingFace token (length: {len(test_cred)})"
            )
            return True
        else:
            print("⚠️  Could not retrieve HuggingFace token")
            return False
    except Exception as e:
        print(f"❌ Error retrieving credential: {e}")
        return False


def test_validation_credentials():
    """Test the ValidationCredentials class."""
    print("\n🧪 Testing ValidationCredentials")
    print("=" * 40)

    creds = ValidationCredentials()

    # Test HuggingFace credentials
    hf_creds = creds.get_huggingface_credentials()
    if hf_creds:
        print("✅ HuggingFace credentials found")
        token = hf_creds.get("token", "")
        print(f"   Token length: {len(token) if token else 0}")
        if hf_creds.get("username"):
            print(f"   Username: {hf_creds['username']}")
    else:
        print("❌ HuggingFace credentials not found")

    # Test Lambda Cloud credentials
    lambda_creds = creds.get_lambda_cloud_credentials()
    if lambda_creds:
        print("✅ Lambda Cloud credentials found")
        api_key = lambda_creds.get("api_key", "")
        print(f"   API key length: {len(api_key) if api_key else 0}")
        print(f"   Endpoint: {lambda_creds.get('endpoint', 'default')}")
    else:
        print("❌ Lambda Cloud credentials not found")

    # Test AWS credentials
    aws_creds = creds.get_aws_credentials()
    if aws_creds:
        print("✅ AWS credentials found")
        access_key = aws_creds.get("aws_access_key_id", "")
        print(f"   Access key length: {len(access_key) if access_key else 0}")
        print(f"   Region: {aws_creds.get('aws_region', 'default')}")
    else:
        print("❌ AWS credentials not found")

    # Test GCP credentials
    gcp_creds = creds.get_gcp_credentials()
    if gcp_creds:
        print("✅ GCP credentials found")
        print(f"   Project ID: {gcp_creds.get('project_id', 'not set')}")
        print(f"   Region: {gcp_creds.get('region', 'default')}")
    else:
        print("❌ GCP credentials not found")


def test_environment_fallback():
    """Test environment variable fallback."""
    print("\n🌍 Testing Environment Variable Fallback")
    print("=" * 45)

    env_vars = [
        "HUGGINGFACE_TOKEN",
        "HF_TOKEN",
        "LAMBDA_CLOUD_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
    ]

    found_vars = []
    for var in env_vars:
        value = os.getenv(var)
        if value:
            found_vars.append(var)
            print(f"✅ {var}: {'*' * min(len(value), 10)}")
        else:
            print(f"❌ {var}: Not set")

    print(f"\nEnvironment variables found: {len(found_vars)}/{len(env_vars)}")

    if found_vars:
        print("✅ Some credentials available via environment variables")
        return True
    else:
        print("❌ No credentials found in environment variables")
        return False


def main():
    """Main test function."""
    print("🔍 Clustrix Credential Access Test")
    print("=" * 45)

    op_success = test_1password_access()
    test_validation_credentials()
    env_success = test_environment_fallback()

    print(f"\n📊 Summary:")
    print(f"   1Password CLI: {'✅' if op_success else '❌'}")
    print(f"   Environment Variables: {'✅' if env_success else '❌'}")

    if op_success:
        print("\n🎉 1Password integration working!")
        print("   Ready for full credential validation")
    elif env_success:
        print("\n⚠️  1Password not available, but environment variables found")
        print("   Some validation possible with environment credentials")
    else:
        print("\n❌ No credential access methods available")
        print("   Set up 1Password CLI or environment variables")

    return op_success or env_success


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
