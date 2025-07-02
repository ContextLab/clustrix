import click
import getpass

from .config import configure, load_config, save_config, get_config, ClusterConfig
from .executor import ClusterExecutor
from .ssh_utils import setup_ssh_keys, detect_working_ssh_key


@click.group()
def cli():
    """Clustrix CLI - Distributed computing for Python functions."""
    pass


@cli.command()
@click.option(
    "--cluster-type",
    type=click.Choice(["slurm", "pbs", "sge", "kubernetes", "ssh", "local"]),
    help="Type of cluster scheduler",
)
@click.option("--cluster-host", help="Cluster hostname")
@click.option("--username", help="Username for cluster access")
@click.option("--api-key", help="API key for authentication")
@click.option("--cores", type=int, help="Default number of cores")
@click.option("--memory", help="Default memory allocation (e.g., 8GB)")
@click.option("--time", help="Default time allocation (e.g., 04:00:00)")
@click.option("--config-file", type=click.Path(), help="Save configuration to file")
def config(
    cluster_type,
    cluster_host,
    username,
    api_key,
    cores,
    memory,
    time,
    config_file,
):
    """Configure Clustrix settings."""

    config_updates = {}

    if cluster_type:
        config_updates["cluster_type"] = cluster_type
    if cluster_host:
        config_updates["cluster_host"] = cluster_host
    if username:
        config_updates["username"] = username
    if api_key:
        config_updates["api_key"] = api_key
    if cores:
        config_updates["default_cores"] = cores
    if memory:
        config_updates["default_memory"] = memory
    if time:
        config_updates["default_time"] = time

    if config_updates:
        configure(**config_updates)
        click.echo("Configuration updated successfully!")

        if config_file:
            save_config(config_file)
            click.echo(f"Configuration saved to {config_file}")
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
    """Load configuration from file."""
    try:
        load_config(config_file)
        click.echo(f"Configuration loaded from {config_file}")
    except FileNotFoundError:
        click.echo("Error: File not found", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error loading configuration: {e}", err=True)
        raise SystemExit(1)


@cli.command()
def status():
    """Show cluster status and active jobs."""
    current_config = get_config()

    if not current_config.cluster_host:
        click.echo("No cluster configured")
        return

    click.echo("Cluster Status:")
    click.echo(f"  Type: {current_config.cluster_type}")
    click.echo(f"  Host: {current_config.cluster_host}")
    click.echo(f"  User: {current_config.username}")

    # Try to connect and get status
    try:
        executor = ClusterExecutor(current_config)
        executor.connect()
        click.echo("  Connection: ✓ Connected")
        executor.disconnect()
    except Exception as e:
        click.echo("  Connection: ✗ Failed")
        click.echo(f"  Error: {e}")


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


if __name__ == "__main__":
    cli()
