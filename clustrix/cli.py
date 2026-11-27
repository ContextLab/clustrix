import click
import getpass
from pathlib import Path

from .config import (
    ConfigFileError,
    _read_config_bundle,
    configure,
    default_config_file,
    load_config,
    save_config,
    get_config,
    ClusterConfig,
    SUPPORTED_CLUSTER_TYPES,
)
from .executor import ClusterExecutor
from .profile_manager import _mkdir_private
from .ssh_utils import setup_ssh_keys, detect_working_ssh_key
from .cli_credentials import (
    setup_credentials_interactive,
    list_credentials_command,
    test_credentials_command,
    edit_credentials_command,
    reset_credentials_command,
    migrate_credentials_command,
)


class _CleanErrorGroup(click.Group):
    """Report an unusable configuration file as an error, not a traceback.

    Every command reads the configuration, and a configuration file that was
    found but cannot be loaded raises ``ConfigFileError`` on first use. The
    message already names the file and what to do about it; this is the one
    place it is turned into ``Error: ...`` and exit status 1.
    """

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except ConfigFileError as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(cls=_CleanErrorGroup)
def cli():
    """Clustrix CLI - Distributed computing for Python functions."""
    pass


def _save_configuration(config_file=None) -> Path:
    """Write the live configuration where the next process will find it.

    ``config_file`` is the path the user named, if they named one; otherwise
    it is the per-user configuration file, which is what makes
    ``clustrix config --cluster-host ...`` followed by ``clustrix status``
    work at all -- each is a separate process, and until this existed the
    first one changed nothing but its own memory. Secrets are left out and
    the file is owner-only: both are ``ClusterConfig.save_to_file``'s rules.
    """
    if config_file is not None:
        destination = Path(config_file).expanduser()
    else:
        destination = default_config_file()
        if _read_config_bundle(destination) is not None:
            raise click.ClickException(
                f"{destination} holds named profiles saved by the notebook "
                f"widget, and saving here would replace all of them. Nothing "
                f"was changed. Move that file aside, or name another one "
                f"with --config-file and load it with 'clustrix load'."
            )
        _mkdir_private(destination.parent)
    try:
        save_config(str(destination))
    except OSError as exc:
        raise click.ClickException(
            f"Could not write {destination}: {exc}. Nothing was saved."
        ) from exc
    return destination


@cli.command()
@click.option(
    "--cluster-type",
    type=click.Choice(list(SUPPORTED_CLUSTER_TYPES)),
    help="Type of cluster scheduler",
)
@click.option("--cluster-host", help="Cluster hostname")
@click.option("--username", help="Username for cluster access")
@click.option(
    "--cores", type=int, help="Default number of cores (-1 for every core available)"
)
@click.option("--memory", help="Default memory allocation (e.g., 8GB)")
@click.option("--time", help="Default time allocation (e.g., 04:00:00)")
@click.option(
    "--config-file",
    type=click.Path(),
    help="Save to this file instead of the per-user configuration file",
)
def config(
    cluster_type,
    cluster_host,
    username,
    cores,
    memory,
    time,
    config_file,
):
    """Show Clustrix settings, or change them and save the change."""

    # ``is not None``, not truthiness: ``--cores 0`` is a value the user
    # typed, and it has to reach the validation that refuses it rather than
    # being mistaken for the option not having been given.
    config_updates = {
        name: value
        for name, value in (
            ("cluster_type", cluster_type),
            ("cluster_host", cluster_host),
            ("username", username),
            ("default_cores", cores),
            ("default_memory", memory),
            ("default_time", time),
        )
        if value is not None
    }

    if config_updates or config_file:
        try:
            configure(**config_updates)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        destination = _save_configuration(config_file)
        click.echo("Configuration updated successfully!")
        click.echo(f"Configuration saved to {destination}")
        if config_file:
            click.echo(
                f"It is not read automatically from there; use "
                f"'clustrix load {config_file}' to make it the default."
            )
    else:
        # Display current configuration
        current_config = get_config()
        click.echo("Current Clustrix Configuration:")
        click.echo(f"  cluster_type: {current_config.cluster_type}")
        click.echo(f"  cluster_host: {current_config.cluster_host}")
        click.echo(f"  username: {current_config.username}")
        click.echo(f"  default_cores: {current_config.default_cores}")
        click.echo(f"  default_memory: {current_config.default_memory}")
        click.echo(f"  default_time: {current_config.default_time}")


@cli.command()
@click.argument("config_file", type=click.Path())
def load(config_file):
    """Load configuration from a file and make it the default."""
    try:
        load_config(config_file)
    except FileNotFoundError:
        click.echo("Error: File not found", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error loading configuration: {e}", err=True)
        raise SystemExit(1)
    destination = _save_configuration()
    click.echo(f"Configuration loaded from {config_file}")
    click.echo(f"Configuration saved to {destination}")


@cli.command()
def status():
    """Show cluster status; exits non-zero when the cluster is unreachable."""
    current_config = get_config()

    if not current_config.cluster_host:
        click.echo("No cluster configured")
        return

    click.echo("Cluster Status:")
    click.echo(f"  Type: {current_config.cluster_type}")
    click.echo(f"  Host: {current_config.cluster_host}")
    click.echo(f"  User: {current_config.username}")

    # Try to connect and get status
    executor = None
    try:
        executor = ClusterExecutor(current_config)
        executor.connect()
        click.echo("  Connection: ✓ Connected")
    except Exception as e:
        click.echo("  Connection: ✗ Failed")
        click.echo(f"  Error: {e}")
        # A script asking "is the cluster up?" reads the exit status. This
        # used to be 0 either way.
        raise SystemExit(1)
    finally:
        if executor is not None:
            executor.disconnect()


@cli.command(name="ssh-setup")
@click.option("--host", required=True, help="Cluster hostname")
@click.option("--user", required=True, help="Username for cluster access")
@click.option("--port", default=22, help="SSH port (default: 22)")
@click.option("--alias", help="Alias name for SSH config entry")
@click.option(
    "--key-type",
    default="ed25519",
    type=click.Choice(["ed25519", "rsa"]),
    help="SSH key type",
)
@click.option("--force-refresh", is_flag=True, help="Force generation of new SSH keys")
def ssh_setup(host, user, port, alias, key_type, force_refresh):
    """Set up SSH keys for cluster authentication."""

    click.echo(f"🔐 Setting up SSH keys for {host}")
    click.echo(f"   Username: {user}")
    click.echo(f"   Port: {port}")
    if alias:
        click.echo(f"   Alias: {alias}")
    if force_refresh:
        click.echo("   🔄 Force refresh enabled")
    click.echo()

    # Create cluster config
    config = ClusterConfig(
        cluster_type="ssh",  # Default to SSH type for CLI
        cluster_host=host,
        username=user,
        cluster_port=port,
    )

    # Check for existing keys first (unless force refresh)
    if not force_refresh:
        click.echo("🔍 Checking for existing SSH keys...")
        existing_key = detect_working_ssh_key(host, user, port)

        if existing_key:
            click.echo(f"✅ Found working SSH key: {existing_key}")
            click.echo("   No action needed. Use --force-refresh to generate new keys.")
            return
        else:
            click.echo("ℹ️  No existing working SSH keys found")

    # Get password securely
    click.echo("🔐 Enter your SSH password for initial authentication:")
    password = getpass.getpass("Password: ")

    if not password:
        click.echo("❌ Password is required for SSH key setup")
        raise SystemExit(1)

    click.echo()
    click.echo("🔧 Generating and deploying SSH keys...")

    try:
        # Setup SSH keys
        result = setup_ssh_keys(
            config,
            password=password,
            cluster_alias=alias,
            key_type=key_type,
            force_refresh=force_refresh,
        )

        # Clear password from memory
        password = None

        # Report results
        if result["success"]:
            click.echo("✅ SSH key setup completed successfully!")
            click.echo(f"   Private key: {result['key_path']}")
            click.echo(f"   Public key: {result['key_path']}.pub")

            if result["key_already_existed"]:
                click.echo("   Using existing working SSH key")
            else:
                click.echo("   Generated new SSH key")

            if result["key_deployed"]:
                click.echo("   Public key deployed to remote server")

            if result["connection_tested"]:
                click.echo("   Connection test successful")
            elif "connection_test_warning" in result["details"]:
                click.echo("   ⚠️  Connection test failed but key was deployed")
                click.echo("   The key may need time to propagate")

            if alias:
                click.echo(f"   SSH config updated for alias: {alias}")

            click.echo()
            click.echo("🎉 You can now use passwordless SSH authentication!")

        else:
            click.echo(f"❌ SSH key setup failed: {result['error']}")
            if result["details"]:
                click.echo("   Details:")
                for key, value in result["details"].items():
                    click.echo(f"     {key}: {value}")
            raise SystemExit(1)

    except Exception as e:
        click.echo(f"❌ Error during SSH key setup: {e}")
        raise SystemExit(1)


# Credential management commands
@cli.group()
def credentials():
    """Manage Clustrix credentials."""
    pass


@credentials.command("setup")
def credentials_setup():
    """Interactive credential setup wizard."""
    setup_credentials_interactive()


@credentials.command("list")
def credentials_list():
    """List available credentials (values masked)."""
    list_credentials_command()


@credentials.command("test")
def credentials_test():
    """Test all configured credentials; exit 1 unless every one works."""
    if not test_credentials_command():
        raise SystemExit(1)


@credentials.command("edit")
def credentials_edit():
    """Open credential file in editor."""
    edit_credentials_command()


@credentials.command("reset")
def credentials_reset():
    """Reset credential file to template."""
    reset_credentials_command()


@credentials.command("migrate")
def credentials_migrate():
    """Legacy migration command - 1Password support removed."""
    migrate_credentials_command()


if __name__ == "__main__":
    cli()
