"""
Local Docker-based Kubernetes provisioner using kind (Kubernetes in Docker).

This provisioner creates local Kubernetes clusters using Docker containers,
allowing for complete integration testing without cloud infrastructure costs.
Perfect for development, testing, and CI/CD environments.
"""

import logging
import subprocess
import time
import yaml
import tempfile
import os
from typing import Dict, Any

from .cluster_provisioner import BaseKubernetesProvisioner, ClusterSpec

logger = logging.getLogger(__name__)


class LocalDockerKubernetesProvisioner(BaseKubernetesProvisioner):
    """Local Docker-based Kubernetes provisioner using kind."""

    def __init__(self, credentials: Dict[str, str], region: str = "local"):
        """Initialize local provisioner.

        Args:
            credentials: Not used for local provisioner, but kept for interface compatibility
            region: Not used for local provisioner, but kept for interface compatibility
        """
        super().__init__(credentials, region)
        self.docker_available = self._check_docker_available()
        self.kind_available = self._check_kind_available()
        self.kubectl_available = self._check_kubectl_available()

        if not self.docker_available:
            raise RuntimeError("Docker is required for local Kubernetes provisioning")
        if not self.kind_available:
            raise RuntimeError(
                "kind (Kubernetes in Docker) is required for local provisioning"
            )
        if not self.kubectl_available:
            raise RuntimeError("kubectl is required for local Kubernetes provisioning")

    def _check_docker_available(self) -> bool:
        """Check if Docker is available and running."""
        try:
            result = subprocess.run(
                ["docker", "version"], capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (
            subprocess.TimeoutExpired,
            subprocess.SubprocessError,
            FileNotFoundError,
        ):
            return False

    def _check_kind_available(self) -> bool:
        """Check if kind is available."""
        try:
            result = subprocess.run(
                ["kind", "version"], capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (
            subprocess.TimeoutExpired,
            subprocess.SubprocessError,
            FileNotFoundError,
        ):
            return False

    def _check_kubectl_available(self) -> bool:
        """Check if kubectl is available."""
        try:
            result = subprocess.run(
                ["kubectl", "version", "--client"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (
            subprocess.TimeoutExpired,
            subprocess.SubprocessError,
            FileNotFoundError,
        ):
            return False

    def validate_credentials(self) -> bool:
        """Validate that required tools are available."""
        return self.docker_available and self.kind_available and self.kubectl_available

    def provision_complete_infrastructure(
        self, cluster_spec: ClusterSpec
    ) -> Dict[str, Any]:
        """Provision a complete local Kubernetes cluster using kind.

        Args:
            cluster_spec: Cluster specification

        Returns:
            Dictionary containing cluster information
        """
        logger.info(
            f"🐳 Provisioning local Kubernetes cluster: {cluster_spec.cluster_name}"
        )

        start_time = time.time()

        try:
            # 1. Create kind cluster configuration
            kind_config = self._create_kind_config(cluster_spec)

            # 2. Write config to temporary file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as f:
                yaml.dump(kind_config, f, default_flow_style=False)
                kind_config_path = f.name

            try:
                # 3. Create the cluster
                logger.info(
                    f"🚀 Creating kind cluster with {cluster_spec.node_count} nodes..."
                )

                cmd = [
                    "kind",
                    "create",
                    "cluster",
                    "--name",
                    cluster_spec.cluster_name,
                    "--config",
                    kind_config_path,
                    "--wait",
                    "300s",  # Wait up to 5 minutes for cluster to be ready
                ]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,  # 10 minute timeout
                )

                if result.returncode != 0:
                    raise RuntimeError(
                        f"Failed to create kind cluster: {result.stderr}"
                    )

                logger.info("✅ Kind cluster created successfully")

                # 4. Get kubeconfig
                kubeconfig = self._get_kubeconfig(cluster_spec.cluster_name)

                # 5. Wait for cluster to be fully ready
                if not self._wait_for_cluster_ready(cluster_spec.cluster_name):
                    raise RuntimeError("Cluster failed to become ready")

                # 6. Get cluster info
                cluster_info = self._get_cluster_info(cluster_spec, kubeconfig)

                provision_time = time.time() - start_time
                logger.info(
                    f"✅ Local cluster provisioned successfully in {provision_time:.1f}s"
                )

                return cluster_info

            finally:
                # Clean up temporary config file
                os.unlink(kind_config_path)

        except Exception as e:
            logger.error(f"❌ Failed to provision local cluster: {e}")
            # Try to clean up on failure
            try:
                self.destroy_cluster_infrastructure(cluster_spec.cluster_name)
            except Exception:
                pass  # Ignore cleanup errors
            raise

    def _create_kind_config(self, cluster_spec: ClusterSpec) -> Dict[str, Any]:
        """Create kind cluster configuration."""
        config: Dict[str, Any] = {
            "kind": "Cluster",
            "apiVersion": "kind.x-k8s.io/v1alpha4",
            "nodes": [],
        }

        # Add control plane node
        nodes = [{"role": "control-plane"}]

        # Add worker nodes
        worker_count = max(0, cluster_spec.node_count - 1)  # Subtract control plane
        for i in range(worker_count):
            nodes.append({"role": "worker"})

        config["nodes"] = nodes

        return config

    def _get_kubeconfig(self, cluster_name: str) -> Dict[str, Any]:
        """Get kubeconfig for the cluster."""
        logger.info("🔧 Retrieving kubeconfig...")

        try:
            result = subprocess.run(
                ["kind", "get", "kubeconfig", "--name", cluster_name],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                raise RuntimeError(f"Failed to get kubeconfig: {result.stderr}")

            kubeconfig = yaml.safe_load(result.stdout)
            return kubeconfig

        except Exception as e:
            logger.error(f"Failed to retrieve kubeconfig: {e}")
            raise

    def _wait_for_cluster_ready(self, cluster_name: str, timeout: int = 300) -> bool:
        """Wait for cluster to be fully ready."""
        logger.info("⏳ Waiting for cluster to be ready...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # Check if all nodes are ready
                result = subprocess.run(
                    ["kubectl", "get", "nodes", "--context", f"kind-{cluster_name}"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode == 0:
                    # Check if all nodes show as Ready
                    lines = result.stdout.strip().split("\n")[1:]  # Skip header
                    if lines and all("Ready" in line for line in lines):
                        logger.info("✅ All nodes are ready")
                        return True

                logger.info("Still waiting for nodes to be ready...")
                time.sleep(10)

            except Exception as e:
                logger.debug(f"Error checking node status: {e}")
                time.sleep(5)

        logger.error("❌ Timeout waiting for cluster to be ready")
        return False

    def _get_cluster_info(
        self, cluster_spec: ClusterSpec, kubeconfig: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Get comprehensive cluster information."""

        # Get cluster endpoint from kubeconfig
        cluster_info_from_config = kubeconfig["clusters"][0]["cluster"]
        endpoint = cluster_info_from_config["server"]

        # Get node information
        nodes = self._get_node_info(cluster_spec.cluster_name)

        return {
            "cluster_id": cluster_spec.cluster_name,
            "cluster_name": cluster_spec.cluster_name,
            "provider": "local-docker",
            "region": "local",
            "status": "RUNNING",
            "ready_for_jobs": True,
            "endpoint": endpoint,
            "nodes": nodes,
            "node_count": len(nodes),
            "kubernetes_version": self._get_kubernetes_version(
                cluster_spec.cluster_name
            ),
            "kubectl_config": kubeconfig,
            "created_resources": {
                "cluster": cluster_spec.cluster_name,
                "nodes": [node["name"] for node in nodes],
            },
            "provisioning_method": "kind",
            "cost_estimate": 0.0,  # Local clusters are free
        }

    def _get_node_info(self, cluster_name: str) -> list:
        """Get information about cluster nodes."""
        try:
            result = subprocess.run(
                [
                    "kubectl",
                    "get",
                    "nodes",
                    "-o",
                    "json",
                    "--context",
                    f"kind-{cluster_name}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                logger.warning(f"Failed to get node info: {result.stderr}")
                return []

            nodes_data = yaml.safe_load(result.stdout)
            nodes = []

            for node in nodes_data.get("items", []):
                node_info = {
                    "name": node["metadata"]["name"],
                    "status": "Ready" if self._node_is_ready(node) else "NotReady",
                    "roles": self._get_node_roles(node),
                    "version": node["status"]["nodeInfo"]["kubeletVersion"],
                    "os": node["status"]["nodeInfo"]["osImage"],
                    "container_runtime": node["status"]["nodeInfo"][
                        "containerRuntimeVersion"
                    ],
                }
                nodes.append(node_info)

            return nodes

        except Exception as e:
            logger.warning(f"Failed to get node information: {e}")
            return []

    def _node_is_ready(self, node: Dict[str, Any]) -> bool:
        """Check if a node is ready."""
        conditions = node.get("status", {}).get("conditions", [])
        for condition in conditions:
            if condition.get("type") == "Ready" and condition.get("status") == "True":
                return True
        return False

    def _get_node_roles(self, node: Dict[str, Any]) -> list:
        """Get roles for a node."""
        labels = node.get("metadata", {}).get("labels", {})
        roles = []

        if "node-role.kubernetes.io/control-plane" in labels:
            roles.append("control-plane")
        if "node-role.kubernetes.io/master" in labels:
            roles.append("master")
        if not roles:
            roles.append("worker")

        return roles

    def _get_kubernetes_version(self, cluster_name: str) -> str:
        """Get Kubernetes version of the cluster."""
        try:
            result = subprocess.run(
                [
                    "kubectl",
                    "version",
                    "--context",
                    f"kind-{cluster_name}",
                    "--output",
                    "yaml",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                version_info = yaml.safe_load(result.stdout)
                server_version = version_info.get("serverVersion", {}).get(
                    "gitVersion", "unknown"
                )
                return server_version

        except Exception as e:
            logger.debug(f"Failed to get Kubernetes version: {e}")

        return "unknown"

    def get_cluster_status(self, cluster_name: str) -> Dict[str, str]:
        """Get current status of the cluster."""
        try:
            # Check if cluster exists
            result = subprocess.run(
                ["kind", "get", "clusters"], capture_output=True, text=True, timeout=30
            )

            if result.returncode != 0:
                return {"status": "ERROR", "ready_for_jobs": "false"}

            clusters = result.stdout.strip().split("\n")
            if cluster_name not in clusters:
                return {"status": "NOT_FOUND", "ready_for_jobs": "false"}

            # Check if cluster is ready
            ready = self._wait_for_cluster_ready(cluster_name, timeout=10)

            return {
                "status": "RUNNING" if ready else "STARTING",
                "ready_for_jobs": "true" if ready else "false",
            }

        except Exception as e:
            logger.error(f"Error getting cluster status: {e}")
            return {"status": "ERROR", "ready_for_jobs": "false"}

    def destroy_cluster_infrastructure(self, cluster_name: str) -> bool:
        """Destroy the local Kubernetes cluster."""
        logger.info(f"🗑️ Destroying local cluster: {cluster_name}")

        try:
            result = subprocess.run(
                ["kind", "delete", "cluster", "--name", cluster_name],
                capture_output=True,
                text=True,
                timeout=120,  # 2 minute timeout
            )

            if result.returncode == 0:
                logger.info(f"✅ Local cluster destroyed successfully: {cluster_name}")
                return True
            else:
                logger.error(f"Failed to destroy cluster: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"Error destroying cluster: {e}")
            return False

    def list_clusters(self) -> list:
        """List all local kind clusters."""
        try:
            result = subprocess.run(
                ["kind", "get", "clusters"], capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0:
                clusters = result.stdout.strip().split("\n")
                return [c for c in clusters if c.strip()]
            else:
                logger.warning(f"Failed to list clusters: {result.stderr}")
                return []

        except Exception as e:
            logger.error(f"Error listing clusters: {e}")
            return []
