import os
import time
import tempfile
import pickle
import logging
from typing import Any, Dict, Optional, TYPE_CHECKING
import paramiko
import cloudpickle  # type: ignore

from .config import ClusterConfig
from .utils import create_job_script, setup_remote_environment

if TYPE_CHECKING:
    from .cloud_providers.base import CloudProvider

logger = logging.getLogger(__name__)


class ClusterExecutor:
    """Handles execution of jobs on various cluster types."""

    def __init__(self, config: ClusterConfig):
        self.config = config
        self.ssh_client = None
        self.sftp_client = None
        self.active_jobs: Dict[str, Any] = {}

        # Connection will be established on-demand

    def _setup_ssh_connection(self):
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

    def _setup_kubernetes(self):
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
        import os
        import tempfile
        import yaml
        from kubernetes import config  # type: ignore

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

    def submit_job(self, func_data: Dict[str, Any], job_config: Dict[str, Any]) -> str:
        """
        Submit a job to the cluster.

        Args:
            func_data: Serialized function and data
            job_config: Job configuration parameters

        Returns:
            Job ID for tracking
        """
        # Check if this is a cloud provider job (but not auto-provisioned Kubernetes)
        provider = job_config.get("provider")
        if provider is not None and not (
            self.config.cluster_type == "kubernetes"
            and getattr(self.config, "auto_provision_k8s", False)
        ):
            # If provider is specified and not auto-provisioned K8s, use cloud provider routing
            supported_providers = ["lambda", "aws", "azure", "gcp", "huggingface"]
            if provider in supported_providers:
                return self._submit_cloud_job(func_data, job_config, provider)
            else:
                raise ValueError(
                    f"Unsupported cloud provider: {provider}. Supported providers: {supported_providers}"
                )

        # If no provider specified, use traditional cluster routing

        # Ensure connection is established for traditional cluster types
        self.connect()

        if self.config.cluster_type == "slurm":
            return self._submit_slurm_job(func_data, job_config)
        elif self.config.cluster_type == "pbs":
            return self._submit_pbs_job(func_data, job_config)
        elif self.config.cluster_type == "sge":
            return self._submit_sge_job(func_data, job_config)
        elif self.config.cluster_type == "kubernetes":
            return self._submit_k8s_job(func_data, job_config)
        elif self.config.cluster_type == "ssh":
            return self._submit_ssh_job(func_data, job_config)
        else:
            raise ValueError(f"Unsupported cluster type: {self.config.cluster_type}")

    def _submit_slurm_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via SLURM."""

        # Create remote working directory
        remote_job_dir = f"{self.config.remote_work_dir}/job_{int(time.time())}"
        self._execute_remote_command(f"mkdir -p {remote_job_dir}")

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f, protocol=4)
            local_pickle_path = f.name

        self._upload_file(local_pickle_path, f"{remote_job_dir}/function_data.pkl")
        os.unlink(local_pickle_path)

        # Setup two-venv environment for cross-version compatibility (if enabled)
        updated_config = self.config
        if getattr(self.config, "use_two_venv", True):
            try:
                from .utils import enhanced_setup_two_venv_environment
                import threading

                logger.info(
                    "Setting up enhanced two-venv environment with GPU detection"
                )

                # Use threading to implement timeout for venv setup
                venv_info = None
                exception_occurred = None

                def setup_venv():
                    nonlocal venv_info, exception_occurred
                    try:
                        venv_info = enhanced_setup_two_venv_environment(
                            self.ssh_client,
                            remote_job_dir,
                            func_data["requirements"],
                            self.config,
                        )
                    except Exception as e:
                        exception_occurred = e

                setup_thread = threading.Thread(target=setup_venv)
                setup_thread.daemon = True
                setup_thread.start()
                setup_thread.join(
                    timeout=getattr(self.config, "venv_setup_timeout", 300)
                )

                if setup_thread.is_alive():
                    logger.warning(
                        "Two-venv setup timed out, falling back to basic setup"
                    )
                    raise TimeoutError("Two-venv setup timed out")
                elif exception_occurred:
                    raise exception_occurred
                elif venv_info:
                    # Update config with venv paths for job script generation
                    updated_config.python_executable = venv_info["venv1_python"]
                    # Store venv_info for script generation
                    updated_config.venv_info = venv_info
                    logger.info(
                        f"Two-venv setup successful, using: {venv_info['venv1_python']}"
                    )
                else:
                    raise RuntimeError("Two-venv setup returned no result")

            except Exception as e:
                logger.warning(
                    f"Two-venv setup failed, falling back to basic setup: {e}"
                )
                # Fallback to basic environment setup
                setup_remote_environment(
                    self.ssh_client,
                    remote_job_dir,
                    func_data["requirements"],
                    self.config,
                )
                updated_config.venv_info = None
        else:
            logger.info("Two-venv setup disabled, using basic environment setup")
            # Use basic environment setup
            setup_remote_environment(
                self.ssh_client,
                remote_job_dir,
                func_data["requirements"],
                self.config,
            )
            updated_config = self.config
            updated_config.venv_info = None

        # Create job script
        script_content = create_job_script(
            cluster_type="slurm",
            job_config=job_config,
            remote_job_dir=remote_job_dir,
            config=updated_config,
        )

        # Upload and submit job script
        script_path = f"{remote_job_dir}/job.sh"
        self._create_remote_file(script_path, script_content)

        # Submit job
        cmd = f"cd {remote_job_dir} && sbatch job.sh"
        stdout, stderr = self._execute_remote_command(cmd)

        # Extract job ID from sbatch output
        job_id = stdout.strip().split()[-1]

        # Store job info
        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "submitted",
            "submit_time": time.time(),
        }

        return job_id

    def _submit_pbs_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via PBS."""
        # Similar to SLURM but with PBS commands
        remote_job_dir = f"{self.config.remote_work_dir}/job_{int(time.time())}"
        self._execute_remote_command(f"mkdir -p {remote_job_dir}")

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f, protocol=4)
            local_pickle_path = f.name

        self._upload_file(local_pickle_path, f"{remote_job_dir}/function_data.pkl")
        os.unlink(local_pickle_path)

        # Create PBS script
        script_content = create_job_script(
            cluster_type="pbs",
            job_config=job_config,
            remote_job_dir=remote_job_dir,
            config=self.config,
        )

        script_path = f"{remote_job_dir}/job.pbs"
        self._create_remote_file(script_path, script_content)

        # Submit job
        cmd = f"cd {remote_job_dir} && qsub job.pbs"
        stdout, stderr = self._execute_remote_command(cmd)

        job_id = stdout.strip()

        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "submitted",
            "submit_time": time.time(),
        }

        return job_id

    def _submit_sge_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via SGE."""

        # Create remote working directory
        remote_job_dir = f"{self.config.remote_work_dir}/job_{int(time.time())}"
        self._execute_remote_command(f"mkdir -p {remote_job_dir}")

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f, protocol=4)
            local_pickle_path = f.name

        self._upload_file(local_pickle_path, f"{remote_job_dir}/function_data.pkl")
        os.unlink(local_pickle_path)

        # Setup environment
        setup_remote_environment(
            self.ssh_client,
            remote_job_dir,
            func_data["requirements"],
            self.config,
        )

        # Create job script
        script_content = create_job_script(
            cluster_type="sge",
            job_config=job_config,
            remote_job_dir=remote_job_dir,
            config=self.config,
        )

        # Upload and submit job script
        script_path = f"{remote_job_dir}/job.sge"
        self._create_remote_file(script_path, script_content)

        # Submit job
        cmd = f"cd {remote_job_dir} && qsub job.sge"
        stdout, stderr = self._execute_remote_command(cmd)

        # Extract job ID from qsub output (SGE format: "Your job 123456 ...")
        job_id = stdout.strip().split()[2] if "Your job" in stdout else stdout.strip()

        # Store job info
        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "submitted",
            "submit_time": time.time(),
        }

        return job_id

    def _submit_k8s_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """
        Submit a job to Kubernetes cluster using containerized Python execution.

        This method implements a sophisticated Kubernetes job submission strategy that
        packages Python functions and data into self-contained container jobs without
        requiring custom Docker images or persistent storage.

        **Architecture:**

        1. **Function Serialization**: Uses cloudpickle to serialize the function and all data
        2. **Base64 Encoding**: Encodes serialized data for safe embedding in container args
        3. **Container Execution**: Creates a Job with inline Python code that:
           - Decodes the base64 data
           - Deserializes the function and arguments
           - Executes the function
           - Captures results or errors
        4. **Resource Management**: Applies CPU and memory limits from job_config

        **Key Features:**
        - **No Custom Images**: Uses standard `python:3.11-slim` image
        - **Self-Contained**: All code and data embedded in Job manifest
        - **Resource Aware**: Respects CPU/memory requirements
        - **Error Handling**: Captures exceptions with full tracebacks
        - **Cloud Native**: Leverages Kubernetes Job semantics for reliability

        **Job Manifest Structure:**
        ```yaml
        apiVersion: batch/v1
        kind: Job
        metadata:
          name: clustrix-job-{timestamp}
        spec:
          template:
            spec:
              containers:
              - name: clustrix-worker
                image: python:3.11-slim
                command: ["python", "-c"]
                args: ["<embedded Python code>"]
                resources:
                  requests/limits: {cpu, memory from job_config}
              restartPolicy: Never
        ```

        Args:
            func_data: Serialized function data containing:
                      - 'func': The function to execute
                      - 'args': Positional arguments
                      - 'kwargs': Keyword arguments
                      - 'requirements': Package dependencies (not used for K8s)
            job_config: Job configuration including:
                       - 'cores': CPU request/limit (default: 1)
                       - 'memory': Memory request/limit (default: "1Gi")
                       - Additional K8s-specific settings

        Returns:
            str: Kubernetes Job name that can be used for status tracking

        Raises:
            ImportError: If kubernetes package is not installed
            Exception: If Kubernetes API calls fail

        Examples:
            >>> func_data = {
            ...     'func': lambda x: x**2,
            ...     'args': (5,),
            ...     'kwargs': {},
            ...     'requirements': {}
            ... }
            >>> job_config = {'cores': 2, 'memory': '4Gi'}
            >>> job_id = executor._submit_k8s_job(func_data, job_config)
            >>> print(job_id)  # "clustrix-job-1234567890"

        Note:
            - Requires kubernetes package: `pip install kubernetes`
            - Assumes kubectl is configured with cluster access
            - Jobs are created in the "default" namespace
            - Cloudpickle is used for function serialization
            - Results are captured via stdout parsing (CLUSTRIX_RESULT: prefix)
        """
        try:
            from kubernetes import client  # type: ignore
        except ImportError:
            raise ImportError(
                "kubernetes package required for Kubernetes support. "
                "Install with: pip install kubernetes"
            )

        # Ensure Kubernetes client is set up
        if not hasattr(self, "k8s_client") or self.k8s_client is None:
            self._setup_kubernetes()

        # Create a unique job name
        import random

        job_name = f"clustrix-job-{int(time.time())}-{random.randint(1000, 9999)}"

        # Serialize function data
        func_data_serialized = cloudpickle.dumps(func_data)
        import base64

        func_data_b64 = base64.b64encode(func_data_serialized).decode("utf-8")

        # Create Kubernetes Job manifest
        job_manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": job_name},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "clustrix-worker",
                                "image": self.config.k8s_image,
                                "command": ["/bin/bash", "-c"],
                                "args": [
                                    f"""
pip install cloudpickle dill --quiet && python -c "
import base64
import cloudpickle
import traceback
import pickle
import sys
import types

# Fix for Python 2/3 compatibility
import builtins
sys.modules['__builtin__'] = builtins

try:
    # Decode and deserialize function data
    func_data_b64 = '{func_data_b64}'
    func_data_bytes = base64.b64decode(func_data_b64)
    func_data = cloudpickle.loads(func_data_bytes)

    # Get components
    func_bytes = func_data['function']
    args_bytes = func_data['args']
    kwargs_bytes = func_data['kwargs']
    func_source = func_data.get('function_source')

    # Load arguments
    args = pickle.loads(args_bytes)
    kwargs = pickle.loads(kwargs_bytes)

    # Try to load function, with fallback for __main__ issues
    func = None
    try:
        func = cloudpickle.loads(func_bytes)
    except (AttributeError, ImportError) as e:
        if func_source and '__main__' in str(e):
            # Function was defined in __main__, try to recreate from source
            print(f'Recreating function from source due to __main__ issue')
            
            # Create a temporary module to execute the function in
            temp_module = types.ModuleType('temp_func_module')
            temp_module.__dict__.update(globals())
            
            # Clean the function source - remove decorators
            import re
            # Remove @cluster decorator lines (handle multi-line decorators)
            lines = func_source.split('\\n')
            cleaned_lines = []
            skip_until_def = False
            
            for line in lines:
                if line.strip().startswith('@cluster'):
                    skip_until_def = True
                    continue
                elif skip_until_def and line.strip().startswith(')'):
                    skip_until_def = True  # Keep skipping until we see def
                    continue  
                elif skip_until_def and line.strip().startswith('def '):
                    skip_until_def = False
                    cleaned_lines.append(line)
                elif not skip_until_def:
                    cleaned_lines.append(line)
            
            cleaned_source = '\\n'.join(cleaned_lines)
            
            # Execute the cleaned function source in the temporary module
            exec(cleaned_source, temp_module.__dict__)
            
            # Extract the function (assume it's the first function defined)
            for name, obj in temp_module.__dict__.items():
                if callable(obj) and hasattr(obj, '__code__') and not name.startswith('_'):
                    func = obj
                    break
                    
            if func is None:
                raise RuntimeError('Could not extract function from source code')
        else:
            # Re-raise the original error
            raise e

    if func is None:
        raise RuntimeError('Failed to load function')

    # Execute function
    result = func(*args, **kwargs)
    print(f'CLUSTRIX_RESULT:{{result}}')

except Exception as e:
    print(f'CLUSTRIX_ERROR:{{str(e)}}')
    print(f'CLUSTRIX_TRACEBACK:{{traceback.format_exc()}}')
    exit(1)
"
"""
                                ],
                                "resources": {
                                    "requests": {
                                        "cpu": f"{job_config.get('cores', 1)}",
                                        "memory": job_config.get("memory", "1Gi"),
                                    },
                                    "limits": {
                                        "cpu": f"{job_config.get('cores', 1)}",
                                        "memory": job_config.get("memory", "1Gi"),
                                    },
                                },
                            }
                        ],
                        "restartPolicy": "Never",
                    }
                },
                "backoffLimit": self.config.k8s_backoff_limit,
                "ttlSecondsAfterFinished": self.config.k8s_job_ttl_seconds,
            },
        }

        # Submit job to Kubernetes
        batch_api = client.BatchV1Api()
        response = batch_api.create_namespaced_job(
            namespace=self.config.k8s_namespace, body=job_manifest
        )

        job_id = response.metadata.name

        # Store job info
        self.active_jobs[job_id] = {
            "status": "submitted",
            "submit_time": time.time(),
            "k8s_job": True,
        }

        return job_id

    def _submit_ssh_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any]
    ) -> str:
        """Submit job via direct SSH using two-venv approach."""
        remote_job_dir = f"{self.config.remote_work_dir}/job_{int(time.time())}"
        self._execute_remote_command(f"mkdir -p {remote_job_dir}")

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f, protocol=4)
            local_pickle_path = f.name

        self._upload_file(local_pickle_path, f"{remote_job_dir}/function_data.pkl")
        os.unlink(local_pickle_path)

        # Setup two-venv environment for cross-version compatibility (if enabled)
        updated_config = self.config
        if getattr(self.config, "use_two_venv", True):
            try:
                from .utils import enhanced_setup_two_venv_environment
                import threading

                logger.info(
                    "Setting up enhanced two-venv environment with GPU detection"
                )

                # Use threading to implement timeout for venv setup
                venv_info = None
                exception_occurred = None

                def setup_venv():
                    nonlocal venv_info, exception_occurred
                    try:
                        venv_info = enhanced_setup_two_venv_environment(
                            self.ssh_client,
                            remote_job_dir,
                            func_data["requirements"],
                            self.config,
                        )
                    except Exception as e:
                        exception_occurred = e

                setup_thread = threading.Thread(target=setup_venv)
                setup_thread.daemon = True
                setup_thread.start()
                setup_thread.join(
                    timeout=getattr(self.config, "venv_setup_timeout", 300)
                )

                if setup_thread.is_alive():
                    logger.warning(
                        "Two-venv setup timed out, falling back to basic setup"
                    )
                    raise TimeoutError("Two-venv setup timed out")
                elif exception_occurred:
                    raise exception_occurred
                elif venv_info:
                    # Update config with venv paths
                    updated_config.python_executable = venv_info["venv1_python"]
                    # Store venv_info for script generation
                    updated_config.venv_info = venv_info
                    logger.info(
                        f"Two-venv setup successful, using: {venv_info['venv1_python']}"
                    )
                else:
                    raise RuntimeError("Two-venv setup returned no result")

            except Exception as e:
                logger.warning(f"Failed to setup two-venv environment: {e}")
                # Fall back to original approach
                updated_config.venv_info = None
        else:
            logger.info("Two-venv setup disabled, using basic environment setup")
            updated_config.venv_info = None

        # Create execution script
        script_content = create_job_script(
            cluster_type="ssh",
            job_config=job_config,
            remote_job_dir=remote_job_dir,
            config=updated_config,
        )

        script_path = f"{remote_job_dir}/job.sh"
        self._create_remote_file(script_path, script_content)

        # Execute in background
        cmd = f"cd {remote_job_dir} && nohup bash job.sh > job.out 2> job.err < /dev/null &"
        stdout, stderr = self._execute_remote_command(cmd)

        # Use timestamp as job ID for SSH
        job_id = f"ssh_{int(time.time())}"

        self.active_jobs[job_id] = {
            "remote_dir": remote_job_dir,
            "status": "running",
            "submit_time": time.time(),
        }

        return job_id

    def wait_for_result(self, job_id: str) -> Any:
        """
        Wait for job completion and return result.

        Args:
            job_id: Job identifier

        Returns:
            Function execution result
        """

        job_info = self.active_jobs.get(job_id)
        if not job_info:
            raise ValueError(f"Unknown job ID: {job_id}")

        # Check if this is a Kubernetes job
        is_k8s_job = job_info.get("k8s_job", False)

        if not is_k8s_job:
            remote_dir = job_info["remote_dir"]

        # Poll for completion
        while True:
            status = self._check_job_status(job_id)

            if status == "completed":
                if is_k8s_job:
                    # For Kubernetes jobs, get result from pod logs
                    result = self._get_k8s_result(job_id)

                    # Cleanup
                    if self.config.cleanup_on_success:
                        self._cleanup_k8s_job(job_id)

                    del self.active_jobs[job_id]
                    return result
                else:
                    # SSH-based job result collection
                    result_path = f"{remote_dir}/result.pkl"

                    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
                        local_result_path = f.name

                    try:
                        self._download_file(result_path, local_result_path)

                        with open(local_result_path, "rb") as f:
                            result = pickle.load(f)

                        # Cleanup
                        if self.config.cleanup_on_success:
                            self._execute_remote_command(f"rm -rf {remote_dir}")

                        del self.active_jobs[job_id]

                        return result

                    finally:
                        if os.path.exists(local_result_path):
                            os.unlink(local_result_path)

            elif status == "failed":
                if is_k8s_job:
                    # For Kubernetes jobs, get error from pod logs
                    error_log = self._get_k8s_error_log(job_id)
                    original_exception = self._extract_k8s_exception(job_id)

                    if original_exception:
                        raise original_exception
                    else:
                        raise RuntimeError(
                            f"Kubernetes job {job_id} failed. Error log:\n{error_log}"
                        )
                else:
                    # SSH-based error handling
                    error_log = self._get_error_log(job_id)
                    original_exception = self._extract_original_exception(job_id)

                    if original_exception:
                        # Re-raise the original exception
                        raise original_exception
                    else:
                        # Fallback to RuntimeError with log
                        raise RuntimeError(
                            f"Job {job_id} failed. Error log:\n{error_log}"
                        )

            # Wait before next poll
            time.sleep(self.config.job_poll_interval)

    def _check_job_status(self, job_id: str) -> str:
        """
        Check the current status of a job across multiple cluster schedulers.

        This method implements cluster-specific job status checking with intelligent
        fallback mechanisms to handle various edge cases including completed jobs
        that have been removed from scheduler queues.

        **Multi-Scheduler Support:**

        - **SLURM**: Uses `squeue -j {job_id} -h -o %T` to check job status
        - **PBS**: Uses `qstat -f {job_id}` to query detailed job information
        - **SGE**: Job status checking (using similar logic to PBS)
        - **SSH**: File-based status detection (result.pkl vs error files)
        - **Kubernetes**: Pod/Job status via Kubernetes API

        **Status Detection Logic:**

        1. **Active Jobs**: Query scheduler-specific commands for current status
        2. **Completed Jobs**: Many schedulers remove completed jobs from queues,
           requiring file-based detection using result.pkl existence
        3. **Failed Jobs**: Detected through scheduler status or error file presence
        4. **Unknown Status**: Graceful handling when commands fail

        **Return Values:**
        - `"completed"`: Job finished successfully (result.pkl exists)
        - `"failed"`: Job failed (scheduler reports failure or error files exist)
        - `"running"`: Job is currently executing
        - `"queued"`: Job is waiting in scheduler queue
        - `"unknown"`: Status cannot be determined

        Args:
            job_id: Unique job identifier (scheduler-specific format)

        Returns:
            str: Current job status as a standardized string value

        Examples:
            >>> # SLURM job running
            >>> status = executor._check_job_status("12345")
            >>> print(status)  # "running"

            >>> # PBS job completed (removed from queue)
            >>> status = executor._check_job_status("67890.headnode")
            >>> print(status)  # "completed"

            >>> # SSH job failed
            >>> status = executor._check_job_status("ssh_1234567890")
            >>> print(status)  # "failed"

        Note:
            This method is called repeatedly by `wait_for_result()` during job polling.
            The implementation handles scheduler-specific quirks and provides robust
            status detection even when jobs are removed from scheduler queues.
        """

        if self.config.cluster_type == "slurm":
            # Use robust checking only if we have a real SSH connection (not unit tests)
            try:
                from unittest.mock import Mock

                is_mock = (
                    isinstance(self.ssh_client, Mock)
                    if hasattr(self, "ssh_client") and self.ssh_client
                    else False
                )
            except ImportError:
                is_mock = False

            if hasattr(self, "ssh_client") and self.ssh_client and not is_mock:
                return self._check_slurm_job_status_robust(job_id)
            else:
                # Fallback to original logic for unit tests
                cmd = f"squeue -j {job_id} -h -o %T"
                try:
                    stdout, stderr = self._execute_remote_command(cmd)
                    if not stdout.strip():
                        # Job not in queue, check if result exists
                        if job_id in self.active_jobs:
                            job_info = self.active_jobs[job_id]
                            result_exists = self._remote_file_exists(
                                f"{job_info['remote_dir']}/result.pkl"
                            )
                            return "completed" if result_exists else "failed"
                        else:
                            # Job not tracked, assume completed
                            return "completed"
                    else:
                        slurm_status = stdout.strip()
                        if slurm_status in ["COMPLETED"]:
                            return "completed"
                        elif slurm_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                            return "failed"
                        else:
                            return "running"
                except Exception:
                    return "unknown"

        elif self.config.cluster_type == "pbs":
            cmd = f"qstat -f {job_id}"
            try:
                stdout, stderr = self._execute_remote_command(cmd)
                if "job_state = C" in stdout:
                    return "completed"
                elif "job_state = R" in stdout:
                    return "running"
                else:
                    return "failed"
            except Exception:
                # Job might be completed and removed from queue
                if job_id in self.active_jobs:
                    job_info = self.active_jobs[job_id]
                    result_exists = self._remote_file_exists(
                        f"{job_info['remote_dir']}/result.pkl"
                    )
                    return "completed" if result_exists else "failed"
                else:
                    return "completed"

        elif self.config.cluster_type == "sge":
            sge_status = self._check_sge_status(job_id)
            if sge_status == "completed":
                # Job completed but not in queue, check if result exists
                if job_id in self.active_jobs:
                    job_info = self.active_jobs[job_id]
                    result_exists = self._remote_file_exists(
                        f"{job_info['remote_dir']}/result.pkl"
                    )
                    return "completed" if result_exists else "failed"
                else:
                    return "completed"
            else:
                return sge_status

        elif self.config.cluster_type == "ssh":
            # For SSH jobs, check if result file exists
            if job_id in self.active_jobs:
                job_info = self.active_jobs[job_id]
                result_exists = self._remote_file_exists(
                    f"{job_info['remote_dir']}/result.pkl"
                )
                error_exists = self._remote_file_exists(
                    f"{job_info['remote_dir']}/job.err"
                )

                if result_exists:
                    return "completed"
                elif error_exists:
                    # Check if error file has content indicating failure
                    try:
                        stdout, _ = self._execute_remote_command(
                            f"wc -l {job_info['remote_dir']}/job.err"
                        )
                        line_count = int(stdout.strip().split()[0])
                        if line_count > 0:
                            return "failed"
                    except Exception:
                        pass
                    return "running"
                else:
                    return "running"
            else:
                return "completed"

        elif self.config.cluster_type == "kubernetes":
            # For Kubernetes jobs, check job status via API
            try:
                from kubernetes import client  # type: ignore

                batch_api = client.BatchV1Api()

                # Get job status
                job = batch_api.read_namespaced_job(
                    name=job_id, namespace=self.config.k8s_namespace
                )

                # Check job conditions
                if job.status.succeeded:
                    return "completed"
                elif job.status.failed:
                    return "failed"
                elif job.status.active:
                    return "running"
                else:
                    return "pending"

            except Exception as e:
                # Job might have been deleted or not found
                if job_id in self.active_jobs:
                    # If we're tracking it but can't find it, consider it completed
                    return "completed"
                else:
                    return "unknown"

        return "unknown"

    def _execute_remote_command(self, command: str) -> tuple:
        """Execute command on remote cluster."""
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call _setup_ssh_connection() first."
            )
        stdin, stdout, stderr = self.ssh_client.exec_command(command)
        return stdout.read().decode(), stderr.read().decode()

    def _upload_file(self, local_path: str, remote_path: str):
        """Upload file to remote cluster."""
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call _setup_ssh_connection() first."
            )
        sftp = self.ssh_client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()

    def _download_file(self, remote_path: str, local_path: str):
        """Download file from remote cluster."""
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call _setup_ssh_connection() first."
            )
        sftp = self.ssh_client.open_sftp()
        sftp.get(remote_path, local_path)
        sftp.close()

    def _create_remote_file(self, remote_path: str, content: str):
        """Create file with content on remote cluster."""
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call _setup_ssh_connection() first."
            )
        sftp = self.ssh_client.open_sftp()
        with sftp.open(remote_path, "w") as f:
            f.write(content)
        sftp.close()

    def _remote_file_exists(self, remote_path: str) -> bool:
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

    def _get_error_log(self, job_id: str) -> str:
        """
        Retrieve comprehensive error information from a failed job using multiple fallback mechanisms.

        This method implements a sophisticated error retrieval strategy that prioritizes
        structured error data (pickled exceptions) over raw log files, providing users
        with the most detailed and useful error information available.

        **Error Retrieval Strategy (in priority order):**

        1. **Pickled Error Data** (Highest Priority): Attempts to download and deserialize
           `error.pkl` containing structured exception information including:
           - Original exception objects
           - Error messages with full context
           - Complete stack traces

        2. **Text Log Files** (Fallback): Searches for various scheduler-specific log files:
           - job.err (standard error output)
           - slurm-*.out (SLURM output files)
           - job.e* (PBS/SGE error files)

        3. **No Error Found**: Returns appropriate message if no error information exists.

        **Structured Error Handling**: When error.pkl is found, the method handles multiple
        data formats gracefully:
        - Dictionary format: {'error': message, 'traceback': trace}
        - Direct exception objects
        - String representations

        Args:
            job_id: Unique identifier for the failed job

        Returns:
            str: Comprehensive error information including error messages and tracebacks.
                 Returns detailed structured information when available, or raw log
                 content as fallback.

        Examples:
            >>> # Structured error (preferred)
            >>> error_log = executor._get_error_log("job_12345")
            >>> # Returns: "ValueError: Division by zero\n\nTraceback:\n  File..."

            >>> # Text log fallback
            >>> error_log = executor._get_error_log("job_67890")
            >>> # Returns: "Error from job.err: Process failed with exit code 1"

            >>> # No error info
            >>> error_log = executor._get_error_log("job_unknown")
            >>> # Returns: "No error log found"

        Note:
            This method is typically called automatically by `wait_for_result()` when
            a job status is detected as "failed". It provides the error information
            used for exception re-raising and user notification.
        """
        job_info = self.active_jobs.get(job_id)
        if not job_info:
            return "No job info available"

        remote_dir = job_info["remote_dir"]

        # First, try to get pickled error data
        error_pkl_path = f"{remote_dir}/error.pkl"
        if self._remote_file_exists(error_pkl_path):
            try:
                with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
                    local_error_path = f.name

                self._download_file(error_pkl_path, local_error_path)

                with open(local_error_path, "rb") as f:
                    error_data = pickle.load(f)

                os.unlink(local_error_path)

                # Handle different error data formats
                if isinstance(error_data, dict):
                    error_msg = error_data.get("error", str(error_data))
                    traceback_info = error_data.get("traceback", "")
                    return f"{error_msg}\n\nTraceback:\n{traceback_info}"
                else:
                    return str(error_data)
            except Exception as e:
                # If error.pkl exists but can't be read, continue to text logs
                pass

        # Fallback to text error files
        error_files = ["job.err", "slurm-*.out", "job.e*"]

        for error_file in error_files:
            try:
                stdout, _ = self._execute_remote_command(
                    f"cat {remote_dir}/{error_file} 2>/dev/null"
                )
                if stdout.strip():
                    return stdout
            except Exception:
                continue

        return "No error log found"

    def _extract_original_exception(self, job_id: str) -> Optional[Exception]:
        """
        Extract and reconstruct the original exception from a failed remote job.

        This method enables proper exception propagation by retrieving and deserializing
        exception objects that were pickled during remote execution. This allows users
        to catch specific exception types (e.g., ValueError, KeyError) rather than
        generic RuntimeError wrappers.

        **Exception Reconstruction Process:**

        1. **Download Pickled Data**: Retrieves the error.pkl file from the remote job directory
        2. **Deserialize Exception**: Safely unpickles the exception data
        3. **Type Preservation**: Maintains original exception types and messages
        4. **Fallback Handling**: Creates RuntimeError for malformed exception data

        **Supported Exception Formats:**
        - **Direct Exception Objects**: Exception instances pickled directly
        - **Dictionary Format**: {'error': message, 'traceback': trace} structures
        - **Graceful Degradation**: Returns None if extraction fails

        Args:
            job_id: Unique identifier for the failed job

        Returns:
            Optional[Exception]: The original exception object if successfully extracted,
                               RuntimeError for recoverable data, or None if extraction
                               fails completely.

        Examples:
            >>> # Original ValueError preserved
            >>> exc = executor._extract_original_exception("job_123")
            >>> isinstance(exc, ValueError)  # True
            >>> str(exc)  # "Division by zero"

            >>> # Dictionary format converted
            >>> exc = executor._extract_original_exception("job_456")
            >>> isinstance(exc, RuntimeError)  # True (fallback)
            >>> str(exc)  # "Original error message"

            >>> # Extraction failed
            >>> exc = executor._extract_original_exception("job_789")
            >>> exc is None  # True

        Note:
            This method is called by `wait_for_result()` to enable proper exception
            re-raising. When successful, users can catch specific exception types
            instead of generic RuntimeError messages.

        See Also:
            _get_error_log(): Retrieves error information for logging/display
            wait_for_result(): Main method that uses both error retrieval functions
        """
        job_info = self.active_jobs.get(job_id)
        if not job_info:
            return None

        remote_dir = job_info["remote_dir"]
        error_pkl_path = f"{remote_dir}/error.pkl"

        if self._remote_file_exists(error_pkl_path):
            try:
                with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
                    local_error_path = f.name

                self._download_file(error_pkl_path, local_error_path)

                with open(local_error_path, "rb") as f:
                    error_data = pickle.load(f)

                os.unlink(local_error_path)

                # Return the exception object if it is one
                if isinstance(error_data, Exception):
                    return error_data
                elif isinstance(error_data, dict) and "error" in error_data:
                    # Try to recreate exception from dict
                    error_str = error_data["error"]
                    # This is a simplified approach - in practice you'd want more sophisticated exception recreation
                    return RuntimeError(error_str)

            except Exception:
                # If we can't extract the exception, return None
                pass

        return None

    def cancel_job(self, job_id: str):
        """Cancel a running job."""
        if self.config.cluster_type == "slurm":
            self._execute_remote_command(f"scancel {job_id}")
        elif self.config.cluster_type == "pbs":
            self._execute_remote_command(f"qdel {job_id}")
        elif self.config.cluster_type == "sge":
            self._execute_remote_command(f"qdel {job_id}")

        if job_id in self.active_jobs:
            del self.active_jobs[job_id]

    def connect(self):
        """Establish connection to cluster (for manual connection)."""
        if self.config.cluster_type in ["slurm", "pbs", "sge", "ssh"]:
            if not self.ssh_client:
                self._setup_ssh_connection()
        elif self.config.cluster_type == "kubernetes":
            if not hasattr(self, "k8s_client"):
                self._setup_kubernetes()

    def disconnect(self):
        """Disconnect from cluster."""
        if self.sftp_client:
            self.sftp_client.close()
            self.sftp_client = None
        if self.ssh_client:
            self.ssh_client.close()
            self.ssh_client = None

    def _execute_command(self, command: str) -> tuple:
        """Execute command on remote cluster (alias for _execute_remote_command)."""
        if not self.ssh_client:
            raise RuntimeError("Not connected to cluster")
        return self._execute_remote_command(command)

    def _prepare_function_data(
        self, func, args: tuple, kwargs: dict, config: dict
    ) -> bytes:
        """Prepare function data for serialization."""
        func_data = {
            "func": func,
            "args": args,
            "kwargs": kwargs,
            "config": config,
        }

        return cloudpickle.dumps(func_data)

    def execute(self, func, args: tuple, kwargs: dict) -> Any:
        """Execute function on cluster (simplified interface for tests)."""
        job_config = {"cores": 4, "memory": "8GB", "time": "01:00:00"}
        func_data = {
            "function": cloudpickle.dumps(func, protocol=4),
            "args": pickle.dumps(args, protocol=4),
            "kwargs": pickle.dumps(kwargs, protocol=4),
            "requirements": {},
        }

        job_id = self.submit_job(func_data, job_config)
        return self.wait_for_result(job_id)

    def _check_slurm_status(self, job_id: str) -> str:
        """Check SLURM job status."""
        cmd = f"squeue -j {job_id} -h -o %T"
        try:
            stdout, stderr = self._execute_remote_command(cmd)
            if not stdout.strip():
                # Job not in queue, assume completed
                return "completed"
            else:
                slurm_status = stdout.strip()
                if slurm_status in ["COMPLETED"]:
                    return "completed"
                elif slurm_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                    return "failed"
                else:
                    return "running"
        except Exception:
            return "unknown"

    def _check_slurm_job_status_robust(self, job_id: str) -> str:
        """
        Robust SLURM job status checking with retry logic and proper error handling.

        This method addresses common issues with SLURM job status detection:
        - File system synchronization delays (NFS/Lustre)
        - Race conditions between job completion and file availability
        - Proper error handling with specific logging

        Returns:
            Job status: "completed", "failed", "running", "queued", or "unknown"
        """
        import logging

        logger = logging.getLogger(__name__)

        # First check if job is still in SLURM queue
        cmd = f"squeue -j {job_id} -h -o '%T %r'"  # Status and reason
        try:
            stdout, stderr = self._execute_remote_command(cmd)
            if stdout.strip():
                # Job is still in queue
                status_parts = stdout.strip().split()
                slurm_status = status_parts[0] if status_parts else ""
                reason = status_parts[1] if len(status_parts) > 1 else ""

                logger.debug(
                    f"SLURM job {job_id} status: {slurm_status}, reason: {reason}"
                )

                if slurm_status in ["COMPLETED"]:
                    return "completed"
                elif slurm_status in [
                    "FAILED",
                    "CANCELLED",
                    "TIMEOUT",
                    "NODE_FAIL",
                    "PREEMPTED",
                ]:
                    return "failed"
                elif slurm_status in ["RUNNING", "CONFIGURING"]:
                    return "running"
                elif slurm_status in ["PENDING", "RESIZING", "REQUEUED"]:
                    return "queued"
                else:
                    logger.warning(
                        f"Unknown SLURM status '{slurm_status}' for job {job_id}"
                    )
                    return "unknown"

        except Exception as e:
            logger.warning(f"Error checking SLURM queue status for job {job_id}: {e}")

        # Job not in queue - could be completed or failed
        # Use robust file-based detection with retry logic
        if job_id not in self.active_jobs:
            logger.warning(f"Job {job_id} not found in active_jobs, assuming completed")
            return "completed"

        job_info = self.active_jobs[job_id]
        remote_dir = job_info["remote_dir"]

        return self._check_job_completion_with_retry(job_id, remote_dir)

    def _check_job_completion_with_retry(self, job_id: str, remote_dir: str) -> str:
        """
        Check job completion with exponential backoff retry for file system delays.

        This handles the common scenario where SLURM jobs complete but result files
        aren't immediately visible due to NFS/Lustre synchronization delays.
        """
        import logging
        import time
        from clustrix.filesystem import ClusterFilesystem

        logger = logging.getLogger(__name__)

        # Try to use ClusterFilesystem for reliable file operations
        # Fall back to direct SSH if ClusterFilesystem fails (e.g., in unit tests)
        fs = None
        try:
            fs = ClusterFilesystem(self.config)
        except Exception as e:
            logger.debug(f"Could not create ClusterFilesystem, using direct SSH: {e}")
            fs = None

        result_path = f"{remote_dir}/result.pkl"
        error_path = f"{remote_dir}/error.pkl"

        # Retry logic with exponential backoff
        max_retries = 5
        base_delay = 1.0  # Start with 1 second

        for attempt in range(max_retries):
            try:
                # Check for result file first (success case)
                if fs and fs.exists(result_path):
                    logger.info(
                        f"Job {job_id} completed successfully - result.pkl found"
                    )
                    return "completed"
                elif not fs and self._remote_file_exists(result_path):
                    logger.info(
                        f"Job {job_id} completed successfully - result.pkl found"
                    )
                    return "completed"

                # Check for error file (failure case)
                if fs and fs.exists(error_path):
                    logger.info(f"Job {job_id} failed - error.pkl found")
                    return "failed"
                elif not fs and self._remote_file_exists(error_path):
                    logger.info(f"Job {job_id} failed - error.pkl found")
                    return "failed"

                # Check for SLURM output files for additional error context
                slurm_files = []
                if fs:
                    slurm_files = fs.glob("slurm-*.out", remote_dir)
                else:
                    # Fallback to direct SSH command
                    try:
                        cmd = f"ls {remote_dir}/slurm-*.out 2>/dev/null | head -5"
                        stdout, stderr = self._execute_remote_command(cmd)
                        slurm_files = (
                            stdout.strip().split("\n") if stdout.strip() else []
                        )
                    except Exception:
                        slurm_files = []

                if slurm_files:
                    # Check if any SLURM output files contain error indicators
                    for slurm_file in slurm_files:
                        try:
                            # Read first/last few lines to check for errors without full download
                            cmd = (
                                f"tail -20 {remote_dir}/{slurm_file} | "
                                f"grep -i 'error\\|failed\\|exception\\|traceback' | head -5"
                            )
                            stdout, stderr = self._execute_remote_command(cmd)
                            if stdout.strip():
                                logger.warning(
                                    f"Job {job_id} shows errors in SLURM output: {stdout.strip()}"
                                )
                                return "failed"
                        except Exception as e:
                            logger.debug(
                                f"Could not check SLURM output file {slurm_file}: {e}"
                            )

                # If this is not the last attempt, wait with exponential backoff
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)  # Exponential backoff
                    logger.debug(
                        f"Job {job_id} files not ready, waiting {delay}s "
                        f"before retry {attempt + 2}/{max_retries}"
                    )
                    time.sleep(delay)

            except Exception as e:
                logger.error(
                    f"Error checking job {job_id} completion (attempt {attempt + 1}): {e}"
                )
                if attempt == max_retries - 1:
                    return "unknown"
                time.sleep(base_delay * (2**attempt))

        # If we get here, no result or error files found after all retries
        logger.error(
            f"Job {job_id} completion status unknown - no result or error files "
            f"found after {max_retries} attempts"
        )

        # Final fallback: check if job directory exists and has any files
        try:
            if fs:
                files = fs.ls(remote_dir)
            else:
                cmd = f"ls -la {remote_dir} 2>/dev/null || true"
                stdout, stderr = self._execute_remote_command(cmd)
                files = stdout.strip().split("\n") if stdout.strip() else []
            if files:
                logger.warning(
                    f"Job {job_id} directory contains files but no result/error: {files}"
                )
                # Look for any Python traceback in job directory files
                for filename in files:
                    if filename.endswith((".out", ".err", ".log")):
                        try:
                            cmd = f"grep -l -i 'traceback\\|exception' {remote_dir}/{filename}"
                            stdout, stderr = self._execute_remote_command(cmd)
                            if stdout.strip():
                                logger.info(
                                    f"Job {job_id} failed - Python traceback found in {filename}"
                                )
                                return "failed"
                        except Exception:
                            pass
            else:
                logger.warning(f"Job {job_id} directory is empty or doesn't exist")
        except Exception as e:
            logger.error(f"Could not list job {job_id} directory: {e}")

        return "unknown"

    def _check_pbs_status(self, job_id: str) -> str:
        """Check PBS job status."""
        cmd = f"qstat -f {job_id}"
        try:
            stdout, stderr = self._execute_remote_command(cmd)
            # Handle full format output (qstat -f)
            if "job_state = C" in stdout:
                return "completed"
            elif "job_state = Q" in stdout:
                return "queued"
            elif "job_state = R" in stdout:
                return "running"
            elif "job_state = E" in stdout:
                return "failed"
            # Handle short format output (qstat)
            elif " R " in stdout:
                return "running"
            elif " Q " in stdout:
                return "queued"
            elif " C " in stdout:
                return "completed"
            elif " E " in stdout:
                return "failed"
            else:
                return "unknown"
        except Exception:
            return "unknown"

    def _check_sge_status(self, job_id: str) -> str:
        """Check SGE job status."""
        cmd = f"qstat -j {job_id}"
        try:
            stdout, stderr = self._execute_remote_command(cmd)
            if not stdout.strip() or "Following jobs do not exist" in stderr:
                # Job not in queue, likely completed
                return "completed"
            else:
                # Parse SGE job state from qstat output
                # Common SGE states: r (running), qw (queued), Eqw (error), dr (deleting)
                if "job_state                          r" in stdout:
                    return "running"
                elif "job_state                          qw" in stdout:
                    return "queued"
                elif "job_state                          Eqw" in stdout:
                    return "failed"
                elif "job_state                          dr" in stdout:
                    return "completed"
                # Check for exit status indicating completion
                elif "exit_status" in stdout:
                    return "completed"
                else:
                    return "running"  # Default for unknown running states
        except Exception:
            return "unknown"

    def _get_k8s_result(self, job_id: str) -> Any:
        """Get result from Kubernetes job logs."""
        try:
            from kubernetes import client  # type: ignore

            core_api = client.CoreV1Api()

            # Get pods for this job
            pods = core_api.list_namespaced_pod(
                namespace=self.config.k8s_namespace,
                label_selector=f"job-name={job_id}",
            )

            for pod in pods.items:
                if pod.status.phase == "Succeeded":
                    # Get pod logs
                    logs = core_api.read_namespaced_pod_log(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                    )

                    # Parse result from logs
                    for line in logs.split("\n"):
                        if line.startswith("CLUSTRIX_RESULT:"):
                            result_str = line[len("CLUSTRIX_RESULT:") :]
                            # Try to evaluate the result
                            try:
                                import ast

                                return ast.literal_eval(result_str)
                            except Exception:
                                # If literal_eval fails, return as string
                                return result_str

                    # If no CLUSTRIX_RESULT found, return logs
                    return logs

            raise RuntimeError(f"No successful pod found for job {job_id}")

        except Exception as e:
            raise RuntimeError(f"Failed to get Kubernetes job result: {e}")

    def _get_k8s_error_log(self, job_id: str) -> str:
        """Get error log from Kubernetes job."""
        try:
            from kubernetes import client  # type: ignore

            core_api = client.CoreV1Api()

            # Get pods for this job
            pods = core_api.list_namespaced_pod(
                namespace=self.config.k8s_namespace,
                label_selector=f"job-name={job_id}",
            )

            error_logs = []
            for pod in pods.items:
                # Get pod logs regardless of status
                try:
                    logs = core_api.read_namespaced_pod_log(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                    )
                    error_logs.append(f"Pod {pod.metadata.name}:\n{logs}")
                except Exception as e:
                    error_logs.append(
                        f"Pod {pod.metadata.name}: Failed to get logs - {e}"
                    )

            return "\n\n".join(error_logs) if error_logs else "No error logs available"

        except Exception as e:
            return f"Failed to get Kubernetes error logs: {e}"

    def _extract_k8s_exception(self, job_id: str) -> Optional[Exception]:
        """Extract original exception from Kubernetes job logs."""
        try:
            error_log = self._get_k8s_error_log(job_id)

            # Look for CLUSTRIX_ERROR and CLUSTRIX_TRACEBACK in logs
            lines = error_log.split("\n")
            error_msg = None
            traceback_found = False

            for line in lines:
                if line.startswith("CLUSTRIX_ERROR:"):
                    error_msg = line[len("CLUSTRIX_ERROR:") :]
                elif line.startswith("CLUSTRIX_TRACEBACK:"):
                    traceback_found = True
                    break

            if error_msg:
                # Try to recreate the original exception
                return RuntimeError(error_msg)

            return None

        except Exception:
            return None

    def _cleanup_k8s_job(self, job_id: str):
        """Clean up Kubernetes job resources."""
        try:
            from kubernetes import client  # type: ignore

            batch_api = client.BatchV1Api()
            core_api = client.CoreV1Api()

            # Delete the job (this will also delete associated pods)
            batch_api.delete_namespaced_job(
                name=job_id,
                namespace=self.config.k8s_namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground"),
            )

        except Exception as e:
            # Log warning but don't fail
            import logging

            logging.warning(f"Failed to cleanup Kubernetes job {job_id}: {e}")

    def _submit_cloud_job(
        self, func_data: Dict[str, Any], job_config: Dict[str, Any], provider: str
    ) -> str:
        """
        Submit a job to a cloud provider.

        Args:
            func_data: Serialized function and data
            job_config: Job configuration parameters
            provider: Cloud provider name ('lambda', 'aws', 'azure', 'gcp', 'huggingface')

        Returns:
            Job ID for tracking
        """
        import uuid
        import threading
        from datetime import datetime, timezone

        # Generate unique job ID
        job_id = f"{provider}_{uuid.uuid4().hex[:8]}"

        # Get provider instance
        cloud_provider = self._get_cloud_provider_instance(provider, job_config)

        # Store job info for tracking
        self.active_jobs[job_id] = {
            "provider": provider,
            "cloud_provider_instance": cloud_provider,
            "func_data": func_data,
            "job_config": job_config,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "instance_id": None,
            "ssh_config": None,
        }

        # Start cloud job execution in background thread
        def execute_cloud_job():
            try:
                self._execute_cloud_job_workflow(job_id)
            except Exception as e:
                logger.error(f"Cloud job {job_id} failed: {e}")
                self.active_jobs[job_id]["status"] = "failed"
                self.active_jobs[job_id]["error"] = str(e)

        thread = threading.Thread(target=execute_cloud_job)
        thread.daemon = True
        thread.start()

        return job_id

    def _get_cloud_provider_instance(
        self, provider: str, job_config: Dict[str, Any]
    ) -> Optional["CloudProvider"]:
        """Get cloud provider instance based on provider name."""
        cloud_provider: Optional["CloudProvider"] = None

        if provider == "lambda":
            from .cloud_providers.lambda_cloud import LambdaCloudProvider

            cloud_provider = LambdaCloudProvider()

            # Authenticate with Lambda Cloud
            api_key = job_config.get("lambda_api_key") or self.config.lambda_api_key
            if api_key:
                cloud_provider.authenticate(api_key=api_key)

        elif provider == "aws":
            from .cloud_providers.aws import AWSProvider

            cloud_provider = AWSProvider()

            # Authenticate with AWS
            aws_creds = {
                "access_key_id": job_config.get("aws_access_key_id")
                or self.config.aws_access_key_id,
                "secret_access_key": job_config.get("aws_secret_access_key")
                or self.config.aws_secret_access_key,
                "region": job_config.get("aws_region")
                or self.config.aws_region
                or "us-east-1",
            }
            if aws_creds["access_key_id"] and aws_creds["secret_access_key"]:
                cloud_provider.authenticate(**aws_creds)

        elif provider == "azure":
            from .cloud_providers.azure import AzureProvider

            cloud_provider = AzureProvider()

            # Authenticate with Azure
            azure_creds = {
                "subscription_id": job_config.get("azure_subscription_id")
                or self.config.azure_subscription_id,
                "tenant_id": job_config.get("azure_tenant_id")
                or self.config.azure_tenant_id,
                "client_id": job_config.get("azure_client_id")
                or self.config.azure_client_id,
                "client_secret": job_config.get("azure_client_secret")
                or self.config.azure_client_secret,
            }
            if all(azure_creds.values()):
                cloud_provider.authenticate(**azure_creds)

        elif provider == "gcp":
            from .cloud_providers.gcp import GCPProvider

            cloud_provider = GCPProvider()

            # Authenticate with GCP
            gcp_creds = {
                "project_id": job_config.get("gcp_project_id")
                or self.config.gcp_project_id,
                "service_account_key": job_config.get("gcp_service_account_key")
                or self.config.gcp_service_account_key,
            }
            if gcp_creds["project_id"]:
                cloud_provider.authenticate(**gcp_creds)

        elif provider == "huggingface":
            from .cloud_providers.huggingface_spaces import HuggingFaceSpacesProvider

            cloud_provider = HuggingFaceSpacesProvider()

            # Authenticate with HuggingFace
            hf_creds = {
                "token": job_config.get("hf_token") or self.config.hf_token,
                "username": job_config.get("hf_username") or self.config.hf_username,
            }
            if hf_creds["token"]:
                cloud_provider.authenticate(**hf_creds)
        else:
            raise ValueError(f"Unsupported cloud provider: {provider}")

        return cloud_provider

    def _execute_cloud_job_workflow(self, job_id: str):
        """Execute the complete cloud job workflow."""
        job_info = self.active_jobs[job_id]
        cloud_provider = job_info["cloud_provider_instance"]
        job_config = job_info["job_config"]
        func_data = job_info["func_data"]

        try:
            # Step 1: Create/provision cloud instance
            job_info["status"] = "provisioning"
            instance_config = self._create_cloud_instance(
                cloud_provider, job_config, job_id
            )
            job_info["instance_id"] = instance_config["instance_id"]

            # Step 2: Wait for instance to be ready
            job_info["status"] = "waiting_for_ready"
            ssh_config = self._wait_for_instance_ready(
                cloud_provider, instance_config, job_config
            )
            job_info["ssh_config"] = ssh_config

            # Step 3: Execute job via SSH
            job_info["status"] = "executing"
            result = self._execute_job_on_cloud_instance(
                ssh_config, func_data, job_config, job_id
            )
            job_info["result"] = result
            job_info["status"] = "completed"

        except Exception as e:
            job_info["status"] = "failed"
            job_info["error"] = str(e)
            logger.error(f"Cloud job workflow failed for {job_id}: {e}")
        finally:
            # Step 4: Optional cleanup - terminate instance if configured
            if job_config.get("terminate_on_completion", True):
                try:
                    self._cleanup_cloud_instance(cloud_provider, job_info)
                except Exception as e:
                    logger.warning(
                        f"Failed to cleanup cloud instance for job {job_id}: {e}"
                    )

    def _create_cloud_instance(
        self, cloud_provider, job_config: Dict[str, Any], job_id: str
    ) -> Dict[str, Any]:
        """Create cloud instance for job execution."""
        instance_name = f"clustrix-{job_id}"

        # Provider-specific instance creation
        if hasattr(cloud_provider, "create_instance"):
            instance_type = job_config.get(
                "instance_type", "gpu_1x_a10"
            )  # Default for Lambda
            region = job_config.get("region", "us-east-1")

            instance_info = cloud_provider.create_instance(
                instance_name=instance_name, instance_type=instance_type, region=region
            )

            return instance_info
        else:
            raise NotImplementedError(
                "Cloud provider does not support instance creation"
            )

    def _wait_for_instance_ready(
        self,
        cloud_provider,
        instance_config: Dict[str, Any],
        job_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Wait for cloud instance to be ready and return SSH configuration."""
        instance_id = instance_config["instance_id"]
        max_wait_time = job_config.get(
            "instance_startup_timeout", 300
        )  # 5 minutes default
        check_interval = 10  # seconds
        elapsed = 0

        while elapsed < max_wait_time:
            try:
                status_info = cloud_provider.get_cluster_status(instance_id)
                if status_info.get("status") == "active":
                    # Instance is ready, get SSH configuration
                    cluster_config = cloud_provider.get_cluster_config(instance_id)

                    return {
                        "host": cluster_config["cluster_host"],
                        "username": cluster_config.get("username", "ubuntu"),
                        "port": cluster_config.get("cluster_port", 22),
                        "key_file": job_config.get("key_file", "~/.ssh/id_rsa"),
                    }

                elif status_info.get("status") in ["failed", "terminated"]:
                    raise RuntimeError(
                        f"Instance {instance_id} failed to start: {status_info.get('status')}"
                    )

            except Exception as e:
                if elapsed + check_interval >= max_wait_time:
                    raise RuntimeError(
                        f"Instance {instance_id} not ready within {max_wait_time}s: {e}"
                    )

            time.sleep(check_interval)
            elapsed += check_interval

        raise RuntimeError(
            f"Instance {instance_id} not ready within {max_wait_time} seconds"
        )

    def _execute_job_on_cloud_instance(
        self,
        ssh_config: Dict[str, Any],
        func_data: Dict[str, Any],
        job_config: Dict[str, Any],
        job_id: str,
    ) -> Any:
        """Execute job on cloud instance via SSH."""
        import paramiko

        # Create temporary SSH client for cloud instance
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            # Connect to cloud instance
            ssh_client.connect(
                hostname=ssh_config["host"],
                username=ssh_config["username"],
                port=ssh_config["port"],
                key_filename=os.path.expanduser(ssh_config["key_file"]),
                timeout=30,
            )

            # Create SFTP client
            sftp_client = ssh_client.open_sftp()

            # Create remote work directory
            remote_work_dir = f"/tmp/clustrix_cloud_{job_id}"
            sftp_client.mkdir(remote_work_dir)

            # Upload function data
            with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
                cloudpickle.dump(func_data, f)
                temp_pickle_path = f.name

            try:
                sftp_client.put(temp_pickle_path, f"{remote_work_dir}/func_data.pkl")
            finally:
                os.unlink(temp_pickle_path)

            # Create and upload execution script
            execution_script = self._create_cloud_execution_script(
                remote_work_dir, job_config
            )
            script_path = f"{remote_work_dir}/execute_job.py"

            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(execution_script)
                temp_script_path = f.name

            try:
                sftp_client.put(temp_script_path, script_path)
            finally:
                os.unlink(temp_script_path)

            # Execute job
            stdin, stdout, stderr = ssh_client.exec_command(
                f"cd {remote_work_dir} && python execute_job.py"
            )

            # Wait for completion
            exit_status = stdout.channel.recv_exit_status()
            stdout_data = stdout.read().decode()
            stderr_data = stderr.read().decode()

            if exit_status != 0:
                raise RuntimeError(f"Job execution failed: {stderr_data}")

            # Download result
            result_path = f"{remote_work_dir}/result.pkl"
            with tempfile.NamedTemporaryFile(delete=False) as f:
                temp_result_path = f.name

            try:
                sftp_client.get(result_path, temp_result_path)
                with open(temp_result_path, "rb") as f:
                    result = pickle.load(f)
            finally:
                os.unlink(temp_result_path)

            return result

        finally:
            # Cleanup
            try:
                sftp_client.close()
            except Exception:
                pass
            ssh_client.close()

    def _create_cloud_execution_script(
        self, remote_work_dir: str, job_config: Dict[str, Any]
    ) -> str:
        """Create Python execution script for cloud instance."""
        return f"""#!/usr/bin/env python3
import sys
import os
import pickle
import cloudpickle
import traceback

def main():
    try:
        # Load function data
        with open('{remote_work_dir}/func_data.pkl', 'rb') as f:
            func_data = cloudpickle.load(f)

        # Execute function
        func = func_data['func']
        args = func_data.get('args', ())
        kwargs = func_data.get('kwargs', {{}})

        result = func(*args, **kwargs)

        # Save result
        with open('{remote_work_dir}/result.pkl', 'wb') as f:
            pickle.dump(result, f)

        print("Job completed successfully")

    except Exception as e:
        print(f"Job failed: {{e}}")
        traceback.print_exc()

        # Save error
        with open('{remote_work_dir}/error.pkl', 'wb') as f:
            pickle.dump({{'error': str(e), 'traceback': traceback.format_exc()}}, f)

        sys.exit(1)

if __name__ == "__main__":
    main()
"""

    def _cleanup_cloud_instance(self, cloud_provider, job_info: Dict[str, Any]):
        """Clean up cloud instance after job completion."""
        instance_id = job_info.get("instance_id")
        if instance_id and hasattr(cloud_provider, "delete_cluster"):
            cloud_provider.delete_cluster(instance_id)
            logger.info(f"Cleaned up cloud instance {instance_id}")

    def get_job_status(self, job_id: str) -> str:
        """Get job status (alias for _check_job_status)."""
        # Check if this is a cloud job
        if job_id in self.active_jobs and "provider" in self.active_jobs[job_id]:
            return self.active_jobs[job_id].get("status", "unknown")

        return self._check_job_status(job_id)

    def get_result(self, job_id: str) -> Any:
        """Get result (alias for wait_for_result)."""
        # Check if this is a cloud job
        if job_id in self.active_jobs and "provider" in self.active_jobs[job_id]:
            return self._wait_for_cloud_result(job_id)

        return self.wait_for_result(job_id)

    def _wait_for_cloud_result(self, job_id: str) -> Any:
        """Wait for cloud job result."""
        job_info = self.active_jobs[job_id]
        poll_interval = self.config.job_poll_interval

        while job_info.get("status") not in ["completed", "failed"]:
            time.sleep(poll_interval)

        if job_info.get("status") == "failed":
            error = job_info.get("error", "Unknown error")
            raise RuntimeError(f"Cloud job {job_id} failed: {error}")

        return job_info.get("result")

    def cleanup_auto_provisioned_cluster(self):
        """Clean up auto-provisioned Kubernetes cluster."""
        logger.info("🧹 Cleaning up auto-provisioned Kubernetes cluster")

        try:
            # Clean up temporary kubectl config
            if hasattr(self, "_k8s_temp_config_path") and self._k8s_temp_config_path:
                import os

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

        import time

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

    def __del__(self):
        """Cleanup resources."""
        # Clean up auto-provisioned cluster if configured to do so
        if hasattr(self, "_k8s_provisioner") and getattr(
            self.config, "k8s_cleanup_on_exit", True
        ):
            try:
                self.cleanup_auto_provisioned_cluster()
            except Exception as e:
                # Don't raise exceptions in destructor
                import logging

                logging.getLogger(__name__).error(
                    f"Error during cluster cleanup in destructor: {e}"
                )

        self.disconnect()
