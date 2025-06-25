import click
import json
from pathlib import Path

from .config import configure, load_config, save_config, get_config


@click.group()
def cli():
    """Clustrix - Distributed computing for Python functions."""
    pass


@cli.command()
@click.option(
    "--cluster-type",
    type=click.Choice(["slurm", "pbs", "sge", "kubernetes", "ssh"]),
    help="Type of cluster scheduler",
)
@click.option("--cluster-host", help="Cluster hostname")
@click.option("--username", help="Username for cluster access")
@click.option("--api-key", help="API key for authentication")
@click.option("--cores", type=int, help="Default number of cores")
@click.option("--memory", help="Default memory allocation (e.g., 8GB)")
@click.option("--config-file", type=click.Path(), help="Save configuration to file")
def config(cluster_type, cluster_host, username, api_key, cores, memory, config_file):
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

    if config_updates:
        configure(**config_updates)
        click.echo("Configuration updated successfully.")

        if config_file:
            save_config(config_file)
            click.echo(f"Configuration saved to {config_file}")
    else:
        # Display current configuration
        current_config = get_config()
        click.echo("Current configuration:")
        click.echo(json.dumps(current_config.__dict__, indent=2, default=str))


@cli.command()
@click.argument("config_file", type=click.Path(exists=True))
def load(config_file):
    """Load configuration from file."""
    try:
        load_config(config_file)
        click.echo(f"Configuration loaded from {config_file}")
    except Exception as e:
        click.echo(f"Error loading configuration: {e}", err=True)


@cli.command()
def status():
    """Show cluster status and active jobs."""
    config = get_config()
    click.echo(f"Cluster type: {config.cluster_type}")
    click.echo(f"Cluster host: {config.cluster_host}")
    click.echo(f"Default cores: {config.default_cores}")
    click.echo(f"Default memory: {config.default_memory}")

    # TODO: Add job status checking
    click.echo("\nActive jobs: (feature coming soon)")


if __name__ == "__main__":
    cli()
