#!/usr/bin/env python3
"""Verify the supported credential path actually resolves credentials.

This file used to test 1Password. 1Password was removed from clustrix in
issue #97, and `SecureCredentialManager` has since been an inert shell whose
`is_op_available()` returns False unconditionally -- so the old
`test_1password_access` could only ever print "not authenticated" and return
False, which pytest reports as a pass. It tested nothing (issue #153).

What is checked now is the path that exists: ~/.clustrix/.env and the
environment, read through `clustrix.credential_manager`. No credential is
invented when one is missing; the assertions below only claim what the
environment actually contains.
"""

import os
import sys
from pathlib import Path

# Add clustrix to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.credential_manager import (  # noqa: E402
    ensure_credential,
    get_credential_status,
    parse_env_file,
)
from clustrix.secure_credentials import ValidationCredentials  # noqa: E402
from tests.real_world.credential_manager import (  # noqa: E402
    CREDENTIAL_SETUP_HINT,
    get_cluster_credentials,
)


def test_credential_status_reports_real_sources():
    """`get_credential_status` describes the .env file and every source."""
    print("🔐 Testing clustrix credential sources")
    print("=" * 40)

    status = get_credential_status()

    # The shape is a contract other tooling reads; assert it, not the contents,
    # because the contents depend on what this developer has configured.
    assert set(status) >= {"env_file", "env_file_exists", "sources", "providers"}
    assert status["env_file"].endswith(".env")
    assert {"DotEnvCredentialSource", "EnvironmentCredentialSource"} <= set(
        status["sources"]
    )
    assert {"ssh", "huggingface"} <= set(status["providers"])

    print(f"   .env file: {status['env_file']} (exists: {status['env_file_exists']})")
    for provider, provider_status in status["providers"].items():
        icon = "✅" if provider_status["available"] else "❌"
        print(f"   {icon} {provider}: source={provider_status['source']}")


def test_huggingface_credentials_match_the_environment():
    """A configured HF token is returned; an unconfigured one yields None."""
    print("\n🧪 Testing HuggingFace credential lookup")
    print("=" * 40)

    # Resolving a credential deliberately does NOT export it, so os.environ
    # alone cannot say what is configured: a token that lives only in
    # ~/.clustrix/.env is invisible there. Read the same two places clustrix
    # reads, in the same precedence order (environment over file).
    status = get_credential_status()
    configured = {**parse_env_file(Path(status["env_file"])), **os.environ}
    env_token = configured.get("HF_TOKEN") or configured.get("HUGGINGFACE_TOKEN")

    hf_creds = ensure_credential("huggingface")
    validation_creds = ValidationCredentials().get_huggingface_credentials()

    if hf_creds and hf_creds.get("token"):
        # Whatever was returned must be what is actually configured, not a
        # placeholder: compare against the environment it came from.
        assert hf_creds["token"] == env_token
        assert validation_creds is not None
        assert validation_creds["token"] == env_token
        print(f"   ✅ token resolved (length {len(hf_creds['token'])})")
    else:
        # No token configured: both paths must say so rather than substitute.
        assert not env_token, "HF token is configured but was not resolved"
        assert validation_creds is None
        print("   ❌ no HF token configured (HF_TOKEN / HUGGINGFACE_TOKEN unset)")


def test_cluster_credentials_are_complete_or_absent():
    """Cluster credentials are either fully usable or None -- never partial."""
    print("\n🌍 Testing cluster credential resolution")
    print("=" * 40)

    for role in ("ssh", "slurm"):
        credentials = get_cluster_credentials(role)
        if credentials is None:
            print(f"   ❌ {role}: not configured")
            continue

        # A half-populated credential connects, fails to authenticate seconds
        # later, and reads like a broken cluster. Guarantee it cannot happen.
        assert credentials["host"], f"{role} credentials have no host"
        assert credentials["username"], f"{role} credentials have no username"
        assert credentials.get("password") or credentials.get(
            "private_key_path"
        ), f"{role} credentials have neither a password nor a key file"
        print(f"   ✅ {role}: {credentials['username']}@{credentials['host']}")


def main():
    """Print a credential report for a developer setting this machine up."""
    print("🔍 Clustrix Credential Access Report")
    print("=" * 45)

    test_credential_status_reports_real_sources()
    test_huggingface_credentials_match_the_environment()
    test_cluster_credentials_are_complete_or_absent()

    configured = [
        role for role in ("ssh", "slurm") if get_cluster_credentials(role) is not None
    ]
    has_hf = ensure_credential("huggingface") is not None

    print("\n📊 Summary:")
    print(f"   Cluster roles configured: {configured or 'none'}")
    print(f"   HuggingFace token: {'✅' if has_hf else '❌'}")

    if not configured and not has_hf:
        print(f"\n❌ No credentials available. {CREDENTIAL_SETUP_HINT}")
        return False
    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
