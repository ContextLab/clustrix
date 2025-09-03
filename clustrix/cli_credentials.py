"""CLI tools for interactive credential setup and management.

This module provides command-line tools for setting up and managing Clustrix credentials,
including interactive setup, validation, and migration from existing systems.
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Dict
import logging

try:
    import click

    HAS_CLICK = True
except ImportError:
    HAS_CLICK = False

from .credential_manager import FlexibleCredentialManager, get_credential_manager

logger = logging.getLogger(__name__)


def setup_credentials_interactive():
    """Interactive credential setup wizard that creates and populates .env file."""
    if not HAS_CLICK:
        print(
            "❌ Interactive setup requires 'click' package. Install with: pip install click"
        )
        return False

    print("🔐 Clustrix Credential Setup Wizard")
    print("=" * 50)

    manager = FlexibleCredentialManager()

    print(f"📁 Configuration directory: {manager.config_dir}")
    print(f"📄 Credential file: {manager.env_file}")

    if manager.env_file.exists():
        print("📋 Existing .env file found")
        if not click.confirm("Do you want to update existing credentials?"):
            print("Setup cancelled.")
            return False

    # Collect credentials for each provider
    credentials_to_add = {}

    print("\n🔧 Select providers to configure:")

    if click.confirm("Configure AWS credentials (for EC2, Batch, pricing APIs)?"):
        aws_creds = _collect_aws_credentials_interactive()
        if aws_creds:
            credentials_to_add.update(aws_creds)

    if click.confirm("Configure SSH cluster access (for SLURM, PBS, SGE)?"):
        ssh_creds = _collect_ssh_credentials_interactive()
        if ssh_creds:
            credentials_to_add.update(ssh_creds)

    if click.confirm("Configure Azure credentials (for Azure VM, ACI)?"):
        azure_creds = _collect_azure_credentials_interactive()
        if azure_creds:
            credentials_to_add.update(azure_creds)

    if click.confirm(
        "Configure Google Cloud credentials (for GCP Compute, pricing APIs)?"
    ):
        gcp_creds = _collect_gcp_credentials_interactive()
        if gcp_creds:
            credentials_to_add.update(gcp_creds)

    if click.confirm("Configure Kubernetes credentials (for K8s job execution)?"):
        k8s_creds = _collect_kubernetes_credentials_interactive()
        if k8s_creds:
            credentials_to_add.update(k8s_creds)

    if click.confirm("Configure HuggingFace credentials (for HF Spaces)?"):
        hf_creds = _collect_huggingface_credentials_interactive()
        if hf_creds:
            credentials_to_add.update(hf_creds)

    if click.confirm("Configure Lambda Cloud credentials (for GPU instances)?"):
        lambda_creds = _collect_lambda_cloud_credentials_interactive()
        if lambda_creds:
            credentials_to_add.update(lambda_creds)

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


def _collect_aws_credentials_interactive() -> Dict[str, str]:
    """Collect and validate AWS credentials interactively."""
    print("\n🔑 AWS Credential Setup")
    print("You can find these in the AWS Console > IAM > Access Keys")

    try:
        access_key = click.prompt("AWS Access Key ID", type=str)
        secret_key = click.prompt("AWS Secret Access Key", type=str, hide_input=True)
        region = click.prompt("AWS Region", default="us-east-1", type=str)

        # Validate credentials with real AWS API call
        print("🔍 Validating AWS credentials...")
        if _validate_aws_credentials_real(access_key, secret_key, region):
            print("✅ AWS credentials validated successfully")
            return {
                "AWS_ACCESS_KEY_ID": access_key,
                "AWS_SECRET_ACCESS_KEY": secret_key,
                "AWS_REGION": region,
            }
        else:
            print("❌ AWS credential validation failed")
            return {}
    except click.Abort:
        print("AWS credential setup cancelled")
        return {}


def _collect_ssh_credentials_interactive() -> Dict[str, str]:
    """Collect and validate SSH credentials interactively."""
    print("\n🔑 SSH Cluster Credential Setup")
    print("For accessing SLURM, PBS, or SGE clusters via SSH")

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


def _collect_azure_credentials_interactive() -> Dict[str, str]:
    """Collect Azure credentials interactively."""
    print("\n🔑 Azure Credential Setup")
    print("You can find these in Azure Portal > App Registrations")

    try:
        subscription_id = click.prompt("Azure Subscription ID", type=str)
        tenant_id = click.prompt("Azure Tenant ID", type=str)
        client_id = click.prompt("Azure Client ID (Application ID)", type=str)
        client_secret = click.prompt("Azure Client Secret", type=str, hide_input=True)

        credentials = {
            "AZURE_SUBSCRIPTION_ID": subscription_id,
            "AZURE_TENANT_ID": tenant_id,
            "AZURE_CLIENT_ID": client_id,
            "AZURE_CLIENT_SECRET": client_secret,
        }

        # Validate credentials
        print("🔍 Validating Azure credentials...")
        if _validate_azure_credentials_real(credentials):
            print("✅ Azure credentials validated successfully")
            return credentials
        else:
            print("❌ Azure credential validation failed")
            return {}
    except click.Abort:
        print("Azure credential setup cancelled")
        return {}


def _collect_gcp_credentials_interactive() -> Dict[str, str]:
    """Collect GCP credentials interactively."""
    print("\n🔑 Google Cloud Credential Setup")

    try:
        project_id = click.prompt("GCP Project ID", type=str)

        auth_method = click.prompt(
            "Authentication method",
            type=click.Choice(["service_account_file", "service_account_json"]),
            default="service_account_file",
        )

        credentials = {"GCP_PROJECT_ID": project_id}

        if auth_method == "service_account_file":
            key_path = click.prompt("Service Account Key File Path", type=str)
            if Path(key_path).exists():
                credentials["GOOGLE_APPLICATION_CREDENTIALS"] = key_path
            else:
                print(f"❌ Service account key file not found at {key_path}")
                return {}
        else:
            print("Paste your service account JSON (Ctrl+D when done):")
            json_content = sys.stdin.read().strip()
            if json_content:
                credentials["GCP_SERVICE_ACCOUNT_JSON"] = json_content
            else:
                print("❌ No service account JSON provided")
                return {}

        # Validate credentials
        print("🔍 Validating GCP credentials...")
        if _validate_gcp_credentials_real(credentials):
            print("✅ GCP credentials validated successfully")
            return credentials
        else:
            print("❌ GCP credential validation failed")
            return {}
    except click.Abort:
        print("GCP credential setup cancelled")
        return {}


def _collect_kubernetes_credentials_interactive() -> Dict[str, str]:
    """Collect Kubernetes credentials interactively."""
    print("\n🔑 Kubernetes Credential Setup")

    try:
        default_kubeconfig = str(Path.home() / ".kube" / "config")
        kubeconfig_path = click.prompt(
            "Kubeconfig file path", default=default_kubeconfig, type=str
        )

        if not Path(kubeconfig_path).exists():
            print(f"❌ Kubeconfig file not found at {kubeconfig_path}")
            return {}

        credentials = {"KUBECONFIG": kubeconfig_path}

        namespace = click.prompt("Kubernetes namespace", default="default", type=str)
        credentials["K8S_NAMESPACE"] = namespace

        context = click.prompt("Kubernetes context (optional)", default="", type=str)
        if context:
            credentials["K8S_CONTEXT"] = context

        # Validate Kubernetes access
        print("🔍 Validating Kubernetes access...")
        if _validate_kubernetes_credentials_real(credentials):
            print("✅ Kubernetes credentials validated successfully")
            return credentials
        else:
            print("❌ Kubernetes credential validation failed")
            return {}
    except click.Abort:
        print("Kubernetes credential setup cancelled")
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


def _collect_lambda_cloud_credentials_interactive() -> Dict[str, str]:
    """Collect Lambda Cloud credentials interactively."""
    print("\n🔑 Lambda Cloud Credential Setup")
    print("You can find your API key in Lambda Cloud console")

    try:
        api_key = click.prompt("Lambda Cloud API Key", type=str, hide_input=True)

        credentials = {
            "LAMBDA_CLOUD_API_KEY": api_key,
            "LAMBDA_CLOUD_ENDPOINT": "https://cloud.lambdalabs.com/api/v1",
        }

        # Validate credentials
        print("🔍 Validating Lambda Cloud credentials...")
        if _validate_lambda_cloud_credentials_real(credentials):
            print("✅ Lambda Cloud credentials validated successfully")
            return credentials
        else:
            print("❌ Lambda Cloud credential validation failed")
            return {}
    except click.Abort:
        print("Lambda Cloud credential setup cancelled")
        return {}


# Real credential validation functions (NO MOCKS)


def _validate_aws_credentials_real(
    access_key: str, secret_key: str, region: str
) -> bool:
    """Validate AWS credentials using real AWS STS API call."""
    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError

        # Create STS client with provided credentials
        sts_client = boto3.client(
            "sts",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

        # Make real API call to get caller identity
        response = sts_client.get_caller_identity()

        # If we get here, credentials are valid
        logger.info(f"AWS credentials validated for account: {response.get('Account')}")
        return True

    except (ClientError, NoCredentialsError) as e:
        logger.debug(f"AWS credential validation failed: {e}")
        return False
    except ImportError:
        logger.warning("boto3 not available for AWS validation")
        return False  # Conservative: require validation
    except Exception as e:
        logger.debug(f"AWS validation error: {e}")
        return False


def _validate_ssh_credentials_real(credentials: Dict[str, str]) -> bool:
    """Validate SSH credentials using real SSH connection attempt."""
    try:
        import paramiko

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Prepare connection parameters with proper types
        hostname = credentials["SSH_HOST"]
        username = credentials["SSH_USERNAME"]
        port = int(credentials.get("SSH_PORT", 22))
        timeout = 10

        # Make real SSH connection with proper parameter types
        if "SSH_PASSWORD" in credentials:
            password = credentials["SSH_PASSWORD"]
            ssh.connect(
                hostname=hostname,
                username=username,
                port=port,
                password=password,
                timeout=timeout,
            )
        elif "SSH_PRIVATE_KEY_PATH" in credentials:
            key_filename = credentials["SSH_PRIVATE_KEY_PATH"]
            ssh.connect(
                hostname=hostname,
                username=username,
                port=port,
                key_filename=key_filename,
                timeout=timeout,
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


def _validate_azure_credentials_real(credentials: Dict[str, str]) -> bool:
    """Validate Azure credentials using real Azure API call."""
    try:
        from azure.identity import ClientSecretCredential
        from azure.mgmt.resource import ResourceManagementClient
        from azure.core.exceptions import ClientAuthenticationError

        # Create credential object
        credential = ClientSecretCredential(
            tenant_id=credentials["AZURE_TENANT_ID"],
            client_id=credentials["AZURE_CLIENT_ID"],
            client_secret=credentials["AZURE_CLIENT_SECRET"],
        )

        # Create resource management client
        resource_client = ResourceManagementClient(
            credential, credentials["AZURE_SUBSCRIPTION_ID"]
        )

        # Make real API call to list resource groups (validates credentials)
        list(resource_client.resource_groups.list())

        logger.info(
            f"Azure credentials validated for subscription: {credentials['AZURE_SUBSCRIPTION_ID']}"
        )
        return True

    except ClientAuthenticationError as e:
        logger.debug(f"Azure authentication failed: {e}")
        return False
    except ImportError:
        logger.warning("Azure SDK not available for Azure validation")
        return False  # Conservative: require validation
    except Exception as e:
        logger.debug(f"Azure validation error: {e}")
        return False


def _validate_gcp_credentials_real(credentials: Dict[str, str]) -> bool:
    """Validate GCP credentials using real Google Cloud API call."""
    try:
        # Try different GCP client libraries
        try:
            from google.cloud import resource_manager

            client_type = "resource_manager"
        except ImportError:
            try:
                from google.cloud import compute_v1

                client_type = "compute"
            except ImportError:
                logger.warning("Google Cloud SDK not available for GCP validation")
                return False

        from google.auth.exceptions import DefaultCredentialsError
        import tempfile

        # Set up authentication
        if "service_account_json" in credentials:
            # Use service account JSON
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, suffix=".json"
            ) as f:
                f.write(credentials["service_account_json"])
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name
        elif "service_account_path" in credentials:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials[
                "service_account_path"
            ]
        else:
            return False

        # Make real API call based on available client
        if client_type == "resource_manager":
            client = resource_manager.Client()
            client.fetch_project(credentials["project_id"])  # Validates credentials
        elif client_type == "compute":
            client = compute_v1.ZonesClient()
            list(
                client.list(project=credentials["project_id"])
            )  # Validates credentials

        logger.info(
            f"GCP credentials validated for project: {credentials['project_id']}"
        )
        return True

    except DefaultCredentialsError as e:
        logger.debug(f"GCP authentication failed: {e}")
        return False
    except ImportError:
        logger.warning("Google Cloud SDK not available for GCP validation")
        return False  # Conservative: require validation
    except Exception as e:
        logger.debug(f"GCP validation error: {e}")
        return False


def _validate_kubernetes_credentials_real(credentials: Dict[str, str]) -> bool:
    """Validate Kubernetes credentials using real kubectl command."""
    try:
        # Set KUBECONFIG environment variable
        env = os.environ.copy()
        env["KUBECONFIG"] = credentials["KUBECONFIG"]

        # Test kubectl access with real command
        cmd = ["kubectl", "get", "namespaces"]
        if "K8S_CONTEXT" in credentials:
            cmd.extend(["--context", credentials["K8S_CONTEXT"]])

        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=10
        )

        success = result.returncode == 0
        if success:
            logger.info("Kubernetes credentials validated")
        return success

    except subprocess.TimeoutExpired:
        logger.debug("Kubernetes validation timed out")
        return False
    except FileNotFoundError:
        logger.warning("kubectl not available for Kubernetes validation")
        return False  # Conservative: require validation
    except Exception as e:
        logger.debug(f"Kubernetes validation error: {e}")
        return False


def _validate_huggingface_credentials_real(credentials: Dict[str, str]) -> bool:
    """Validate HuggingFace credentials using real HF API call."""
    try:
        from huggingface_hub import HfApi

        # from huggingface_hub.utils import RepositoryNotFoundError  # Currently unused

        # Create HF API client
        api = HfApi(token=credentials["HF_TOKEN"])

        # Make real API call to get user info
        user_info = api.whoami()

        logger.info(
            f"HuggingFace credentials validated for user: {user_info.get('name', 'unknown')}"
        )
        return True

    except Exception as e:
        logger.debug(f"HuggingFace validation error: {e}")
        return False


def _validate_lambda_cloud_credentials_real(credentials: Dict[str, str]) -> bool:
    """Validate Lambda Cloud credentials using real API call."""
    try:
        import requests

        # Make real API call to Lambda Cloud
        headers = {
            "Authorization": f"Bearer {credentials['LAMBDA_CLOUD_API_KEY']}",
            "Content-Type": "application/json",
        }

        response = requests.get(
            f"{credentials['LAMBDA_CLOUD_ENDPOINT']}/instance-types",
            headers=headers,
            timeout=10,
        )

        success = response.status_code == 200
        if success:
            logger.info("Lambda Cloud credentials validated")
        return success

    except Exception as e:
        logger.debug(f"Lambda Cloud validation error: {e}")
        return False


def _write_credentials_to_env_file(env_file: Path, credentials: Dict[str, str]) -> bool:
    """Write credentials to .env file with atomic operation and secure permissions."""
    try:
        # Read existing content to preserve comments and structure
        existing_content = ""
        if env_file.exists():
            existing_content = env_file.read_text()

        # Merge new credentials with existing content
        updated_content = _merge_env_content(existing_content, credentials)

        # Write with atomic operation
        temp_file = env_file.with_suffix(".tmp")
        temp_file.write_text(updated_content)
        temp_file.chmod(0o600)  # Secure permissions

        # Atomic replacement
        temp_file.replace(env_file)

        logger.info(f"Credentials written to {env_file}")
        return True

    except Exception as e:
        logger.error(f"Failed to write credentials: {e}")
        return False


def _merge_env_content(existing_content: str, new_credentials: Dict[str, str]) -> str:
    """Merge new credentials with existing .env content, preserving comments."""
    lines = existing_content.split("\n") if existing_content else []

    # Create a map of existing variables
    existing_vars = set()
    for i, line in enumerate(lines):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            existing_vars.add(key)
        elif line and line.startswith("#") and "=" in line[1:]:
            # Commented variable - uncomment if we have a value
            key = line[1:].split("=", 1)[0].strip()
            if key in new_credentials:
                lines[i] = f"{key}={new_credentials[key]}"
                existing_vars.add(key)

    # Add new credentials that don't exist yet
    for key, value in new_credentials.items():
        if key not in existing_vars:
            lines.append(f"{key}={value}")

    return "\n".join(lines)


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


def test_credentials_command():
    """Test all configured credentials by attempting real API calls."""
    manager = get_credential_manager()

    print("🧪 Testing Clustrix Credentials")
    print("=" * 50)

    # Test each provider
    providers_to_test = [
        "aws",
        "azure",
        "gcp",
        "ssh",
        "kubernetes",
        "huggingface",
        "lambda_cloud",
    ]

    for provider in providers_to_test:
        print(f"\n🔍 Testing {provider.upper()} credentials...")

        credentials = manager.ensure_credential(provider)
        if not credentials:
            print("  ❌ No credentials found")
            continue

        # Test with real validation
        if provider == "aws":
            required_keys = ["access_key_id", "secret_access_key"]
            if all(key in credentials for key in required_keys):
                success = _validate_aws_credentials_real(
                    credentials["access_key_id"],
                    credentials["secret_access_key"],
                    credentials.get("region", "us-east-1"),
                )
            else:
                print(
                    f"  ❌ Missing required AWS credentials: {[k for k in required_keys if k not in credentials]}"
                )
                continue
        elif provider == "azure":
            required_keys = [
                "subscription_id",
                "tenant_id",
                "client_id",
                "client_secret",
            ]
            if all(key in credentials for key in required_keys):
                success = _validate_azure_credentials_real(credentials)
            else:
                print(
                    f"  ❌ Missing required Azure credentials: {[k for k in required_keys if k not in credentials]}"
                )
                continue
        elif provider == "gcp":
            if "project_id" in credentials and (
                "service_account_path" in credentials
                or "service_account_json" in credentials
            ):
                success = _validate_gcp_credentials_real(credentials)
            else:
                print(
                    "  ❌ Missing required GCP credentials (need project_id + service account)"
                )
                continue
        elif provider == "ssh":
            required_keys = ["host", "username"]
            if all(key in credentials for key in required_keys) and (
                "password" in credentials or "private_key_path" in credentials
            ):
                success = _validate_ssh_credentials_real(credentials)
            else:
                print(
                    "  ❌ Missing required SSH credentials (need host, username, and password or private_key_path)"
                )
                continue
        elif provider == "kubernetes":
            if "kubeconfig_path" in credentials or "kubeconfig_content" in credentials:
                success = _validate_kubernetes_credentials_real(credentials)
            else:
                print("  ❌ Missing required Kubernetes credentials (need kubeconfig)")
                continue
        elif provider == "huggingface":
            if "token" in credentials:
                success = _validate_huggingface_credentials_real(credentials)
            else:
                print("  ❌ Missing required HuggingFace credentials (need token)")
                continue
        elif provider == "lambda_cloud":
            if "api_key" in credentials:
                success = _validate_lambda_cloud_credentials_real(credentials)
            else:
                print("  ❌ Missing required Lambda Cloud credentials (need api_key)")
                continue
        else:
            success = True  # Unknown provider, assume valid

        if success:
            print(f"  ✅ {provider.upper()} credentials valid")
        else:
            print(f"  ❌ {provider.upper()} credentials invalid or inaccessible")


def edit_credentials_command():
    """Open the .env file in the default editor."""
    manager = get_credential_manager()

    if not manager.env_file.exists():
        print("📝 Creating new .env file...")
        manager._ensure_setup()

    # Try to open in default editor
    try:
        editor = os.getenv("EDITOR", "nano")  # Default to nano
        subprocess.run([editor, str(manager.env_file)])
    except FileNotFoundError:
        try:
            # Try common editors
            for editor in ["code", "vim", "nano", "emacs"]:
                try:
                    subprocess.run([editor, str(manager.env_file)])
                    break
                except FileNotFoundError:
                    continue
            else:
                print(
                    f"📝 Please edit the credential file manually: {manager.env_file}"
                )
        except Exception:
            print(f"📝 Please edit the credential file manually: {manager.env_file}")


def reset_credentials_command():
    """Reset credentials by recreating the .env template."""
    manager = get_credential_manager()

    if manager.env_file.exists():
        if HAS_CLICK and not click.confirm(
            f"Reset credential file {manager.env_file}?"
        ):
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
