#!/usr/bin/env python3
"""Setup script for validation credentials using 1Password.

This script helps set up all the credentials needed for external service validation
in a secure way using 1Password CLI.
"""

import sys
from pathlib import Path

# Add clustrix to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Imported after the path is set, which is the point of this script running
# standalone against a checkout.
from clustrix.secure_credentials import (  # noqa: E402
    SecureCredentialManager,
    ensure_secure_environment,
)


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
            "name": "clustrix-huggingface-validation",
            "description": "HuggingFace credentials for HuggingFace Jobs validation",
            "fields": {
                "token": "HuggingFace API Token",
                "username": "HuggingFace Username",
            },
            "setup_notes": [
                "Create account at https://huggingface.co/",
                "Generate token at https://huggingface.co/settings/tokens",
                "Use 'Write' access for full testing capabilities",
            ],
        },
        {
            "name": "clustrix-ssh-validation",
            "description": "SSH credentials for cluster testing",
            "fields": {
                "hostname": "SSH Hostname/IP",
                "username": "SSH Username",
                "private_key": "SSH Private Key (PEM format)",
                "port": "SSH Port (default: 22)",
            },
            "setup_notes": [
                "Use any SSH-accessible host (lab machine, cluster login node, VM)",
                "Generate SSH key pair: ssh-keygen -t rsa -b 4096",
                "Add public key to ~/.ssh/authorized_keys on target",
            ],
        },
    ]

    print("\n📝 To set up credentials in 1Password:")
    print("   1. Open 1Password app")
    print("   2. Navigate to 'clustrix-dev' vault (or create it)")
    print("   3. Create new items with these exact names:")
    print()

    for cred in credentials_to_setup:
        print(f"🔑 {cred['name']}")
        print(f"   Description: {cred['description']}")
        print("   Fields to add:")
        for field_name, field_desc in cred["fields"].items():
            print(f"     - {field_name}: {field_desc}")
        print("   Setup notes:")
        for note in cred["setup_notes"]:
            print(f"     • {note}")
        print()

    print("💡 Alternative: Use environment variables as fallback")
    print("   HUGGINGFACE_TOKEN (or HF_TOKEN), HUGGINGFACE_USERNAME")
    print("   SSH_HOST, SSH_USERNAME, SSH_PASSWORD, SSH_PRIVATE_KEY_PATH")
    print()


def test_credential_access():
    """Test that credentials can be accessed."""
    print("🧪 Testing Credential Access")
    print("=" * 30)

    from clustrix.secure_credentials import ValidationCredentials

    creds = ValidationCredentials()

    tests = [
        ("HuggingFace", creds.get_huggingface_credentials),
        ("SSH", creds.get_ssh_credentials),
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

    print(
        f"\n📊 Summary: {len(available_creds)}/{len(tests)} credential sets available"
    )

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
        return 0
    else:
        print("\n⚠️  Complete credential setup first, then re-run this script")
        return 1


if __name__ == "__main__":
    exit(main())
