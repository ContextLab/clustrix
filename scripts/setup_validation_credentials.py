#!/usr/bin/env python3
"""Setup script for validation credentials using 1Password.

This script helps set up all the credentials needed for external service validation
in a secure way using 1Password CLI.
"""

import json
import sys
from pathlib import Path

# Add clustrix to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import SecureCredentialManager, ensure_secure_environment


def setup_1password_vault():
    """Set up 1Password vault for Clustrix validation."""
    print("🔐 1Password Setup for Clustrix Validation")
    print("=" * 50)
    
    cred_manager = SecureCredentialManager()
    
    if not cred_manager.is_op_available():
        print("❌ 1Password CLI not available!")
        print("\n📥 To install 1Password CLI:")
        print("   macOS: brew install --cask 1password-cli")
        print("   Linux: https://developer.1password.com/docs/cli/get-started/")
        print("   Windows: https://developer.1password.com/docs/cli/get-started/")
        print("\n🔑 After installation, sign in with: op signin")
        return False
    
    print("✅ 1Password CLI available and authenticated")
    print(f"   Vault: {cred_manager.vault_name}")
    
    return True


def guide_credential_setup():
    """Guide user through credential setup process."""
    print("\n📋 Credential Setup Guide")
    print("=" * 30)
    
    credentials_to_setup = [
        {
            "name": "clustrix-aws-validation",
            "description": "AWS credentials for pricing and compute validation",
            "fields": {
                "access_key_id": "AWS Access Key ID",
                "secret_access_key": "AWS Secret Access Key", 
                "region": "AWS Region (e.g., us-east-1)"
            },
            "setup_notes": [
                "Create IAM user with pricing:GetProducts permission",
                "For compute testing: ec2:* permissions (use sandbox account)",
                "Get credentials from AWS Console → IAM → Users → Security Credentials"
            ]
        },
        {
            "name": "clustrix-gcp-validation", 
            "description": "GCP credentials for pricing and compute validation",
            "fields": {
                "project_id": "GCP Project ID",
                "service_account_json": "Service Account JSON key (full content)",
                "region": "GCP Region (e.g., us-central1)"
            },
            "setup_notes": [
                "Create service account with Cloud Billing Catalog Viewer role",
                "For compute testing: Compute Engine Admin role",
                "Download JSON key from GCP Console → IAM → Service Accounts"
            ]
        },
        {
            "name": "clustrix-lambda-cloud-validation",
            "description": "Lambda Cloud credentials for GPU pricing validation", 
            "fields": {
                "api_key": "Lambda Cloud API Key",
                "endpoint": "API Endpoint (default: https://cloud.lambdalabs.com/api/v1)"
            },
            "setup_notes": [
                "Sign up at https://lambdalabs.com/",
                "Generate API key from account settings",
                "Note: Lambda Cloud has limited free tier"
            ]
        },
        {
            "name": "clustrix-huggingface-validation",
            "description": "HuggingFace credentials for Spaces validation",
            "fields": {
                "token": "HuggingFace API Token",
                "username": "HuggingFace Username"
            },
            "setup_notes": [
                "Create account at https://huggingface.co/",
                "Generate token at https://huggingface.co/settings/tokens",
                "Use 'Write' access for full testing capabilities"
            ]
        },
        {
            "name": "clustrix-docker-validation",
            "description": "Docker registry credentials for container testing",
            "fields": {
                "username": "Docker Hub Username",
                "password": "Docker Hub Password/Token",
                "registry": "Registry URL (default: docker.io)"
            },
            "setup_notes": [
                "Create Docker Hub account",
                "Generate access token (recommended over password)",
                "For testing: create temporary repository"
            ]
        },
        {
            "name": "clustrix-ssh-validation",
            "description": "SSH credentials for cluster testing",
            "fields": {
                "hostname": "SSH Hostname/IP",
                "username": "SSH Username", 
                "private_key": "SSH Private Key (PEM format)",
                "port": "SSH Port (default: 22)"
            },
            "setup_notes": [
                "Set up test VM (AWS EC2, GCP Compute, etc.)",
                "Generate SSH key pair: ssh-keygen -t rsa -b 4096",
                "Add public key to ~/.ssh/authorized_keys on target"
            ]
        }
    ]
    
    print(f"\n📝 To set up credentials in 1Password:")
    print(f"   1. Open 1Password app")
    print(f"   2. Navigate to 'clustrix-dev' vault (or create it)")
    print(f"   3. Create new items with these exact names:")
    print()
    
    for cred in credentials_to_setup:
        print(f"🔑 {cred['name']}")
        print(f"   Description: {cred['description']}")
        print(f"   Fields to add:")
        for field_name, field_desc in cred['fields'].items():
            print(f"     - {field_name}: {field_desc}")
        print(f"   Setup notes:")
        for note in cred['setup_notes']:
            print(f"     • {note}")
        print()
    
    print("💡 Alternative: Use environment variables as fallback")
    print("   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
    print("   GOOGLE_APPLICATION_CREDENTIALS, GOOGLE_CLOUD_PROJECT")
    print("   LAMBDA_CLOUD_API_KEY")
    print("   HUGGINGFACE_TOKEN")
    print()


def test_credential_access():
    """Test that credentials can be accessed."""
    print("🧪 Testing Credential Access")
    print("=" * 30)
    
    from clustrix.secure_credentials import ValidationCredentials
    
    creds = ValidationCredentials()
    
    tests = [
        ("AWS", creds.get_aws_credentials),
        ("GCP", creds.get_gcp_credentials), 
        ("Lambda Cloud", creds.get_lambda_cloud_credentials),
        ("HuggingFace", creds.get_huggingface_credentials),
        ("Docker", creds.get_docker_credentials),
        ("SSH", creds.get_ssh_credentials)
    ]
    
    available_creds = []
    
    for name, get_cred_func in tests:
        try:
            cred_data = get_cred_func()
            if cred_data:
                print(f"✅ {name}: Available")
                available_creds.append(name)
            else:
                print(f"❌ {name}: Not available")
        except Exception as e:
            print(f"❌ {name}: Error - {e}")
    
    print(f"\n📊 Summary: {len(available_creds)}/{len(tests)} credential sets available")
    
    if available_creds:
        print(f"✅ Ready to validate: {', '.join(available_creds)}")
    else:
        print("⚠️  No credentials available - follow setup guide above")
    
    return len(available_creds) > 0


def main():
    """Main setup function."""
    print("🔐 Clustrix Validation Credential Setup")
    print("=" * 45)
    
    # Ensure secure environment
    ensure_secure_environment()
    print("✅ Secure environment configured")
    
    # Check 1Password availability
    if not setup_1password_vault():
        return 1
    
    # Guide user through setup
    guide_credential_setup()
    
    # Test access
    if test_credential_access():
        print("\n🎉 Credential setup validation completed!")
        print("   You can now run validation scripts:")
        print("   - python scripts/validate_lambda_cloud_pricing.py")
        print("   - python scripts/validate_huggingface_pricing.py")
        return 0
    else:
        print("\n⚠️  Complete credential setup first, then re-run this script")
        return 1


if __name__ == "__main__":
    exit(main())