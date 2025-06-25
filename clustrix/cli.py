import click

from .config import configure, load_config, save_config, get_config
from .executor import ClusterExecutor


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
    cluster_type, cluster_host, username, api_key, cores, memory, time, config_file
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


if __name__ == "__main__":
    cli()
