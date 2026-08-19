"""Cloud provider workflow management for job execution.

This module handles cloud-based job execution workflows including instance
provisioning, SSH-based execution, and cleanup for various cloud providers
(AWS, Azure, GCP, Lambda Labs, HuggingFace).
"""

import os
import secrets
import shlex
import stat
import time
import tempfile
import logging
import threading
import uuid
from typing import Dict, Any, Optional, TYPE_CHECKING
from datetime import datetime, timezone

import paramiko
import cloudpickle
import dill

from .ssh_security import configure_host_key_policy
from .utils import key_capture_lines, result_signing_lines, verify_signed_payload

if TYPE_CHECKING:
    from .cloud_providers.base import CloudProvider

logger = logging.getLogger(__name__)

# What a cloud provider must implement before clustrix can run a job on it.
# `create_instance` is deliberately not on the CloudProvider ABC -- only
# LambdaCloudProvider provisions single instances -- so the gap is checked
# here, at submit time, instead of surfacing as a NotImplementedError from
# inside a background thread once the caller has already been told the job
# was accepted (#119).
REQUIRED_PROVIDER_METHODS = (
    "create_instance",
    "get_cluster_status",
    "get_cluster_config",
)


class CloudJobManager:
    """Manages cloud-based job execution workflows."""

    def __init__(self, config):
        """Initialize cloud job manager.

        Args:
            config: ClusterConfig instance with cloud provider settings
        """
        self.config = config
        self.active_jobs: Dict[str, Any] = {}

    def submit_cloud_job(
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
        # Generate unique job ID
        job_id = f"{provider}_{uuid.uuid4().hex[:8]}"

        # Get provider instance
        cloud_provider = self._get_cloud_provider_instance(provider, job_config)
        self._check_provider_can_run_jobs(provider, cloud_provider)

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

    def _check_provider_can_run_jobs(self, provider: str, cloud_provider) -> None:
        """Refuse a job the provider has no way of running.

        Raises:
            NotImplementedError: if the provider cannot provision instances
            RuntimeError: if the provider was never authenticated
        """
        if cloud_provider is None:
            raise NotImplementedError(
                f"No cloud provider implementation was built for '{provider}'."
            )

        missing = [
            name
            for name in REQUIRED_PROVIDER_METHODS
            if not callable(getattr(cloud_provider, name, None))
        ]
        if missing:
            raise NotImplementedError(
                f"The '{provider}' cloud provider cannot run clustrix jobs: "
                f"{type(cloud_provider).__name__} does not implement "
                f"{', '.join(missing)}. Of the built-in providers only "
                "'lambda' provisions instances for job execution; for the "
                "others, provision the machine yourself and use cluster_type "
                "'ssh', or use cluster_type 'kubernetes'."
            )

        if not cloud_provider.is_authenticated():
            raise RuntimeError(
                f"The '{provider}' cloud provider is not authenticated, so no "
                "instance can be provisioned for this job. Supply its "
                "credentials in the clustrix config or in the job config."
            )

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

        # The provider was checked for create_instance at submit time, so
        # there is no "does it support this?" branch to take here.
        instance_type = job_config.get(
            "instance_type", "gpu_1x_a10"
        )  # Default for Lambda
        region = job_config.get("region", "us-east-1")

        return cloud_provider.create_instance(
            instance_name=instance_name, instance_type=instance_type, region=region
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
            # Only the status poll is retried. An instance that has reached a
            # terminal state, or one that is up but whose connection details
            # cannot be read, is not going to improve -- and both of those
            # raises used to be caught by this loop's own except clause and
            # retried until the timeout, so the real reason arrived five
            # minutes late wearing a "not ready" message.
            try:
                status_info = cloud_provider.get_cluster_status(instance_id)
            except Exception as e:
                if elapsed + check_interval >= max_wait_time:
                    raise RuntimeError(
                        f"Instance {instance_id} not ready within "
                        f"{max_wait_time}s: {e}"
                    ) from e
                logger.warning(
                    f"Could not read the status of instance {instance_id}, "
                    f"retrying: {e}"
                )
                status_info = {}

            status = status_info.get("status")

            if status == "active":
                # Instance is ready, get SSH configuration
                cluster_config = cloud_provider.get_cluster_config(instance_id)

                return {
                    "host": cluster_config["cluster_host"],
                    "username": cluster_config.get("username", "ubuntu"),
                    "port": cluster_config.get("cluster_port", 22),
                    "key_file": job_config.get("key_file", "~/.ssh/id_rsa"),
                }

            if status in ("failed", "terminated"):
                raise RuntimeError(f"Instance {instance_id} failed to start: {status}")

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
        # Create temporary SSH client for cloud instance
        ssh_client = paramiko.SSHClient()
        # The last host-key site in the package that had not been routed
        # through ssh_security: a cloud instance's key was trusted on sight,
        # which is a machine-in-the-middle away from someone else's job.
        configure_host_key_policy(ssh_client, self.config)

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

            # Create remote work directory 0700 and drop a per-job signing
            # key inside it, exactly as the scheduler backends do. The result
            # this instance produces is deserialized with dill on the
            # submitting machine, and dill.loads executes code, so it has to
            # be authenticated -- this path had no key and no check at all.
            remote_work_dir = f"/tmp/clustrix_cloud_{job_id}"
            sftp_client.mkdir(remote_work_dir, mode=0o700)
            result_key = secrets.token_hex(32)
            key_path = f"{remote_work_dir}/.clustrix_result_key"
            with sftp_client.open(key_path, "w") as key_handle:
                key_handle.write(result_key)
            sftp_client.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)

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

            # Execute job. The key is read from the 0600 file rather than
            # passed on the command line, which any user on the box can read
            # out of /proc.
            quoted_dir = shlex.quote(remote_work_dir)
            stdin, stdout, stderr = ssh_client.exec_command(
                f"cd {quoted_dir} && "
                f"export CLUSTRIX_RESULT_KEY=$(cat {shlex.quote(key_path)}) && "
                "python execute_job.py"
            )

            # Wait for completion
            exit_status = stdout.channel.recv_exit_status()
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
                    payload = f.read()
            finally:
                os.unlink(temp_result_path)

            try:
                with sftp_client.open(f"{result_path}.hmac") as tag_handle:
                    tag = tag_handle.read().decode()
            except IOError:
                # Absent signature: verify_signed_payload refuses on an empty
                # tag, which is what an unsigned result has to mean.
                tag = ""

            verify_signed_payload(payload, tag, result_key, f"Cloud job {job_id}")
            # dill: the worker writes this with dill (see
            # _create_cloud_execution_script), and stdlib pickle would rebuild
            # __main__ classes instead of reusing the caller's. Safe to
            # deserialize only because the bytes just verified against the
            # per-job key.
            result = dill.loads(payload)

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
        # The one signing implementation, shared with the SSH/SLURM job
        # scripts; `_ser` is the dill alias this script already binds.
        signing = "\n".join(result_signing_lines(indent=" " * 8, serializer="_ser"))
        # Capture the signing key and drop it from the environment before the
        # user's function -- and anything it imports -- gets to run.
        capture = "\n".join(key_capture_lines())
        # The job directory is interpolated into *Python source*, so it needs
        # Python-literal quoting, not bare quotes: repr() escapes an embedded
        # quote (which would otherwise close the literal and let the rest of
        # the value run as code) and any backslash.
        func_data_literal = repr(f"{remote_work_dir}/func_data.pkl")
        error_literal = repr(f"{remote_work_dir}/error.pkl")
        return f"""#!/usr/bin/env python3
import sys
import os
import pickle
import cloudpickle
import traceback

{capture}

def main():
    try:
        # Load function data
        with open({func_data_literal}, 'rb') as f:
            func_data = cloudpickle.load(f)

        # Unpack what serialize_function() actually produced. It stores the
        # function as dill (or cloudpickle) bytes under "function", and the
        # arguments as pickle bytes -- not as live objects. Reading 'func'
        # here raised KeyError on the first line of every cloud job, which is
        # proof this path had never run.
        try:
            import dill as _ser
        except ImportError:
            _ser = cloudpickle
        try:
            func = _ser.loads(func_data['function'])
        except Exception:
            func = cloudpickle.loads(func_data['function'])
        # _ser, not stdlib pickle: args may carry classes defined in the
        # caller's __main__, which pickle can only store by qualified name.
        args = _ser.loads(func_data['args'])
        kwargs = _ser.loads(func_data['kwargs'])

        result = func(*args, **kwargs)

        # Save result, signed with the per-job key. Written with _ser (dill),
        # which is what the caller reads it back with -- it used to be written
        # with stdlib pickle under a comment claiming dill, and with no
        # signature at all.
{signing}

        print("Job completed successfully")

    except Exception as e:
        print(f"Job failed: {{e}}")
        traceback.print_exc()

        # Save error
        with open({error_literal}, 'wb') as f:
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

    def get_cloud_job_status(self, job_id: str) -> str:
        """Get cloud job status."""
        if job_id not in self.active_jobs:
            return "unknown"

        return self.active_jobs[job_id].get("status", "unknown")

    def wait_for_cloud_result(self, job_id: str) -> Any:
        """Wait for cloud job result."""
        job_info = self.active_jobs.get(job_id)
        if not job_info:
            raise ValueError(f"Unknown cloud job ID: {job_id}")

        poll_interval = getattr(self.config, "job_poll_interval", 10)

        while job_info.get("status") not in ["completed", "failed"]:
            time.sleep(poll_interval)

        if job_info.get("status") == "failed":
            error = job_info.get("error", "Unknown error")
            raise RuntimeError(f"Cloud job {job_id} failed: {error}")

        return job_info.get("result")

    def cancel_cloud_job(self, job_id: str):
        """Cancel a running cloud job."""
        if job_id not in self.active_jobs:
            return

        job_info = self.active_jobs[job_id]
        cloud_provider = job_info.get("cloud_provider_instance")
        instance_id = job_info.get("instance_id")

        if cloud_provider and instance_id:
            try:
                # Attempt to terminate the cloud instance
                if hasattr(cloud_provider, "delete_cluster"):
                    cloud_provider.delete_cluster(instance_id)
                    logger.info(
                        f"Terminated cloud instance {instance_id} for job {job_id}"
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to terminate cloud instance for job {job_id}: {e}"
                )

        # Mark job as cancelled
        job_info["status"] = "cancelled"
