"""Secure credential management using 1Password CLI integration.

This module provides secure access to API keys and credentials stored in 1Password,
ensuring that sensitive information never gets committed to version control.
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class SecureCredentialManager:
    """Manages secure credentials using 1Password CLI."""

    def __init__(self, vault_name: str = "Private"):
        """Initialize credential manager.

        Args:
            vault_name: Name of 1Password vault to use for Clustrix credentials
        """
        self.vault_name = vault_name
        self._op_available: Optional[bool] = None

    def is_op_available(self) -> bool:
        """Check if 1Password CLI is available and authenticated."""
        if self._op_available is not None:
            return bool(self._op_available)

        try:
            result = subprocess.run(
                ["op", "account", "list"], capture_output=True, text=True, timeout=5
            )
            self._op_available = result.returncode == 0
            return self._op_available
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self._op_available = False
            return False

    def get_credential(
        self, item_name: str, field_name: str = "password"
    ) -> Optional[str]:
        """Retrieve a credential from 1Password.

        Args:
            item_name: Name of the 1Password item
            field_name: Field name within the item (default: "password")

        Returns:
            Credential value or None if not found/unavailable
        """
        if not self.is_op_available():
            logger.debug("1Password CLI not available, skipping credential retrieval")
            return None

        try:
            cmd = [
                "op",
                "item",
                "get",
                item_name,
                f"--field={field_name}",
                f"--vault={self.vault_name}",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                return result.stdout.strip()
            else:
                logger.debug(
                    f"Failed to retrieve {item_name}.{field_name}: {result.stderr}"
                )
                return None

        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout retrieving credential {item_name}.{field_name}")
            return None
        except Exception as e:
            logger.debug(f"Error retrieving credential {item_name}.{field_name}: {e}")
            return None

    def get_structured_credential(self, item_name: str) -> Optional[Dict[str, Any]]:
        """Retrieve a structured credential from 1Password.

        Args:
            item_name: Name of the 1Password item

        Returns:
            Parsed credential or None if not found/unavailable
        """
        if not self.is_op_available():
            return None

        try:
            cmd = [
                "op",
                "item",
                "get",
                item_name,
                "--format=json",
                f"--vault={self.vault_name}",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                data = json.loads(result.stdout)

                # Extract fields into a simple dict
                fields = {}
                for field in data.get("fields", []):
                    field_id = field.get("id", "")
                    field_label = field.get("label", "")
                    field_value = field.get("value")

                    if field_value:
                        # Use label if available, otherwise id
                        key = field_label if field_label else field_id
                        fields[key] = field_value

                        # Special handling for secure notes with structured content
                        if field_id == "notesPlain" and field_value:
                            # Parse YAML-like content from secure notes
                            parsed_fields = self._parse_notes_content(field_value)
                            fields.update(parsed_fields)

                return fields
            else:
                logger.debug(
                    f"Failed to retrieve structured credential {item_name}: {result.stderr}"
                )
                return None

        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            logger.debug(f"Error retrieving structured credential {item_name}: {e}")
            return None

    def _parse_notes_content(self, notes_content: str) -> Dict[str, str]:
        """Parse YAML-like content from secure notes.

        Args:
            notes_content: Content of the notes field

        Returns:
            Dictionary of parsed key-value pairs
        """
        fields = {}

        try:
            for line in notes_content.strip().split("\n"):
                line = line.strip()
                if line.startswith("- ") and ":" in line:
                    # Parse "- key: value" format
                    line = line[2:]  # Remove "- "
                    key, value = line.split(":", 1)
                    fields[key.strip()] = value.strip()
                elif ":" in line and not line.startswith("#"):
                    # Parse "key: value" format
                    key, value = line.split(":", 1)
                    fields[key.strip()] = value.strip()
        except Exception as e:
            logger.debug(f"Error parsing notes content: {e}")

        return fields

    def store_credential(
        self,
        item_name: str,
        credential_data: Dict[str, str],
        category: str = "API_CREDENTIAL",
    ) -> bool:
        """Store a credential in 1Password.

        Args:
            item_name: Name for the 1Password item
            credential_data: Dictionary of field names to values
            category: 1Password category (API_CREDENTIAL, LOGIN, etc.)

        Returns:
            True if successful, False otherwise
        """
        if not self.is_op_available():
            logger.warning("1Password CLI not available, cannot store credential")
            return False

        try:
            # Build the op create command
            cmd = [
                "op",
                "item",
                "create",
                f"--category={category}",
                f"--vault={self.vault_name}",
                f"--title={item_name}",
            ]

            # Add fields
            for field_name, field_value in credential_data.items():
                cmd.extend([f"{field_name}={field_value}"])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

            if result.returncode == 0:
                logger.info(f"Successfully stored credential {item_name} in 1Password")
                return True
            else:
                logger.error(f"Failed to store credential {item_name}: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout storing credential {item_name}")
            return False
        except Exception as e:
            logger.error(f"Error storing credential {item_name}: {e}")
            return False


class ValidationCredentials:
    """Provides credentials for external service validation."""

    def __init__(self):
        self.cred_manager = SecureCredentialManager()

    def get_aws_credentials(self) -> Optional[Dict[str, str]]:
        """Get AWS credentials for validation."""
        aws_creds = self.cred_manager.get_structured_credential(
            "clustrix-aws-validation"
        )
        if aws_creds:
            return {
                "aws_access_key_id": aws_creds.get("aws_access_key_id"),
                "aws_secret_access_key": aws_creds.get("aws_secret_access_key"),
                "aws_region": aws_creds.get("region", "us-east-1"),
            }

        # Fallback to environment variables
        if all(
            os.getenv(key) for key in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
        ):
            return {
                "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", ""),
                "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", ""),
                "aws_region": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            }

        return None

    def get_gcp_credentials(self) -> Optional[Dict[str, str]]:
        """Get GCP credentials for validation."""
        gcp_creds = self.cred_manager.get_structured_credential(
            "clustrix-gcp-validation"
        )
        if gcp_creds:
            return {
                "project_id": gcp_creds.get("project_id"),
                "service_account_json": gcp_creds.get("service_account_json"),
                "region": gcp_creds.get("region", "us-central1"),
            }

        # Fallback to environment variables/service account file
        if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            return {
                "project_id": os.getenv("GOOGLE_CLOUD_PROJECT", ""),
                "service_account_json": os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
                "region": os.getenv("GOOGLE_CLOUD_REGION", "us-central1"),
            }

        return None

    def get_lambda_cloud_credentials(self) -> Optional[Dict[str, str]]:
        """Get Lambda Cloud credentials for validation."""
        lambda_creds = self.cred_manager.get_structured_credential(
            "clustrix-lambda-cloud-validation"
        )
        if lambda_creds:
            return {
                "api_key": lambda_creds.get("api_key"),
                "endpoint": lambda_creds.get(
                    "endpoint", "https://cloud.lambdalabs.com/api/v1"
                ),
            }

        # Fallback to environment variable
        api_key = os.getenv("LAMBDA_CLOUD_API_KEY")
        if api_key:
            return {
                "api_key": api_key,
                "endpoint": "https://cloud.lambdalabs.com/api/v1",
            }

        return None

    def get_huggingface_credentials(self) -> Optional[Dict[str, str]]:
        """Get HuggingFace credentials for validation."""
        hf_creds = self.cred_manager.get_structured_credential(
            "clustrix-huggingface-validation"
        )
        if hf_creds:
            return {
                "token": hf_creds.get("token"),
                "username": hf_creds.get("username"),
            }

        # Fallback to environment variable
        token = os.getenv("HUGGINGFACE_TOKEN") or os.getenv("HF_TOKEN")
        if token:
            return {"token": token, "username": os.getenv("HUGGINGFACE_USERNAME", "")}

        return None

    def get_docker_credentials(self) -> Optional[Dict[str, str]]:
        """Get Docker registry credentials for validation."""
        docker_creds = self.cred_manager.get_structured_credential(
            "clustrix-docker-validation"
        )
        if docker_creds:
            return {
                "username": docker_creds.get("username"),
                "password": docker_creds.get("password"),
                "registry": docker_creds.get("registry", "docker.io"),
            }

        return None

    def get_ssh_credentials(self) -> Optional[Dict[str, str]]:
        """Get SSH credentials for validation."""
        ssh_creds = self.cred_manager.get_structured_credential(
            "clustrix-ssh-validation"
        )
        if ssh_creds:
            return {
                "hostname": ssh_creds.get("hostname"),
                "username": ssh_creds.get("username"),
                "private_key": ssh_creds.get("private_key"),
                "port": ssh_creds.get("port", "22"),
            }

        return None


def ensure_secure_environment():
    """Ensure environment is set up securely for credential handling."""
    clustrix_dir = Path.home() / ".clustrix"
    clustrix_dir.mkdir(exist_ok=True)

    # Create .gitignore patterns to prevent credential leaks
    gitignore_patterns = [
        "# Clustrix security",
        "**/.clustrix/credentials/**",
        "**/.clustrix/keys/**",
        "**/clustrix-credentials.json",
        "**/clustrix-*.pem",
        "**/clustrix-*.key",
        "**/*-credentials.json",
        "**/*-service-account.json",
        ".env.local",
        ".env.validation",
    ]

    # Add patterns to project .gitignore if not already present
    gitignore_path = Path.cwd() / ".gitignore"
    if gitignore_path.exists():
        existing_content = gitignore_path.read_text()
        if "# Clustrix security" not in existing_content:
            with gitignore_path.open("a") as f:
                f.write("\n" + "\n".join(gitignore_patterns) + "\n")

    # Create secure credentials directory
    cred_dir = clustrix_dir / "credentials"
    cred_dir.mkdir(exist_ok=True)

    # Set restrictive permissions (Unix-like systems)
    try:
        cred_dir.chmod(0o700)  # rwx------
    except Exception:
        pass  # Windows or other systems

    return cred_dir
