"""SSH and Kubernetes connection management for cluster execution.

This module handles establishing and managing connections to different cluster types,
including SSH connections to traditional HPC clusters and Kubernetes cluster setup.
"""

import os
import time
import tempfile
import logging
from typing import Dict, Any
import yaml
import paramiko

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages SSH and Kubernetes connections for cluster execution."""

    def __init__(self, config):
        """Initialize connection manager.

        Args:
            config: ClusterConfig instance with connection settings
        """
        self.config = config
        self.ssh_client = None
        self.sftp_client = None
        self.k8s_client = None

    def setup_ssh_connection(self):
        """Setup SSH connection to cluster."""
        if not self.config.cluster_host:
            raise ValueError("cluster_host must be specified for SSH-based clusters")

        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Connect using provided credentials
        connect_kwargs = {
            "hostname": self.config.cluster_host,
            "port": self.config.cluster_port,
        }

        # Always include username if available
        if self.config.username:
            connect_kwargs["username"] = self.config.username
        else:
            connect_kwargs["username"] = os.getenv("USER")

        # Use key file for authentication (recommended)
        if self.config.key_file:
            connect_kwargs["key_filename"] = self.config.key_file
        elif self.config.password:
            # Fallback to password authentication (not recommended)
            connect_kwargs["password"] = self.config.password
        else:
            # Try to get SSH credentials from credential manager
            # This ensures we check .env, environment variables, GitHub Actions,
            # and ONLY THEN 1Password (which requires manual auth)
            try:
                from .credential_manager import FlexibleCredentialManager

                credential_manager = FlexibleCredentialManager()
                ssh_credentials = credential_manager.ensure_credential("ssh")

                if ssh_credentials:
                    if "password" in ssh_credentials:
                        connect_kwargs["password"] = ssh_credentials["password"]
                        logger.info("Using SSH password from credential manager")
                    elif "key_file" in ssh_credentials:
                        connect_kwargs["key_filename"] = ssh_credentials["key_file"]
                        logger.info("Using SSH key from credential manager")
            except Exception as e:
                logger.debug(f"Could not load SSH credentials from manager: {e}")
                # Fall back to SSH agent or default keys

        self.ssh_client.connect(**connect_kwargs)
        self.sftp_client = self.ssh_client.open_sftp()

    def setup_kubernetes(self):
        """Setup Kubernetes client with optional cloud provider auto-configuration."""
        try:
            from kubernetes import client, config  # type: ignore

            # Try Kubernetes auto-provisioning if enabled (NEW)
            if (
                self.config.auto_provision_k8s
                and self.config.cluster_type == "kubernetes"
            ):
                try:
                    from .kubernetes.cluster_provisioner import (
                        KubernetesClusterProvisioner,
                        ClusterSpec,
                    )

                    logger.info("🚀 Starting Kubernetes cluster auto-provisioning...")

                    # Create cluster specification from config
                    cluster_name = (
                        self.config.k8s_cluster_name
                        or f"clustrix-auto-{int(time.time())}"
                    )

                    spec = ClusterSpec(
                        provider=self.config.k8s_provider,
                        cluster_name=cluster_name,
                        region=self.config.k8s_region
                        or self.config.cloud_region
                        or "us-west-2",
                        node_count=self.config.k8s_node_count,
                        node_type=self.config.k8s_node_type,
                        kubernetes_version=self.config.k8s_version,
                        from_scratch=self.config.k8s_from_scratch,
                    )

                    # Provision cluster
                    provisioner = KubernetesClusterProvisioner(self.config)
                    cluster_info = provisioner.provision_cluster_if_needed(spec)

                    # Store provisioner instance for lifecycle management
                    self._k8s_provisioner = provisioner
                    self._k8s_cluster_info = cluster_info

                    # Update config with provisioned cluster details
                    self.config.cluster_host = cluster_info.get("endpoint", "")
                    self.config.k8s_cluster_name = cluster_info["cluster_id"]

                    # Configure kubectl with the provisioned cluster
                    self._configure_kubectl_for_provisioned_cluster(cluster_info)

                    logger.info(
                        f"✅ Kubernetes cluster auto-provisioned: {cluster_info['cluster_id']}"
                    )

                except Exception as e:
                    logger.error(f"❌ Kubernetes auto-provisioning failed: {e}")
                    # Continue with existing configuration
                    logger.info("Continuing with existing Kubernetes configuration...")

            # Try cloud provider auto-configuration if enabled
            elif (
                self.config.cloud_auto_configure
                and self.config.cluster_type == "kubernetes"
            ):
                try:
                    # Import CloudProviderManager from the renamed module
                    from .cloud_provider_manager import CloudProviderManager

                    cloud_manager = CloudProviderManager(self.config)
                    result = cloud_manager.auto_configure()

                    if result.get("auto_configured"):
                        logger.info(
                            f"Auto-configured {result.get('provider')} cluster: {result.get('cluster_name')}"
                        )
                    else:
                        logger.info(
                            f"Cloud auto-configuration skipped: {result.get('reason', 'Unknown')}"
                        )
                        if "error" in result:
                            logger.warning(
                                f"Auto-configuration error: {result['error']}"
                            )

                except Exception as e:
                    logger.warning(f"Cloud provider auto-configuration failed: {e}")
                    # Continue with manual configuration

            config.load_kube_config()
            self.k8s_client = client.ApiClient()
        except ImportError:
            raise ImportError(
                "kubernetes package required for Kubernetes cluster support"
            )

    def _configure_kubectl_for_provisioned_cluster(self, cluster_info: Dict[str, Any]):
        """Configure kubectl with credentials for auto-provisioned cluster."""
        logger.info(
            f"🔧 Configuring kubectl for cluster: {cluster_info.get('cluster_id', 'unknown')}"
        )

        try:
            # Get kubectl config from cluster info
            kubectl_config = cluster_info.get("kubectl_config")
            if not kubectl_config:
                logger.warning("No kubectl config provided in cluster info")
                return

            # Write kubectl config to temporary file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as f:
                yaml.dump(kubectl_config, f, default_flow_style=False)
                temp_config_path = f.name

            try:
                from kubernetes import config  # type: ignore

                # Load the configuration
                config.load_kube_config(config_file=temp_config_path)
                logger.info(
                    "✅ kubectl configured successfully for auto-provisioned cluster"
                )

                # Store config path for cleanup later
                self._k8s_temp_config_path = temp_config_path

            except Exception as e:
                logger.error(f"Failed to load kubectl config: {e}")
                # Clean up temp file if loading failed
                os.unlink(temp_config_path)
                raise

        except Exception as e:
            logger.error(f"Failed to configure kubectl for provisioned cluster: {e}")
            raise

    def execute_remote_command(self, command: str) -> tuple:
        """Execute command on remote cluster."""
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call setup_ssh_connection() first."
            )
        stdin, stdout, stderr = self.ssh_client.exec_command(command)
        return stdout.read().decode(), stderr.read().decode()

    def upload_file(self, local_path: str, remote_path: str):
        """Upload file to remote cluster."""
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call setup_ssh_connection() first."
            )
        sftp = self.ssh_client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()

    def download_file(self, remote_path: str, local_path: str):
        """Download file from remote cluster."""
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call setup_ssh_connection() first."
            )
        sftp = self.ssh_client.open_sftp()
        sftp.get(remote_path, local_path)
        sftp.close()

    def create_remote_file(self, remote_path: str, content: str):
        """Create file with content on remote cluster."""
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call setup_ssh_connection() first."
            )
        sftp = self.ssh_client.open_sftp()
        with sftp.open(remote_path, "w") as f:
            f.write(content)
        sftp.close()

    def remote_file_exists(self, remote_path: str) -> bool:
        """Check if file exists on remote cluster."""
        if self.ssh_client is None:
            return False
        try:
            sftp = self.ssh_client.open_sftp()
            sftp.stat(remote_path)
            sftp.close()
            return True
        except Exception:
            return False

    def connect(self):
        """Establish connection to cluster (for manual connection)."""
        if self.config.cluster_type in ["slurm", "pbs", "sge", "ssh"]:
            if not self.ssh_client:
                self.setup_ssh_connection()
        elif self.config.cluster_type == "kubernetes":
            if not hasattr(self, "k8s_client") or self.k8s_client is None:
                self.setup_kubernetes()

    def disconnect(self):
        """Disconnect from cluster."""
        if self.sftp_client:
            self.sftp_client.close()
            self.sftp_client = None
        if self.ssh_client:
            self.ssh_client.close()
            self.ssh_client = None

    def cleanup_auto_provisioned_cluster(self):
        """Clean up auto-provisioned Kubernetes cluster."""
        logger.info("🧹 Cleaning up auto-provisioned Kubernetes cluster")

        try:
            # Clean up temporary kubectl config
            if hasattr(self, "_k8s_temp_config_path") and self._k8s_temp_config_path:
                if os.path.exists(self._k8s_temp_config_path):
                    os.unlink(self._k8s_temp_config_path)
                    logger.info("✅ Temporary kubectl config cleaned up")

            # Clean up provisioned cluster if enabled
            if (
                hasattr(self, "_k8s_provisioner")
                and hasattr(self, "_k8s_cluster_info")
                and self._k8s_provisioner
                and self._k8s_cluster_info
            ):

                cluster_name = self._k8s_cluster_info.get("cluster_id")
                if cluster_name and getattr(self.config, "k8s_cleanup_on_exit", True):
                    logger.info(
                        f"🗑️ Destroying auto-provisioned cluster: {cluster_name}"
                    )

                    success = self._k8s_provisioner.destroy_cluster_infrastructure(
                        cluster_name
                    )
                    if success:
                        logger.info(f"✅ Cluster {cluster_name} destroyed successfully")
                    else:
                        logger.warning(
                            f"⚠️ Failed to fully destroy cluster {cluster_name}"
                        )
                else:
                    if cluster_name:
                        logger.info(
                            f"ℹ️ Preserving auto-provisioned cluster: {cluster_name}"
                        )

        except Exception as e:
            logger.error(f"❌ Error during cluster cleanup: {e}")

    def get_cluster_status(self) -> Dict[str, Any]:
        """Get status of managed Kubernetes cluster."""
        if not hasattr(self, "_k8s_provisioner") or not hasattr(
            self, "_k8s_cluster_info"
        ):
            return {"status": "NO_MANAGED_CLUSTER", "ready": False}

        cluster_name = self._k8s_cluster_info.get("cluster_id")
        if not cluster_name:
            return {"status": "UNKNOWN", "ready": False}

        try:
            status = self._k8s_provisioner.get_cluster_status(cluster_name)
            return {
                "status": status.get("status", "UNKNOWN"),
                "ready": status.get("ready_for_jobs", False),
                "cluster_name": cluster_name,
                "provider": self._k8s_cluster_info.get("provider"),
                "endpoint": self._k8s_cluster_info.get("endpoint"),
            }
        except Exception as e:
            logger.error(f"Error getting cluster status: {e}")
            return {"status": "ERROR", "ready": False, "error": str(e)}

    def ensure_cluster_ready(self, timeout: int = 900) -> bool:
        """Ensure auto-provisioned cluster is ready for job execution."""
        if not hasattr(self, "_k8s_provisioner") or not hasattr(
            self, "_k8s_cluster_info"
        ):
            logger.warning("No managed cluster to check readiness for")
            return True  # Assume external cluster is ready

        cluster_name = self._k8s_cluster_info.get("cluster_id")
        if not cluster_name:
            return False

        logger.info(f"⏳ Ensuring cluster {cluster_name} is ready for jobs...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                status = self.get_cluster_status()
                if status.get("ready"):
                    logger.info(f"✅ Cluster {cluster_name} is ready for jobs")
                    return True

                logger.info(f"Cluster status: {status.get('status')} - waiting...")
                time.sleep(30)  # Wait 30 seconds between checks

            except Exception as e:
                logger.error(f"Error checking cluster readiness: {e}")
                time.sleep(10)

        logger.error(
            f"❌ Cluster {cluster_name} did not become ready within {timeout}s"
        )
        return False
