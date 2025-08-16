"""
HuggingFace Spaces Kubernetes adapter.

Provides Kubernetes-style job execution on HuggingFace Spaces infrastructure.
This is not a traditional Kubernetes provisioner but an adapter that makes
HuggingFace Spaces work with the Clustrix Kubernetes interface.
"""

import logging
import time
from typing import Dict, Any, List

try:
    from huggingface_hub import HfApi, HfFolder
    from huggingface_hub.utils import HfHubHTTPError

    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False
    HfApi = None
    HfFolder = None
    HfHubHTTPError = Exception

from .cluster_provisioner import BaseKubernetesProvisioner, ClusterSpec

logger = logging.getLogger(__name__)


class HuggingFaceKubernetesProvisioner(BaseKubernetesProvisioner):
    """
    HuggingFace Spaces Kubernetes adapter.

    This adapter provides a Kubernetes-like interface for HuggingFace Spaces,
    enabling Clustrix to submit jobs to HuggingFace's infrastructure using
    familiar Kubernetes concepts translated to HF Spaces operations.

    Key adaptations:
    - "Clusters" are represented as HuggingFace Spaces
    - "Nodes" are represented as Space hardware configurations
    - "Jobs" are executed as Space applications
    - "kubectl" operations are translated to HuggingFace Hub API calls
    """

    def __init__(self, credentials: Dict[str, str], region: str):
        super().__init__(credentials, region)

        if not HF_AVAILABLE:
            raise ImportError(
                "huggingface_hub required for HuggingFace integration. Install with: pip install huggingface_hub"
            )

        # Validate required credentials
        self.token = credentials.get("token") or credentials.get("hf_token")
        self.username = credentials.get("username") or credentials.get("hf_username")

        if not self.token:
            raise ValueError("HuggingFace token required (token or hf_token)")
        if not self.username:
            raise ValueError("HuggingFace username required (username or hf_username)")

        # Initialize HuggingFace API
        self.api = HfApi(token=self.token)

        # Track created spaces for cleanup
        self.created_resources: Dict[str, List[str]] = {"spaces": [], "repos": []}

    def validate_credentials(self) -> bool:
        """Validate HuggingFace credentials and permissions."""
        try:
            # Test basic HF access
            user_info = self.api.whoami()
            logger.info(
                f"✅ HuggingFace credentials validated for user: {user_info['name']}"
            )

            # Check if user can create spaces
            try:
                # Try to list user's spaces
                self.api.list_spaces(author=self.username)
                logger.debug("✅ HuggingFace Spaces access confirmed")
            except Exception as e:
                logger.warning(f"⚠️ Limited HuggingFace Spaces access: {e}")

            return True

        except Exception as e:
            logger.error(f"❌ HuggingFace credential validation failed: {e}")
            return False

    def provision_complete_infrastructure(self, spec: ClusterSpec) -> Dict[str, Any]:
        """
        Create HuggingFace Space infrastructure adapted as Kubernetes cluster.

        This creates a HuggingFace Space that can execute Clustrix jobs
        using a Kubernetes-compatible interface.
        """
        logger.info(f"🚀 Starting HuggingFace Space provisioning: {spec.cluster_name}")

        try:
            # Step 1: Create HuggingFace Space
            space_info = self._create_huggingface_space(spec)

            # Step 2: Set up Space for Kubernetes-style job execution
            self._setup_space_for_k8s_jobs(space_info, spec)

            # Step 3: Create kubectl-compatible interface
            kubectl_config = self._create_kubectl_interface(space_info)

            # Step 4: Verify space is ready for jobs
            self._verify_space_operational(space_info["space_name"])

            result = {
                "cluster_id": space_info["space_name"],
                "cluster_name": space_info["space_name"],
                "provider": "huggingface",
                "region": "global",  # HF doesn't have regions
                "endpoint": space_info["space_url"],
                "space_id": space_info["space_id"],
                "hardware": space_info["hardware"],
                "sdk": space_info["sdk"],
                "kubectl_config": kubectl_config,
                "ready_for_jobs": True,
                "created_resources": self.created_resources.copy(),
            }

            logger.info(
                f"✅ HuggingFace Space provisioning completed: {spec.cluster_name}"
            )
            return result

        except Exception as e:
            logger.error(f"❌ HuggingFace Space provisioning failed: {e}")
            # Attempt cleanup of any created resources
            self._cleanup_failed_provisioning(spec.cluster_name)
            raise

    def _create_huggingface_space(self, spec: ClusterSpec) -> Dict[str, Any]:
        """Create HuggingFace Space to serve as cluster."""
        logger.info("🏗️ Creating HuggingFace Space...")

        space_name = f"{self.username}/clustrix-{spec.cluster_name}"

        # Determine hardware based on node requirements
        hardware = self._map_node_requirements_to_hardware(spec)

        # Create Space
        space_info = self.api.create_repo(
            repo_id=space_name,
            repo_type="space",
            space_sdk="docker",  # Use Docker for maximum flexibility
            space_hardware=hardware,
            private=True,  # Keep spaces private by default
        )

        self.created_resources["spaces"].append(space_name)

        # Add initial files to the space
        self._upload_space_files(space_name, spec)

        logger.info(f"✅ Created HuggingFace Space: {space_name}")
        return {
            "space_name": space_name,
            "space_id": space_info.repo_id,
            "space_url": f"https://huggingface.co/spaces/{space_name}",
            "hardware": hardware,
            "sdk": "docker",
        }

    def _map_node_requirements_to_hardware(self, spec: ClusterSpec) -> str:
        """Map Kubernetes node requirements to HuggingFace hardware."""
        # Default hardware mapping
        hardware_mapping = {
            1: "cpu-basic",  # Single node -> basic CPU
            2: "cpu-upgrade",  # 2 nodes -> upgraded CPU
            4: "t4-small",  # 4+ nodes -> GPU hardware
            8: "t4-medium",  # 8+ nodes -> larger GPU
        }

        # Find appropriate hardware tier
        for node_threshold in sorted(hardware_mapping.keys(), reverse=True):
            if spec.node_count >= node_threshold:
                return hardware_mapping[node_threshold]

        return "cpu-basic"  # Default

    def _upload_space_files(self, space_name: str, spec: ClusterSpec) -> None:
        """Upload necessary files to make the space Kubernetes-compatible."""
        logger.info("📤 Uploading space configuration files...")

        # Create Dockerfile for the space
        dockerfile_content = f"""FROM python:3.11-slim

# Install kubectl and other Kubernetes tools
RUN apt-get update && apt-get install -y curl && \\
    curl -LO ""\
"https://dl.k8s.io/release/"\
"$(curl -L -s https://dl.k8s.io/release/stable.txt)"\
"/bin/linux/amd64/kubectl" && \\
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \\
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Clustrix and dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application code
COPY . /app
WORKDIR /app

# Set up environment
ENV PYTHONPATH=/app
ENV CLUSTRIX_SPACE_NAME={space_name}

# Start the job execution server
CMD ["python", "clustrix_job_server.py"]
"""

        # Create requirements.txt
        requirements_content = """
clustrix
flask
requests
huggingface_hub
"""

        # Create job execution server
        job_server_content = """
import os
import json
import time
import logging
from flask import Flask, request, jsonify
import subprocess
import tempfile
import threading
from pathlib import Path

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Job storage
jobs = {}

@app.route('/api/v1/namespaces/<namespace>/jobs', methods=['POST'])
def create_job(namespace):
    \"\"\"Create a Kubernetes-style job.\"\"\"
    job_spec = request.json
    job_id = f"job-{int(time.time())}"

    logger.info(f"Creating job {job_id} in namespace {namespace}")

    # Extract job details
    containers = job_spec.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
    if not containers:
        return jsonify({'error': 'No containers specified'}), 400

    container = containers[0]
    image = container.get('image', 'python:3.11-slim')
    command = container.get('command', ['python', '-c', 'print("Hello from HF Space!")'])

    # Store job
    jobs[job_id] = {
        'metadata': {'name': job_id, 'namespace': namespace},
        'spec': job_spec['spec'],
        'status': {'phase': 'Running'},
        'result': None
    }

    # Execute job in background
    thread = threading.Thread(target=execute_job, args=(job_id, command))
    thread.start()

    return jsonify({'metadata': {'name': job_id}})

@app.route('/api/v1/namespaces/<namespace>/jobs/<job_name>', methods=['GET'])
def get_job(namespace, job_name):
    \"\"\"Get job status.\"\"\"
    if job_name not in jobs:
        return jsonify({'error': 'Job not found'}), 404

    return jsonify(jobs[job_name])

def execute_job(job_id, command):
    \"\"\"Execute job and store result.\"\"\"
    try:
        logger.info(f"Executing job {job_id}: {command}")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300
        )

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
    app.run(host='0.0.0.0', port=7860)
"""

        # Upload files to the space
        files_to_upload = {
            "Dockerfile": dockerfile_content,
            "requirements.txt": requirements_content,
            "clustrix_job_server.py": job_server_content,
        }

        for filename, content in files_to_upload.items():
            self.api.upload_file(
                path_or_fileobj=content.encode(),
                path_in_repo=filename,
                repo_id=space_name,
                repo_type="space",
            )

        logger.info("✅ Space files uploaded successfully")

    def _setup_space_for_k8s_jobs(
        self, space_info: Dict[str, Any], spec: ClusterSpec
    ) -> None:
        """Configure space for Kubernetes-style job execution."""
        logger.info("🔧 Setting up Space for K8s jobs...")

        # Wait for space to be built and running
        self._wait_for_space_ready(space_info["space_name"])

        logger.info("✅ Space ready for job execution")

    def _wait_for_space_ready(self, space_name: str) -> None:
        """Wait for HuggingFace Space to be ready."""
        logger.info("⏳ Waiting for Space to be ready...")

        max_attempts = 60  # 10 minutes
        for attempt in range(max_attempts):
            try:
                space_info = self.api.space_info(space_name)
                if space_info.runtime and space_info.runtime.stage == "RUNNING":
                    logger.info("✅ Space is running and ready")
                    return
                elif space_info.runtime and space_info.runtime.stage in [
                    "STOPPED",
                    "FAILED",
                ]:
                    raise RuntimeError(
                        f"Space failed to start: {space_info.runtime.stage}"
                    )

                logger.info(f"⏳ Space not ready yet... ({attempt + 1}/{max_attempts})")
                time.sleep(10)

            except Exception as e:
                if attempt == max_attempts - 1:
                    raise RuntimeError(
                        f"Space not ready after {max_attempts * 10} seconds: {e}"
                    )
                time.sleep(10)

        raise RuntimeError(f"Space not ready after {max_attempts * 10} seconds")

    def _create_kubectl_interface(self, space_info: Dict[str, Any]) -> Dict[str, Any]:
        """Create kubectl-compatible configuration for HF Space."""
        logger.info("⚙️ Creating kubectl interface...")

        # Create a kubeconfig that points to the HF Space API
        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "cluster": {"server": f"{space_info['space_url']}/api/v1"},
                    "name": f"hf-space-{space_info['space_name']}",
                }
            ],
            "contexts": [
                {
                    "context": {
                        "cluster": f"hf-space-{space_info['space_name']}",
                        "user": f"hf-user-{self.username}",
                    },
                    "name": f"hf-space-{space_info['space_name']}",
                }
            ],
            "current-context": f"hf-space-{space_info['space_name']}",
            "users": [
                {"name": f"hf-user-{self.username}", "user": {"token": self.token}}
            ],
        }

        return kubeconfig

    def _verify_space_operational(self, space_name: str) -> None:
        """Verify space is ready for job submission."""
        logger.info("🔍 Verifying space is operational...")

        try:
            space_info = self.api.space_info(space_name)
            if not space_info.runtime or space_info.runtime.stage != "RUNNING":
                raise RuntimeError(
                    f"Space not running: {space_info.runtime.stage if space_info.runtime else 'Unknown'}"
                )

            logger.info("✅ Space verification completed")

        except Exception as e:
            logger.error(f"❌ Space verification failed: {e}")
            raise

    def destroy_cluster_infrastructure(self, cluster_id: str) -> bool:
        """Destroy HuggingFace Space infrastructure."""
        logger.info(f"🧹 Destroying HuggingFace Space: {cluster_id}")

        try:
            # Delete the space
            if cluster_id is not None and cluster_id.startswith(self.username):  # type: ignore
                space_name = cluster_id
            else:
                space_name = f"{self.username}/clustrix-{cluster_id}"

            try:
                self.api.delete_repo(repo_id=space_name, repo_type="space")
                logger.info(f"✅ Deleted HuggingFace Space: {space_name}")
            except HfHubHTTPError as e:
                if e.response.status_code == 404:
                    logger.info(f"Space {space_name} not found (already deleted)")
                else:
                    raise

            return True

        except Exception as e:
            logger.error(f"❌ Failed to destroy space: {e}")
            return False

    def get_cluster_status(self, cluster_id: str) -> Dict[str, Any]:
        """Get HuggingFace Space status."""
        try:
            if cluster_id is not None and cluster_id.startswith(self.username):  # type: ignore
                space_name = cluster_id
            else:
                space_name = f"{self.username}/clustrix-{cluster_id}"
            space_info = self.api.space_info(space_name)

            status = "UNKNOWN"
            ready_for_jobs = False

            if space_info.runtime:
                status = space_info.runtime.stage
                ready_for_jobs = status == "RUNNING"

            return {
                "cluster_id": cluster_id,
                "status": status,
                "endpoint": f"https://huggingface.co/spaces/{space_name}",
                "hardware": (
                    space_info.cardData.get("hardware")
                    if space_info.cardData
                    else "unknown"
                ),
                "sdk": space_info.sdk,
                "ready_for_jobs": ready_for_jobs,
            }

        except HfHubHTTPError as e:
            if e.response.status_code == 404:
                return {
                    "cluster_id": cluster_id,
                    "status": "NOT_FOUND",
                    "ready_for_jobs": False,
                }
            else:
                raise

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

        # Delete all created spaces
        for space_name in self.created_resources.get("spaces", []):
            try:
                self.api.delete_repo(repo_id=space_name, repo_type="space")
                logger.info(f"✅ Deleted space: {space_name}")
            except Exception as e:
                logger.warning(f"Failed to delete space {space_name}: {e}")
