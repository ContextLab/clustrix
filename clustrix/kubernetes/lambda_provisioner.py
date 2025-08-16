"""
Lambda Cloud Kubernetes adapter.

Provides Kubernetes-style job execution on Lambda Cloud infrastructure.
This is not a traditional Kubernetes provisioner but an adapter that makes
Lambda Cloud instances work with the Clustrix Kubernetes interface.
"""

import logging
import time
from typing import Dict, Any, List
import requests

try:
    import paramiko

    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False
    paramiko = None  # type: ignore

from .cluster_provisioner import BaseKubernetesProvisioner, ClusterSpec

logger = logging.getLogger(__name__)


class LambdaCloudKubernetesProvisioner(BaseKubernetesProvisioner):
    """
    Lambda Cloud Kubernetes adapter.

    This adapter provides a Kubernetes-like interface for Lambda Cloud instances,
    enabling Clustrix to submit jobs to Lambda Cloud's GPU infrastructure using
    familiar Kubernetes concepts translated to Lambda Cloud operations.

    Key adaptations:
    - "Clusters" are represented as groups of Lambda Cloud instances
    - "Nodes" are individual Lambda Cloud instances
    - "Jobs" are executed via SSH on instances
    - "kubectl" operations are translated to Lambda Cloud API calls
    """

    def __init__(self, credentials: Dict[str, str], region: str):
        super().__init__(credentials, region)

        if not PARAMIKO_AVAILABLE:
            raise ImportError(
                "paramiko required for Lambda Cloud SSH access. Install with: pip install paramiko"
            )

        # Validate required credentials
        self.api_key = credentials.get("api_key") or credentials.get("lambda_api_key")

        if not self.api_key:
            raise ValueError(
                "Lambda Cloud API key required (api_key or lambda_api_key)"
            )

        # Lambda Cloud API configuration
        self.base_url = "https://cloud.lambdalabs.com/api/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Track created instances for cleanup
        self.created_resources: Dict[str, List[str]] = {"instances": [], "ssh_keys": []}

        # SSH configuration for instances
        self.ssh_connections: Dict[str, paramiko.SSHClient] = {}

    def validate_credentials(self) -> bool:
        """Validate Lambda Cloud credentials and permissions."""
        try:
            # Test basic Lambda Cloud access
            response = requests.get(
                f"{self.base_url}/instance-types", headers=self.headers, timeout=30
            )
            response.raise_for_status()

            instance_types = response.json()
            logger.info(
                f"✅ Lambda Cloud credentials validated, {len(instance_types)} instance types available"
            )

            # Check account info
            response = requests.get(
                f"{self.base_url}/instances", headers=self.headers, timeout=30
            )
            response.raise_for_status()
            logger.debug("✅ Lambda Cloud instances API access confirmed")

            return True

        except Exception as e:
            logger.error(f"❌ Lambda Cloud credential validation failed: {e}")
            return False

    def provision_complete_infrastructure(self, spec: ClusterSpec) -> Dict[str, Any]:
        """
        Create Lambda Cloud instance infrastructure adapted as Kubernetes cluster.

        This creates Lambda Cloud instances that can execute Clustrix jobs
        using a Kubernetes-compatible interface.
        """
        logger.info(
            f"🚀 Starting Lambda Cloud cluster provisioning: {spec.cluster_name}"
        )

        try:
            # Step 1: Create SSH key for instances
            ssh_key_info = self._create_ssh_key(spec)

            # Step 2: Launch Lambda Cloud instances
            instances_info = self._launch_instances(spec, ssh_key_info)

            # Step 3: Set up instances for Kubernetes-style job execution
            self._setup_instances_for_k8s_jobs(instances_info, spec)

            # Step 4: Create kubectl-compatible interface
            kubectl_config = self._create_kubectl_interface(instances_info)

            # Step 5: Verify instances are ready for jobs
            self._verify_cluster_operational(instances_info)

            result = {
                "cluster_id": spec.cluster_name,
                "cluster_name": spec.cluster_name,
                "provider": "lambda",
                "region": spec.region,
                "endpoint": f"lambda-cluster-{spec.cluster_name}",
                "instances": instances_info["instances"],
                "instance_type": instances_info["instance_type"],
                "kubectl_config": kubectl_config,
                "ready_for_jobs": True,
                "created_resources": self.created_resources.copy(),
            }

            logger.info(
                f"✅ Lambda Cloud cluster provisioning completed: {spec.cluster_name}"
            )
            return result

        except Exception as e:
            logger.error(f"❌ Lambda Cloud cluster provisioning failed: {e}")
            # Attempt cleanup of any created resources
            self._cleanup_failed_provisioning(spec.cluster_name)
            raise

    def _create_ssh_key(self, spec: ClusterSpec) -> Dict[str, Any]:
        """Create SSH key for Lambda Cloud instances."""
        logger.info("🔑 Creating SSH key...")

        # Generate SSH key pair
        key = paramiko.RSAKey.generate(2048)

        # Create temporary files for keys
        import tempfile

        private_key_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".pem", delete=False
        )
        public_key_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".pub", delete=False
        )

        # Write private key
        key.write_private_key(private_key_file)
        private_key_file.close()

        # Write public key
        public_key = f"{key.get_name()} {key.get_base64()}"
        public_key_file.write(public_key)
        public_key_file.close()

        # Add SSH key to Lambda Cloud
        ssh_key_name = f"clustrix-{spec.cluster_name}-{int(time.time())}"

        ssh_key_data = {"name": ssh_key_name, "public_key": public_key}

        response = requests.post(
            f"{self.base_url}/ssh-keys",
            headers=self.headers,
            json=ssh_key_data,
            timeout=30,
        )
        response.raise_for_status()

        ssh_key_result = response.json()
        self.created_resources["ssh_keys"].append(ssh_key_name)

        logger.info(f"✅ Created SSH key: {ssh_key_name}")
        return {
            "name": ssh_key_name,
            "id": ssh_key_result.get("id"),
            "private_key_file": private_key_file.name,
            "public_key": public_key,
        }

    def _launch_instances(
        self, spec: ClusterSpec, ssh_key_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Launch Lambda Cloud instances."""
        logger.info("🚀 Launching Lambda Cloud instances...")

        # Map node requirements to Lambda Cloud instance type
        instance_type = self._map_node_requirements_to_instance_type(spec)

        instances = []

        for i in range(spec.node_count):
            instance_name = f"clustrix-{spec.cluster_name}-{i}"

            instance_data = {
                "region_name": spec.region,
                "instance_type_name": instance_type,
                "ssh_key_names": [ssh_key_info["name"]],
                "file_system_names": [],  # No persistent storage needed
                "quantity": 1,
                "name": instance_name,
            }

            logger.info(
                f"Launching instance {i + 1}/{spec.node_count}: {instance_name}"
            )

            response = requests.post(
                f"{self.base_url}/instance-operations/launch",
                headers=self.headers,
                json=instance_data,
                timeout=60,
            )
            response.raise_for_status()

            launch_result = response.json()
            instance_ids = launch_result.get("instance_ids", [])

            if instance_ids:
                instance_id = instance_ids[0]
                instances.append(
                    {
                        "id": instance_id,
                        "name": instance_name,
                        "type": instance_type,
                        "region": spec.region,
                    }
                )
                self.created_resources["instances"].append(instance_id)
            else:
                raise RuntimeError(f"Failed to launch instance: {launch_result}")

        # Wait for instances to be running
        self._wait_for_instances_ready(instances)

        # Get instance details including IP addresses
        instances_with_ips = self._get_instance_details(instances)

        logger.info(f"✅ Launched {len(instances)} Lambda Cloud instances")
        return {
            "instances": instances_with_ips,
            "instance_type": instance_type,
            "ssh_key": ssh_key_info,
        }

    def _map_node_requirements_to_instance_type(self, spec: ClusterSpec) -> str:
        """Map Kubernetes node requirements to Lambda Cloud instance type."""
        # Get available instance types
        response = requests.get(
            f"{self.base_url}/instance-types", headers=self.headers, timeout=30
        )
        response.raise_for_status()

        instance_types = response.json().get("data", {})

        # Prefer GPU instances for Lambda Cloud
        gpu_preferences = [
            "gpu_1x_a100",
            "gpu_1x_v100",
            "gpu_1x_rtx6000",
            "gpu_8x_a100",
            "gpu_8x_v100",
        ]

        # Find the first available GPU instance type
        for instance_type in gpu_preferences:
            if instance_type in instance_types:
                return instance_type

        # Fallback to any available instance type
        available_types = list(instance_types.keys())
        if available_types:
            return available_types[0]

        raise RuntimeError("No available Lambda Cloud instance types found")

    def _wait_for_instances_ready(self, instances: List[Dict[str, Any]]) -> None:
        """Wait for Lambda Cloud instances to be ready."""
        logger.info("⏳ Waiting for instances to be ready...")

        max_attempts = 60  # 10 minutes
        for attempt in range(max_attempts):
            all_ready = True

            for instance in instances:
                status = self._get_instance_status(instance["id"])
                if status != "active":
                    all_ready = False
                    break

            if all_ready:
                logger.info("✅ All instances are ready")
                return

            logger.info(f"⏳ Waiting for instances... ({attempt + 1}/{max_attempts})")
            time.sleep(10)

        raise RuntimeError(f"Instances not ready after {max_attempts * 10} seconds")

    def _get_instance_status(self, instance_id: str) -> str:
        """Get Lambda Cloud instance status."""
        response = requests.get(
            f"{self.base_url}/instances", headers=self.headers, timeout=30
        )
        response.raise_for_status()

        instances_data = response.json().get("data", [])
        for instance in instances_data:
            if instance["id"] == instance_id:
                return instance["status"]

        return "unknown"

    def _get_instance_details(
        self, instances: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Get detailed instance information including IP addresses."""
        response = requests.get(
            f"{self.base_url}/instances", headers=self.headers, timeout=30
        )
        response.raise_for_status()

        instances_data = response.json().get("data", [])
        detailed_instances = []

        for instance in instances:
            for instance_data in instances_data:
                if instance_data["id"] == instance["id"]:
                    detailed_instances.append(
                        {
                            **instance,
                            "ip": instance_data.get("ip"),
                            "status": instance_data.get("status"),
                            "hostname": instance_data.get("hostname"),
                        }
                    )
                    break

        return detailed_instances

    def _setup_instances_for_k8s_jobs(
        self, instances_info: Dict[str, Any], spec: ClusterSpec
    ) -> None:
        """Configure instances for Kubernetes-style job execution."""
        logger.info("🔧 Setting up instances for K8s jobs...")

        instances = instances_info["instances"]
        ssh_key_info = instances_info["ssh_key"]

        for instance in instances:
            try:
                # Connect via SSH
                ssh_client = self._connect_ssh(
                    instance, ssh_key_info["private_key_file"]
                )

                # Install necessary software
                self._install_k8s_tools(ssh_client, instance)

                # Start job execution server
                self._start_job_server(ssh_client, instance)

                self.ssh_connections[instance["id"]] = ssh_client

            except Exception as e:
                logger.warning(f"Failed to set up instance {instance['id']}: {e}")

        logger.info("✅ Instances ready for job execution")

    def _connect_ssh(
        self, instance: Dict[str, Any], private_key_file: str
    ) -> paramiko.SSHClient:
        """Connect to instance via SSH."""
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Load private key
        private_key = paramiko.RSAKey.from_private_key_file(private_key_file)

        # Connect with retries
        max_attempts = 30
        for attempt in range(max_attempts):
            try:
                ssh_client.connect(
                    hostname=instance["ip"],
                    username="ubuntu",  # Default Lambda Cloud user
                    pkey=private_key,
                    timeout=30,
                )
                return ssh_client
            except Exception:
                if attempt == max_attempts - 1:
                    raise
                time.sleep(10)

        raise RuntimeError(f"Could not connect to instance {instance['id']}")

    def _install_k8s_tools(
        self, ssh_client: paramiko.SSHClient, instance: Dict[str, Any]
    ) -> None:
        """Install necessary tools on the instance."""
        logger.info(f"Installing tools on instance {instance['id']}...")

        commands = [
            "sudo apt-get update",
            "sudo apt-get install -y python3-pip docker.io",
            "pip3 install flask requests",
            "sudo systemctl start docker",
            "sudo systemctl enable docker",
        ]

        for command in commands:
            stdin, stdout, stderr = ssh_client.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                logger.warning(f"Command failed on {instance['id']}: {command}")

    def _start_job_server(
        self, ssh_client: paramiko.SSHClient, instance: Dict[str, Any]
    ) -> None:
        """Start job execution server on the instance."""
        logger.info(f"Starting job server on instance {instance['id']}...")

        # Create job server script
        job_server_script = """
import os
import json
import time
import logging
from flask import Flask, request, jsonify
import subprocess
import threading

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

jobs = {}

@app.route('/api/v1/namespaces/<namespace>/jobs', methods=['POST'])
def create_job(namespace):
    job_spec = request.json
    job_id = f"job-{int(time.time())}"

    containers = job_spec.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
    if containers:
        container = containers[0]
        command = container.get('command', ['echo', 'Hello from Lambda Cloud!'])

        jobs[job_id] = {
            'metadata': {'name': job_id, 'namespace': namespace},
            'status': {'phase': 'Running'}
        }

        thread = threading.Thread(target=execute_job, args=(job_id, command))
        thread.start()

        return jsonify({'metadata': {'name': job_id}})

    return jsonify({'error': 'No containers specified'}), 400

@app.route('/api/v1/namespaces/<namespace>/jobs/<job_name>', methods=['GET'])
def get_job(namespace, job_name):
    return jsonify(jobs.get(job_name, {'error': 'Job not found'}))

def execute_job(job_id, command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        jobs[job_id]['status'] = {'phase': 'Succeeded' if result.returncode == 0 else 'Failed'}
        jobs[job_id]['result'] = {
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode
        }
    except Exception as e:
        jobs[job_id]['status'] = {'phase': 'Failed'}
        jobs[job_id]['result'] = {'error': str(e)}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
"""

        # Upload and start the server
        sftp = ssh_client.open_sftp()
        with sftp.file("/tmp/job_server.py", "w") as f:
            f.write(job_server_script)
        sftp.close()

        # Start server in background
        ssh_client.exec_command(
            "nohup python3 /tmp/job_server.py > /tmp/job_server.log 2>&1 &"
        )

    def _create_kubectl_interface(
        self, instances_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create kubectl-compatible configuration for Lambda Cloud cluster."""
        logger.info("⚙️ Creating kubectl interface...")

        # Use first instance as primary endpoint
        primary_instance = instances_info["instances"][0]

        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "cluster": {"server": f"http://{primary_instance['ip']}:8080"},
                    "name": "lambda-cluster",
                }
            ],
            "contexts": [
                {
                    "context": {"cluster": "lambda-cluster", "user": "lambda-user"},
                    "name": "lambda-cluster",
                }
            ],
            "current-context": "lambda-cluster",
            "users": [{"name": "lambda-user", "user": {"token": self.api_key}}],
        }

        return kubeconfig

    def _verify_cluster_operational(self, instances_info: Dict[str, Any]) -> None:
        """Verify cluster is ready for job submission."""
        logger.info("🔍 Verifying cluster is operational...")

        for instance in instances_info["instances"]:
            if instance["status"] != "active":
                raise RuntimeError(
                    f"Instance {instance['id']} not active: {instance['status']}"
                )

        logger.info("✅ Cluster verification completed")

    def destroy_cluster_infrastructure(self, cluster_id: str) -> bool:
        """Destroy Lambda Cloud cluster infrastructure."""
        logger.info(f"🧹 Destroying Lambda Cloud cluster: {cluster_id}")

        try:
            success = True

            # Terminate all instances
            for instance_id in self.created_resources.get("instances", []):
                try:
                    response = requests.post(
                        f"{self.base_url}/instance-operations/terminate",
                        headers=self.headers,
                        json={"instance_ids": [instance_id]},
                        timeout=60,
                    )
                    response.raise_for_status()
                    logger.info(f"✅ Terminated instance: {instance_id}")
                except Exception as e:
                    logger.warning(f"Failed to terminate instance {instance_id}: {e}")
                    success = False

            # Delete SSH keys
            for ssh_key_name in self.created_resources.get("ssh_keys", []):
                try:
                    response = requests.delete(
                        f"{self.base_url}/ssh-keys/{ssh_key_name}",
                        headers=self.headers,
                        timeout=30,
                    )
                    response.raise_for_status()
                    logger.info(f"✅ Deleted SSH key: {ssh_key_name}")
                except Exception as e:
                    logger.warning(f"Failed to delete SSH key {ssh_key_name}: {e}")
                    success = False

            # Close SSH connections
            for ssh_client in self.ssh_connections.values():
                try:
                    ssh_client.close()
                except Exception:
                    pass

            return success

        except Exception as e:
            logger.error(f"❌ Failed to destroy cluster: {e}")
            return False

    def get_cluster_status(self, cluster_id: str) -> Dict[str, Any]:
        """Get Lambda Cloud cluster status."""
        try:
            instance_count = len(self.created_resources.get("instances", []))

            if instance_count == 0:
                return {
                    "cluster_id": cluster_id,
                    "status": "NOT_FOUND",
                    "ready_for_jobs": False,
                }

            # Check instance statuses
            all_active = True
            for instance_id in self.created_resources.get("instances", []):
                status = self._get_instance_status(instance_id)
                if status != "active":
                    all_active = False
                    break

            return {
                "cluster_id": cluster_id,
                "status": "ACTIVE" if all_active else "PROVISIONING",
                "instance_count": instance_count,
                "ready_for_jobs": all_active,
            }

        except Exception as e:
            logger.error(f"Error getting cluster status: {e}")
            return {
                "cluster_id": cluster_id,
                "status": "ERROR",
                "ready_for_jobs": False,
            }

    def _cleanup_failed_provisioning(self, cluster_name: str) -> None:
        """Clean up resources if provisioning fails."""
        logger.info("🧹 Cleaning up failed provisioning...")
        try:
            self._cleanup_all_resources()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def _cleanup_all_resources(self) -> None:
        """Clean up all tracked resources."""
        logger.info("🧹 Cleaning up all created resources...")

        # Terminate instances
        for instance_id in self.created_resources.get("instances", []):
            try:
                response = requests.post(
                    f"{self.base_url}/instance-operations/terminate",
                    headers=self.headers,
                    json={"instance_ids": [instance_id]},
                    timeout=60,
                )
                response.raise_for_status()
                logger.info(f"✅ Terminated instance: {instance_id}")
            except Exception as e:
                logger.warning(f"Failed to terminate instance {instance_id}: {e}")

        # Delete SSH keys
        for ssh_key_name in self.created_resources.get("ssh_keys", []):
            try:
                response = requests.delete(
                    f"{self.base_url}/ssh-keys/{ssh_key_name}",
                    headers=self.headers,
                    timeout=30,
                )
                response.raise_for_status()
                logger.info(f"✅ Deleted SSH key: {ssh_key_name}")
            except Exception as e:
                logger.warning(f"Failed to delete SSH key {ssh_key_name}: {e}")
