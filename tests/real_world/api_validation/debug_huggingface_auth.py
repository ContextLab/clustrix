#!/usr/bin/env python3
"""Debug HuggingFace API authentication issues."""

import sys
from pathlib import Path
import requests

# Add clustrix to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials


def debug_huggingface_auth():
    """Debug HuggingFace authentication step by step."""
    print("🔍 HuggingFace Authentication Debug")
    print("=" * 40)

    # Get credentials
    creds = ValidationCredentials()
    hf_creds = creds.get_huggingface_credentials()

    if not hf_creds or not hf_creds.get("token"):
        print("❌ No HuggingFace token found")
        print(f"   hf_creds: {hf_creds}")
        return False

    token = hf_creds["token"]
    username = hf_creds.get("username", "unknown")

    print(f"✅ Retrieved credentials:")
    print(f"   Token prefix: {token[:10]}...")
    print(f"   Token length: {len(token)}")
    print(f"   Username: {username}")
    print(f"   Token starts with 'hf_': {token.startswith('hf_')}")

    # Test different API endpoints
    headers = {"Authorization": f"Bearer {token}"}

    endpoints_to_test = [
        ("whoami", "https://huggingface.co/api/whoami"),
        ("user info", f"https://huggingface.co/api/users/{username}"),
        ("models", "https://huggingface.co/api/models?limit=1"),
        ("datasets", "https://huggingface.co/api/datasets?limit=1"),
    ]

    print(f"\n🧪 Testing API endpoints...")

    working_endpoints = []
    for name, url in endpoints_to_test:
        try:
            print(f"\n   Testing {name} endpoint...")
            response = requests.get(url, headers=headers, timeout=10)
            print(f"   Status: {response.status_code}")

            if response.status_code == 200:
                print(f"   ✅ {name} endpoint working")
                working_endpoints.append(name)
                # Show first few characters of response
                try:
                    resp_json = response.json()
                    if isinstance(resp_json, dict):
                        print(f"   Response keys: {list(resp_json.keys())[:5]}")
                    elif isinstance(resp_json, list):
                        print(f"   Response: list with {len(resp_json)} items")
                except:
                    print(f"   Response length: {len(response.text)} chars")
            elif response.status_code == 401:
                print(f"   ❌ {name} endpoint: 401 Unauthorized")
                print(f"   Response: {response.text[:100]}")
            elif response.status_code == 403:
                print(f"   ❌ {name} endpoint: 403 Forbidden")
                print(f"   Response: {response.text[:100]}")
            else:
                print(f"   ⚠️  {name} endpoint: {response.status_code}")
                print(f"   Response: {response.text[:100]}")

        except Exception as e:
            print(f"   ❌ {name} endpoint error: {e}")

    # Test without authentication to see if endpoints exist
    print(f"\n🌐 Testing endpoints without authentication...")

    public_endpoints = [
        ("public models", "https://huggingface.co/api/models?limit=1"),
        ("public datasets", "https://huggingface.co/api/datasets?limit=1"),
    ]

    for name, url in public_endpoints:
        try:
            response = requests.get(url, timeout=10)
            print(f"   {name}: {response.status_code}")
            if response.status_code == 200:
                print(f"   ✅ {name} accessible without auth")
        except Exception as e:
            print(f"   ❌ {name} error: {e}")

    # Try the huggingface_hub library if available
    print(f"\n📚 Testing huggingface_hub library...")
    try:
        from huggingface_hub import HfApi

        from clustrix.credential_release import huggingface_client_kwargs

        # Pinned, like every client in the tree: without ``endpoint=``,
        # ``huggingface_hub`` reads $HF_ENDPOINT, so an inherited
        # environment variable would choose where this real token is sent.
        api = HfApi(token=token, **huggingface_client_kwargs())

        print("   Testing HfApi.whoami()...")
        user_info = api.whoami()
        print(f"   ✅ HfApi working: {user_info}")
        working_endpoints.append("huggingface_hub")

    except ImportError:
        print("   ⚠️  huggingface_hub not installed")
    except Exception as e:
        print(f"   ❌ HfApi error: {e}")

    # Summary
    print(f"\n📊 Summary:")
    print(f"   Working endpoints: {len(working_endpoints)}")
    for endpoint in working_endpoints:
        print(f"     ✅ {endpoint}")

    if working_endpoints:
        print(f"\n✅ HuggingFace authentication IS working!")
        print(f"   The token is valid and functional")
        return True
    else:
        print(f"\n❌ HuggingFace authentication NOT working")
        print(f"   Token may be invalid, expired, or lack permissions")

        print(f"\n💡 Troubleshooting steps:")
        print(f"   1. Check token at: https://huggingface.co/settings/tokens")
        print(f"   2. Ensure token has 'read' permissions")
        print(f"   3. Try creating a new token")
        print(f"   4. Verify account is not suspended")

        return False


if __name__ == "__main__":
    success = debug_huggingface_auth()
    exit(0 if success else 1)
