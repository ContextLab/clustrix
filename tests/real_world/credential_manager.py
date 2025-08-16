"""
Credential management for real-world tests.

This module provides secure access to credentials for real-world testing,
supporting both 1Password (local development) and GitHub Actions secrets.
"""

import os
import logging
from typing import Dict, Optional, Any
from pathlib import Path

# Try to import SecureCredentialManager
try:
    from clustrix.secure_credentials import (
        SecureCredentialManager,
        ValidationCredentials,
    )

    HAS_SECURE_CREDENTIALS = True
except ImportError:
    HAS_SECURE_CREDENTIALS = False

logger = logging.getLogger(__name__)


class RealWorldCredentialManager:
    """Manages credentials for real-world testing with multiple fallback options."""

    def __init__(self):
        """Initialize credential manager."""
        self.is_github_actions = os.getenv("GITHUB_ACTIONS") == "true"
        self.is_local_development = not self.is_github_actions

        # Initialize 1Password manager if available
        self._op_manager = None
        self._validation_creds = None

        if HAS_SECURE_CREDENTIALS and self.is_local_development:
            try:
                self._op_manager = SecureCredentialManager()
                self._validation_creds = ValidationCredentials()
            except Exception as e:
                logger.debug(f"Failed to initialize 1Password manager: {e}")

    def is_1password_available(self) -> bool:
        """Check if 1Password CLI is available."""
        if not self._op_manager:
            return False
        return self._op_manager.is_op_available()

    def get_aws_credentials(self) -> Optional[Dict[str, str]]:
        """Get AWS credentials from available sources."""
        # Try 1Password first (local development)
        if self.is_local_development and self._validation_creds:
            try:
                aws_creds = self._validation_creds.get_aws_credentials()
                if aws_creds:
                    return {
                        "access_key_id": aws_creds.get("aws_access_key_id"),
                        "secret_access_key": aws_creds.get("aws_secret_access_key"),
                        "region": aws_creds.get("aws_region", "us-east-1"),
                    }
            except Exception as e:
                logger.debug(f"Failed to get AWS credentials from 1Password: {e}")

        # GitHub Actions: Use repository secrets
        if self.is_github_actions:
            access_key = os.getenv("AWS_ACCESS_KEY_ID")
            secret_key = os.getenv("AWS_ACCESS_KEY")  # GitHub secret name
            region = os.getenv("AWS_REGION", "us-east-1")

            if access_key and secret_key:
                return {
                    "access_key_id": access_key,
                    "secret_access_key": secret_key,
                    "region": region,
                }

        # Fall back to environment variables
        access_key = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("TEST_AWS_ACCESS_KEY")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv(
            "TEST_AWS_SECRET_KEY"
        )
        region = os.getenv("AWS_REGION") or os.getenv("TEST_AWS_REGION", "us-east-1")

        if access_key and secret_key:
            return {
                "access_key_id": access_key,
                "secret_access_key": secret_key,
                "region": region,
            }

        return None

    def get_azure_credentials(self) -> Optional[Dict[str, str]]:
        """Get Azure credentials from available sources."""
        # Try 1Password first (local development)
        if self.is_local_development and self._validation_creds:
            try:
                azure_creds = self._validation_creds.get_azure_credentials()
                if azure_creds:
                    return azure_creds
            except Exception as e:
                logger.debug(f"Failed to get Azure credentials from 1Password: {e}")

        # Fall back to environment variables
        subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID") or os.getenv(
            "TEST_AZURE_SUBSCRIPTION_ID"
        )
        tenant_id = os.getenv("AZURE_TENANT_ID") or os.getenv("TEST_AZURE_TENANT_ID")
        client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("TEST_AZURE_CLIENT_ID")
        client_secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv(
            "TEST_AZURE_CLIENT_SECRET"
        )

        if subscription_id:
            return {
                "subscription_id": subscription_id,
                "tenant_id": tenant_id,
                "client_id": client_id,
                "client_secret": client_secret,
            }

        return None

    def get_gcp_credentials(self) -> Optional[Dict[str, str]]:
        """Get GCP credentials from available sources."""
        # Try 1Password first (local development)
        if self.is_local_development and self._validation_creds:
            try:
                gcp_creds = self._validation_creds.get_gcp_credentials()
                if gcp_creds:
                    return gcp_creds
            except Exception as e:
                logger.debug(f"Failed to get GCP credentials from 1Password: {e}")

        # GitHub Actions: Use repository secrets
        if self.is_github_actions:
            project_id = os.getenv("GCP_PROJECT_ID")
            service_account_json = os.getenv("GCP_JSON")

            if project_id and service_account_json:
                return {
                    "project_id": project_id,
                    "service_account_json": service_account_json,
                }

        # Fall back to environment variables
        project_id = (
            os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCP_PROJECT_ID")
            or os.getenv("TEST_GCP_PROJECT_ID")
        )
        service_account_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv(
            "TEST_GCP_SERVICE_ACCOUNT_PATH"
        )
        service_account_json = os.getenv("GCP_JSON")

        if project_id:
            result = {"project_id": project_id}
            if service_account_json:
                result["service_account_json"] = service_account_json
            elif service_account_path:
                result["service_account_path"] = service_account_path
            return result

        return None

    def get_ssh_credentials(self) -> Optional[Dict[str, str]]:
        """Get SSH credentials from available sources."""
        # Try 1Password first (local development)
        if self.is_local_development and self._op_manager:
            try:
                # Try to get tensor01 credentials first (SSH-GPU)
                tensor01_notes = self._op_manager.get_credential(
                    "clustrix-ssh-gpu", "notesPlain"
                )
                if tensor01_notes:
                    return self._parse_notes_credentials(tensor01_notes)
            except Exception as e:
                logger.debug(f"Failed to get tensor01 credentials from 1Password: {e}")

        # GitHub Actions: Use repository secrets
        if self.is_github_actions:
            username = os.getenv("CLUSTRIX_USERNAME")
            password = os.getenv("CLUSTRIX_PASSWORD")

            if username and password:
                return {
                    "host": "tensor01.dartmouth.edu",  # Default to tensor01 for GitHub Actions
                    "username": username,
                    "password": password,
                    "port": "22",
                }

        # Fall back to environment variables
        host = os.getenv("TEST_SSH_HOST", "localhost")
        username = os.getenv("TEST_SSH_USERNAME", os.getenv("USER"))
        password = os.getenv("TEST_SSH_PASSWORD")
        private_key_path = os.getenv("TEST_SSH_PRIVATE_KEY_PATH")
        port = os.getenv("TEST_SSH_PORT", "22")

        return {
            "host": host,
            "username": username,
            "password": password,
            "private_key_path": private_key_path,
            "port": port,
        }

    def get_tensor01_credentials(self) -> Optional[Dict[str, str]]:
        """Get tensor01 (SSH-GPU) credentials from available sources."""
        # Try 1Password first (local development)
        if self.is_local_development and self._op_manager:
            try:
                tensor01_notes = self._op_manager.get_credential(
                    "clustrix-ssh-gpu", "notesPlain"
                )
                if tensor01_notes:
                    return self._parse_notes_credentials(tensor01_notes)
            except Exception as e:
                logger.debug(f"Failed to get tensor01 credentials from 1Password: {e}")

        # GitHub Actions: Use repository secrets
        if self.is_github_actions:
            username = os.getenv("CLUSTRIX_USERNAME")
            password = os.getenv("CLUSTRIX_PASSWORD")

            if username and password:
                return {
                    "host": "tensor01.dartmouth.edu",
                    "username": username,
                    "password": password,
                    "port": "22",
                }

        return None

    def get_ndoli_credentials(self) -> Optional[Dict[str, str]]:
        """Get ndoli (SSH-SLURM) credentials from available sources."""
        # Try 1Password first (local development)
        if self.is_local_development and self._op_manager:
            try:
                ndoli_notes = self._op_manager.get_credential(
                    "clustrix-ssh-slurm", "notesPlain"
                )
                if ndoli_notes:
                    return self._parse_notes_credentials(ndoli_notes)
            except Exception as e:
                logger.debug(f"Failed to get ndoli credentials from 1Password: {e}")

        # GitHub Actions: Use repository secrets
        if self.is_github_actions:
            username = os.getenv("CLUSTRIX_USERNAME")
            password = os.getenv("CLUSTRIX_PASSWORD")

            if username and password:
                return {
                    "host": "ndoli.dartmouth.edu",
                    "username": username,
                    "password": password,
                    "port": "22",
                }

        return None

    def _parse_notes_credentials(self, notes: str) -> Dict[str, str]:
        """Parse credentials from 1Password notes field."""
        credentials = {}

        # Remove wrapping quotes if present
        if notes.startswith('"') and notes.endswith('"'):
            notes = notes[1:-1]

        for line in notes.split("\n"):
            line = line.strip()
            if line.startswith("- ") and ":" in line:
                key, value = line[2:].split(":", 1)
                credentials[key.strip()] = value.strip()

        return {
            "host": credentials.get("hostname"),
            "username": credentials.get("username"),
            "password": credentials.get("password"),
            "port": "22",
        }

    def get_slurm_credentials(self) -> Optional[Dict[str, str]]:
        """Get SLURM credentials from available sources."""
        # Try 1Password first (local development)
        if self.is_local_development and self._op_manager:
            try:
                username = self._op_manager.get_credential(
                    "clustrix-slurm-validation", "username"
                )
                password = self._op_manager.get_credential(
                    "clustrix-slurm-validation", "password"
                )
                hostname = self._op_manager.get_credential(
                    "clustrix-slurm-validation", "hostname"
                )

                if username and password and hostname:
                    return {
                        "host": hostname,
                        "username": username,
                        "password": password,
                        "port": "22",
                    }
            except Exception as e:
                logger.debug(f"Failed to get SLURM credentials from 1Password: {e}")

        # GitHub Actions: Use repository secrets
        if self.is_github_actions:
            username = os.getenv("CLUSTRIX_USERNAME")
            password = os.getenv("CLUSTRIX_PASSWORD")

            if username and password:
                return {
                    "host": "slurm-server",  # Default SLURM server hostname
                    "username": username,
                    "password": password,
                    "port": "22",
                }

        # Fall back to environment variables
        host = os.getenv("TEST_SLURM_HOST", "localhost")
        username = os.getenv("TEST_SLURM_USERNAME", os.getenv("USER"))
        password = os.getenv("TEST_SLURM_PASSWORD")

        if username and password:
            return {
                "host": host,
                "username": username,
                "password": password,
                "port": "22",
            }

        return None

    def get_huggingface_credentials(self) -> Optional[Dict[str, str]]:
        """Get HuggingFace credentials from available sources."""
        # Try 1Password first (local development)
        if self.is_local_development and self._validation_creds:
            try:
                hf_creds = self._validation_creds.get_huggingface_credentials()
                if hf_creds:
                    return hf_creds
            except Exception as e:
                logger.debug(
                    f"Failed to get HuggingFace credentials from 1Password: {e}"
                )

        # GitHub Actions: Use repository secrets
        if self.is_github_actions:
            username = os.getenv("HF_USERNAME")
            token = os.getenv("HF_TOKEN")

            if token:
                return {"token": token, "username": username}

        # Fall back to environment variables
        token = os.getenv("HUGGINGFACE_TOKEN") or os.getenv("HF_TOKEN")
        username = os.getenv("HUGGINGFACE_USERNAME") or os.getenv("HF_USERNAME")

        if token:
            return {"token": token, "username": username}

        return None

    def get_lambda_cloud_credentials(self) -> Optional[Dict[str, str]]:
        """Get Lambda Cloud credentials from available sources."""
        # Try 1Password first (local development)
        if self.is_local_development and self._validation_creds:
            try:
                lambda_creds = self._validation_creds.get_lambda_cloud_credentials()
                if lambda_creds:
                    return lambda_creds
            except Exception as e:
                logger.debug(
                    f"Failed to get Lambda Cloud credentials from 1Password: {e}"
                )

        # GitHub Actions: Use repository secrets
        if self.is_github_actions:
            api_key = os.getenv("LAMBDA_CLOUD_API_KEY")
            if api_key:
                return {
                    "api_key": api_key,
                    "endpoint": "https://cloud.lambdalabs.com/api/v1",
                }

        # Fall back to environment variables
        api_key = os.getenv("LAMBDA_CLOUD_API_KEY")
        endpoint = os.getenv(
            "LAMBDA_CLOUD_ENDPOINT", "https://cloud.lambdalabs.com/api/v1"
        )

        if api_key:
            return {"api_key": api_key, "endpoint": endpoint}

        return None

    def get_kubernetes_credentials(self) -> Optional[Dict[str, str]]:
        """Get Kubernetes credentials from available sources."""
        # Try 1Password first (local development)
        if self.is_local_development and self._op_manager:
            try:
                # Try to get Kubernetes cluster credentials
                kubeconfig = self._op_manager.get_credential(
                    "clustrix-kubernetes-validation", "kubeconfig"
                )
                namespace = self._op_manager.get_credential(
                    "clustrix-kubernetes-validation", "namespace"
                )
                context = self._op_manager.get_credential(
                    "clustrix-kubernetes-validation", "context"
                )

                if kubeconfig:
                    result = {
                        "kubeconfig_content": kubeconfig,
                        "namespace": namespace or "default",
                    }
                    if context:
                        result["context"] = context
                    return result

            except Exception as e:
                logger.debug(
                    f"Failed to get Kubernetes credentials from 1Password: {e}"
                )

        # GitHub Actions: Use repository secrets
        if self.is_github_actions:
            kubeconfig = os.getenv("KUBECONFIG_CONTENT")
            namespace = os.getenv("K8S_NAMESPACE")

            if kubeconfig:
                result = {
                    "kubeconfig_content": kubeconfig,
                    "namespace": namespace or "default",
                }
                context = os.getenv("K8S_CONTEXT")
                if context:
                    result["context"] = context
                return result

        # Fall back to environment variables and local kubeconfig
        kubeconfig_path = os.getenv("KUBECONFIG") or os.path.expanduser(
            "~/.kube/config"
        )
        if os.path.exists(kubeconfig_path):
            namespace = os.getenv("K8S_NAMESPACE", "default")
            context = os.getenv("K8S_CONTEXT")

            result = {
                "kubeconfig_path": kubeconfig_path,
                "namespace": namespace,
            }
            if context:
                result["context"] = context
            return result

        # Check if running in-cluster
        if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token"):
            return {
                "in_cluster": True,
                "namespace": os.getenv("K8S_NAMESPACE", "default"),
            }

        return None

    def get_credential_status(self) -> Dict[str, bool]:
        """Get status of all credential types."""
        return {
            "aws": self.get_aws_credentials() is not None,
            "azure": self.get_azure_credentials() is not None,
            "gcp": self.get_gcp_credentials() is not None,
            "ssh": self.get_ssh_credentials() is not None,
            "slurm": self.get_slurm_credentials() is not None,
            "kubernetes": self.get_kubernetes_credentials() is not None,
            "huggingface": self.get_huggingface_credentials() is not None,
            "lambda_cloud": self.get_lambda_cloud_credentials() is not None,
            "1password": self.is_1password_available(),
        }

    def print_credential_status(self) -> None:
        """Print credential status for debugging."""
        print("\n🔑 Credential Status:")
        print(
            f"  Environment: {'GitHub Actions' if self.is_github_actions else 'Local Development'}"
        )
        print(f"  1Password CLI: {'✅' if self.is_1password_available() else '❌'}")

        status = self.get_credential_status()
        for service, available in status.items():
            if service == "1password":
                continue
            icon = "✅" if available else "❌"
            print(f"  {service.upper()}: {icon}")

    def setup_environment_variables(self) -> None:
        """Set up environment variables from available credentials."""
        # Set AWS credentials
        aws_creds = self.get_aws_credentials()
        if aws_creds:
            os.environ["TEST_AWS_ACCESS_KEY"] = aws_creds["access_key_id"]
            os.environ["TEST_AWS_SECRET_KEY"] = aws_creds["secret_access_key"]
            os.environ["TEST_AWS_REGION"] = aws_creds["region"]

        # Set Azure credentials
        azure_creds = self.get_azure_credentials()
        if azure_creds:
            os.environ["TEST_AZURE_SUBSCRIPTION_ID"] = azure_creds["subscription_id"]
            if azure_creds.get("tenant_id"):
                os.environ["TEST_AZURE_TENANT_ID"] = azure_creds["tenant_id"]
            if azure_creds.get("client_id"):
                os.environ["TEST_AZURE_CLIENT_ID"] = azure_creds["client_id"]
            if azure_creds.get("client_secret"):
                os.environ["TEST_AZURE_CLIENT_SECRET"] = azure_creds["client_secret"]

        # Set GCP credentials
        gcp_creds = self.get_gcp_credentials()
        if gcp_creds:
            os.environ["TEST_GCP_PROJECT_ID"] = gcp_creds["project_id"]
            if gcp_creds.get("service_account_path"):
                os.environ["TEST_GCP_SERVICE_ACCOUNT_PATH"] = gcp_creds[
                    "service_account_path"
                ]
            if gcp_creds.get("service_account_json"):
                os.environ["GCP_JSON"] = gcp_creds["service_account_json"]

        # Set SSH credentials
        ssh_creds = self.get_ssh_credentials()
        if ssh_creds:
            if ssh_creds.get("host"):
                os.environ["TEST_SSH_HOST"] = ssh_creds["host"]
            if ssh_creds.get("username"):
                os.environ["TEST_SSH_USERNAME"] = ssh_creds["username"]
            if ssh_creds.get("password"):
                os.environ["TEST_SSH_PASSWORD"] = ssh_creds["password"]
            if ssh_creds.get("private_key_path"):
                os.environ["TEST_SSH_PRIVATE_KEY_PATH"] = ssh_creds["private_key_path"]

        # Set SLURM credentials
        slurm_creds = self.get_slurm_credentials()
        if slurm_creds:
            os.environ["TEST_SLURM_HOST"] = slurm_creds["host"]
            os.environ["TEST_SLURM_USERNAME"] = slurm_creds["username"]
            if slurm_creds.get("password"):
                os.environ["TEST_SLURM_PASSWORD"] = slurm_creds["password"]

        # Set HuggingFace credentials
        hf_creds = self.get_huggingface_credentials()
        if hf_creds:
            os.environ["HUGGINGFACE_TOKEN"] = hf_creds["token"]
            os.environ["HF_TOKEN"] = hf_creds["token"]
            if hf_creds.get("username"):
                os.environ["HUGGINGFACE_USERNAME"] = hf_creds["username"]
                os.environ["HF_USERNAME"] = hf_creds["username"]

        # Set Lambda Cloud credentials
        lambda_creds = self.get_lambda_cloud_credentials()
        if lambda_creds:
            os.environ["LAMBDA_CLOUD_API_KEY"] = lambda_creds["api_key"]
            os.environ["LAMBDA_CLOUD_ENDPOINT"] = lambda_creds["endpoint"]


# Global credential manager instance
_credential_manager = None


def get_credential_manager() -> RealWorldCredentialManager:
    """Get the global credential manager instance."""
    global _credential_manager
    if _credential_manager is None:
        _credential_manager = RealWorldCredentialManager()
    return _credential_manager


def setup_test_credentials() -> None:
    """Set up test credentials from all available sources."""
    manager = get_credential_manager()
    manager.setup_environment_variables()


def get_credential_status() -> Dict[str, bool]:
    """Get status of all credential types."""
    manager = get_credential_manager()
    return manager.get_credential_status()


def print_credential_status() -> None:
    """Print credential status for debugging."""
    manager = get_credential_manager()
    manager.print_credential_status()


# Convenience functions for tests
def get_lambda_credentials() -> Optional[Dict[str, str]]:
    """Get Lambda Cloud credentials for tests."""
    manager = get_credential_manager()
    return manager.get_lambda_cloud_credentials()


def get_aws_credentials() -> Optional[Dict[str, str]]:
    """Get AWS credentials for tests."""
    manager = get_credential_manager()
    return manager.get_aws_credentials()


def get_azure_credentials() -> Optional[Dict[str, str]]:
    """Get Azure credentials for tests."""
    manager = get_credential_manager()
    return manager.get_azure_credentials()


def get_gcp_credentials() -> Optional[Dict[str, str]]:
    """Get GCP credentials for tests."""
    manager = get_credential_manager()
    return manager.get_gcp_credentials()


# Set up credentials when module is imported
setup_test_credentials()
