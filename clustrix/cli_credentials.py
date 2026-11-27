"""CLI tools for interactive credential setup and management.

This module provides command-line tools for setting up and managing Clustrix credentials,
including interactive setup, validation, and migration from existing systems.
"""

import os
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
import logging

import click

from .credential_manager import (
    FlexibleCredentialManager,
    format_env_assignment,
    get_credential_manager,
    parse_env_file,
    write_text_securely,
)
from .credential_release import (
    CredentialTarget,
    describe_credential,
    huggingface_client_kwargs,
    release_credential,
)
from .ssh_security import configure_host_key_policy

logger = logging.getLogger(__name__)


def setup_credentials_interactive():
    """Interactive credential setup wizard that creates and populates .env file."""
    print("🔐 Clustrix Credential Setup Wizard")
    print("=" * 50)

    manager = FlexibleCredentialManager()

    print(f"📁 Configuration directory: {manager.config_dir}")
    print(f"📄 Credential file: {manager.env_file}")

    # Asked only when the file already *holds* credentials. Constructing the
    # manager above creates the commented-out template, so "does the file
    # exist" was true on every first run -- and the question defaults to No,
    # so pressing Enter through a first-time setup cancelled it.
    configured = sorted(parse_env_file(manager.env_file))
    if configured:
        print(f"📋 Credentials already configured: {', '.join(configured)}")
        if not click.confirm("Do you want to update existing credentials?"):
            print("Setup cancelled.")
            return False

    # Collect credentials for each provider
    credentials_to_add = {}

    print("\n🔧 Select providers to configure:")

    if click.confirm("Configure SSH cluster access (for SLURM and SSH)?"):
        ssh_creds = _collect_ssh_credentials_interactive()
        if ssh_creds:
            credentials_to_add.update(ssh_creds)

    if click.confirm("Configure HuggingFace credentials (for HuggingFace Jobs)?"):
        hf_creds = _collect_huggingface_credentials_interactive()
        if hf_creds:
            credentials_to_add.update(hf_creds)

    if not credentials_to_add:
        print("No credentials configured. You can add them manually by editing:")
        print(f"  {manager.env_file}")
        return True

    # Write credentials to .env file
    print(f"\n💾 Writing credentials to {manager.env_file}...")
    success = _write_credentials_to_env_file(manager.env_file, credentials_to_add)

    if success:
        print("✅ Credentials saved successfully!")
        print("\n🔍 Testing credentials...")
        test_credentials_command()
        return True
    else:
        print("❌ Failed to save credentials")
        return False


def _collect_ssh_credentials_interactive() -> Dict[str, str]:
    """Collect and validate SSH credentials interactively."""
    print("\n🔑 SSH Cluster Credential Setup")
    print("For accessing SLURM or plain SSH clusters")

    try:
        host = click.prompt("SSH Host (e.g., cluster.university.edu)", type=str)
        username = click.prompt("SSH Username", type=str)

        auth_method = click.prompt(
            "Authentication method",
            type=click.Choice(["password", "private_key"]),
            default="password",
        )

        credentials = {
            "SSH_HOST": host,
            "SSH_USERNAME": username,
            "SSH_PORT": click.prompt("SSH Port", default="22", type=str),
        }

        if auth_method == "password":
            password = click.prompt("SSH Password", type=str, hide_input=True)
            credentials["SSH_PASSWORD"] = password
        else:
            key_path = click.prompt(
                "Private Key Path",
                default=str(Path.home() / ".ssh" / "id_rsa"),
                type=str,
            )
            if Path(key_path).exists():
                credentials["SSH_PRIVATE_KEY_PATH"] = key_path
            else:
                print(f"❌ Private key not found at {key_path}")
                return {}

        # Validate SSH connection
        print("🔍 Testing SSH connection...")
        if _validate_ssh_credentials_real(credentials):
            print("✅ SSH connection validated successfully")
            return credentials
        else:
            print("❌ SSH connection validation failed")
            return {}
    except click.Abort:
        print("SSH credential setup cancelled")
        return {}


def _collect_huggingface_credentials_interactive() -> Dict[str, str]:
    """Collect HuggingFace credentials interactively."""
    print("\n🔑 HuggingFace Credential Setup")
    print("You can find your token at: https://huggingface.co/settings/tokens")

    try:
        token = click.prompt("HuggingFace Token", type=str, hide_input=True)
        username = click.prompt("HuggingFace Username (optional)", default="", type=str)

        credentials = {"HF_TOKEN": token}
        if username:
            credentials["HF_USERNAME"] = username

        # Validate credentials
        print("🔍 Validating HuggingFace credentials...")
        if _validate_huggingface_credentials_real(credentials):
            print("✅ HuggingFace credentials validated successfully")
            return credentials
        else:
            print("❌ HuggingFace credential validation failed")
            return {}
    except click.Abort:
        print("HuggingFace credential setup cancelled")
        return {}


def _validate_ssh_credentials_real(credentials: Dict[str, str]) -> bool:
    """Validate SSH credentials using real SSH connection attempt."""
    try:
        import paramiko

        ssh = paramiko.SSHClient()
        # No ClusterConfig exists yet at this stage of credential setup, so
        # this always uses the strict default: unknown host keys are
        # rejected with an actionable error rather than trusted silently.
        configure_host_key_policy(ssh, None)

        # Prepare connection parameters with proper types
        hostname = credentials["SSH_HOST"]
        username = credentials["SSH_USERNAME"]
        port = int(credentials.get("SSH_PORT", 22))
        timeout = 10

        # Make real SSH connection with proper parameter types.
        #
        # ``look_for_keys`` and ``allow_agent`` off, both branches: this
        # validates the credential it was handed, and with paramiko's own
        # search left on it reported "credentials validated" whenever the
        # agent happened to hold a key for the host -- a green tick for a
        # password that does not work. The route 13 setting, here for
        # honesty rather than for containment (the host is the one the
        # credential file names, which is the user authorising it).
        if "SSH_PASSWORD" in credentials:
            password = credentials["SSH_PASSWORD"]
            ssh.connect(
                hostname=hostname,
                username=username,
                port=port,
                password=password,
                timeout=timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        elif "SSH_PRIVATE_KEY_PATH" in credentials:
            key_filename = credentials["SSH_PRIVATE_KEY_PATH"]
            ssh.connect(
                hostname=hostname,
                username=username,
                port=port,
                key_filename=key_filename,
                timeout=timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        else:
            return False

        # Test with a simple command
        stdin, stdout, stderr = ssh.exec_command('echo "connection_test"')
        output = stdout.read().decode().strip()

        ssh.close()

        # Validate we got expected output
        success = output == "connection_test"
        if success:
            logger.info(f"SSH connection validated to {credentials['SSH_HOST']}")
        return success

    except ImportError:
        logger.warning("paramiko not available for SSH validation")
        return False  # Conservative: require validation
    except Exception as e:
        logger.debug(f"SSH validation error: {e}")
        return False


def _validate_huggingface_credentials_real(credentials: Dict[str, str]) -> bool:
    """Validate HuggingFace credentials using real HF API call."""
    try:
        from huggingface_hub import HfApi

        # from huggingface_hub.utils import RepositoryNotFoundError  # Currently unused

        # Create HF API client
        api = HfApi(token=credentials["HF_TOKEN"], **huggingface_client_kwargs())

        # Make real API call to get user info
        user_info = api.whoami()

        logger.info(
            f"HuggingFace credentials validated for user: {user_info.get('name', 'unknown')}"
        )
        return True

    except Exception as e:
        logger.debug(f"HuggingFace validation error: {e}")
        return False


def _write_credentials_to_env_file(env_file: Path, credentials: Dict[str, str]) -> bool:
    """Write credentials to .env file with atomic operation and secure permissions."""
    try:
        # Read existing content to preserve comments and structure
        existing_content = ""
        if env_file.exists():
            existing_content = env_file.read_text(encoding="utf-8")

        # Merge new credentials with existing content
        updated_content = _merge_env_content(existing_content, credentials)

        # Write with atomic operation
        temp_file = env_file.with_suffix(".tmp")
        write_text_securely(temp_file, updated_content)

        # Atomic replacement
        temp_file.replace(env_file)

        logger.info(f"Credentials written to {env_file}")
        return True

    except Exception as e:
        logger.error(f"Failed to write credentials: {e}")
        return False


def _assigned_name(line: str) -> Optional[str]:
    """The variable an active ``.env`` line assigns, if it assigns one."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    name = stripped.split("=", 1)[0].strip()
    if name.startswith("export "):
        name = name[len("export ") :].strip()
    return name


def _merge_env_content(existing_content: str, new_credentials: Dict[str, str]) -> str:
    """Set ``new_credentials`` in ``.env`` content, preserving everything else.

    Each name ends up assigned exactly once, at the position of its first
    existing assignment, else in place of its commented-out template line,
    else at the end. Replacing matters: this used to write a name only when
    it was commented out or absent, so ``clustrix credentials setup`` could
    never change a password it had already saved -- the old value stayed and
    the wizard reported success. Later duplicates of a name are removed,
    because the parser lets the last assignment win and a stale one further
    down would silently override the value just written.
    """
    lines = existing_content.split("\n") if existing_content else []
    while lines and not lines[-1].strip():
        lines.pop()

    assigned = {name for name in map(_assigned_name, lines) if name}
    remaining = dict(new_credentials)
    merged: List[str] = []
    for line in lines:
        name = _assigned_name(line)
        if name is None and line.strip().startswith("#"):
            # A commented-out template line stands in for a name that has no
            # active assignment anywhere in the file.
            candidate = _assigned_name(line.strip()[1:])
            if candidate in remaining and candidate not in assigned:
                name = candidate
        if name not in new_credentials:
            merged.append(line)
        elif name in remaining:
            merged.append(format_env_assignment(name, remaining.pop(name)))
        # else: a later duplicate of a name already written -- dropped.

    merged.extend(format_env_assignment(key, value) for key, value in remaining.items())
    return "\n".join(merged) + "\n"


# Main CLI command functions


def list_credentials_command():
    """List all available credentials with their sources (values masked)."""
    manager = get_credential_manager()
    status = manager.get_credential_status()

    print("🔑 Clustrix Credential Status")
    print("=" * 50)
    print(f"📁 Config Directory: {status['config_directory']}")
    print(f"📄 Credential File: {status['env_file']}")
    print(f"📋 .env File Exists: {'✅' if status['env_file_exists'] else '❌'}")

    print("\n📊 Credential Sources:")
    for source_name, source_info in status["sources"].items():
        available = "✅" if source_info["available"] else "❌"
        print(f"  {available} {source_name}")
        if source_info.get("providers"):
            print(f"    Providers: {', '.join(source_info['providers'])}")

    print("\n🔐 Available Credentials:")
    for provider, provider_info in status["providers"].items():
        if provider_info["available"]:
            source = provider_info["source"]
            fields = ", ".join(provider_info["fields"])
            print(f"  ✅ {provider.upper()}: {fields} (from {source})")
        else:
            print(f"  ❌ {provider.upper()}: Not configured")

    if not any(p["available"] for p in status["providers"].values()):
        print(
            "\n💡 No credentials configured. Run 'clustrix credentials setup' to get started."
        )


def test_credentials_command() -> bool:
    """Test every configured credential with a real connection.

    Returns whether the run is a pass: at least one credential is
    configured, and every configured one worked. With nothing configured
    there is nothing that passed, and the command used to exit 0 all the
    same -- so a script checking it could not tell "working" from "absent".
    """
    working, broken = [], []
    print("🧪 Testing Clustrix Credentials")
    print("=" * 50)

    # Test each provider
    providers_to_test = [
        "ssh",
        "huggingface",
    ]

    for provider in providers_to_test:
        print(f"\n🔍 Testing {provider.upper()} credentials...")

        # What is configured is a question about names, not values, so it is
        # answered without obtaining a secret at all. The secret itself comes
        # from the gate below, with the recipient named.
        described = describe_credential(provider)
        if not described.available:
            print("  ❌ No credentials found")
            continue

        # Test with real validation
        if provider == "ssh":
            if not (described.host and described.username):
                print(
                    "  ❌ Missing required SSH credentials (need host, username, and password or private_key_path)"
                )
                broken.append(provider)
                continue
            # The credential file naming SSH_HOST *is* the user authorising
            # that host, which is rule 1 of the release rules. So the target
            # is the credential's own host, and the release is the one the
            # rule was written for.
            target = CredentialTarget(
                hostname=described.host,
                username=described.username,
                described_as="SSH_HOST from the credential file",
            )
            release = release_credential(target, provider="ssh")
            if release.refusal is not None:
                print(f"  ❌ {release.refusal}")
                broken.append(provider)
                continue
            # ``_validate_ssh_credentials_real`` reads the ``SSH_*`` spelling
            # of the credential file, and this passed it the lower-case field
            # names ``resolve_provider_credentials`` emits -- so every run
            # raised KeyError inside the helper's own try block and reported
            # "invalid or inaccessible" for credentials that were fine.
            ssh_credentials = {
                "SSH_HOST": described.host,
                "SSH_USERNAME": described.username,
                "SSH_PORT": described.port or "22",
            }
            if release.password:
                ssh_credentials["SSH_PASSWORD"] = release.password
            elif release.key_path:
                ssh_credentials["SSH_PRIVATE_KEY_PATH"] = release.key_path
            else:
                print(
                    "  ❌ Missing required SSH credentials (need host, username, and password or private_key_path)"
                )
                broken.append(provider)
                continue
            success = _validate_ssh_credentials_real(ssh_credentials)
        elif provider == "huggingface":
            release = release_credential(
                CredentialTarget.fixed_service(
                    "huggingface.co",
                    why="the HuggingFace Hub API",
                ),
                provider="huggingface",
            )
            if release.token:
                success = _validate_huggingface_credentials_real(
                    {"HF_TOKEN": release.token}
                )
            else:
                print("  ❌ Missing required HuggingFace credentials (need token)")
                broken.append(provider)
                continue
        else:
            success = True  # Unknown provider, assume valid

        if success:
            working.append(provider)
            print(f"  ✅ {provider.upper()} credentials valid")
        else:
            broken.append(provider)
            print(f"  ❌ {provider.upper()} credentials invalid or inaccessible")

    if not working and not broken:
        print("\nNo credentials are configured; run `clustrix credentials setup`.")
    return bool(working) and not broken


def edit_credentials_command():
    """Open the .env file in the default editor."""
    manager = get_credential_manager()

    if not manager.env_file.exists():
        print("📝 Creating new .env file...")
        manager._ensure_setup()

    # ``$EDITOR`` is a command line, not a program name: ``code -w`` and
    # ``emacsclient -t`` are ordinary values, and handing the whole string to
    # exec as one word reported "editor not found" for an editor that exists.
    candidates = [shlex.split(os.getenv("EDITOR", ""))]
    candidates += [[fallback] for fallback in ("nano", "code", "vim", "emacs")]
    for command in candidates:
        if not command:
            continue
        try:
            subprocess.run([*command, str(manager.env_file)])
            return
        except FileNotFoundError:
            continue
    print(f"📝 Please edit the credential file manually: {manager.env_file}")


def reset_credentials_command():
    """Reset credentials by recreating the .env template."""
    manager = get_credential_manager()

    if manager.env_file.exists():
        if not click.confirm(f"Reset credential file {manager.env_file}?"):
            print("Reset cancelled.")
            return

    try:
        # Remove existing file and recreate template
        if manager.env_file.exists():
            manager.env_file.unlink()

        manager._create_env_template()
        print(f"✅ Credential template recreated: {manager.env_file}")
        print("💡 Run 'clustrix credentials setup' to configure credentials")

    except Exception as e:
        print(f"❌ Failed to reset credentials: {e}")


def migrate_credentials_command():
    """Legacy migration command - 1Password support has been removed."""
    print("🔄 1Password Migration")
    print("=" * 50)
    print("❌ 1Password support has been removed from Clustrix.")
    print("")
    print("Please use one of these methods to set up credentials:")
    print("  • 'clustrix credentials setup' - Interactive credential setup")
    print("  • Edit ~/.clustrix/.env manually")
    print("  • Use environment variables")
    print("  • Use GitHub Actions secrets")
    print("")
    print("For more information, run 'clustrix credentials --help'")
