"""Comprehensive tests for Kubernetes integration and cloud provider features."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import base64
import time
import sys

from clustrix.config import ClusterConfig
from clustrix.executor import ClusterExecutor
from clustrix.cloud_providers import CloudProviderManager, CloudProviderError


class TestKubernetesJobSubmission:
    """Test comprehensive Kubernetes job submission functionality."""

    @pytest.fixture
    def k8s_config(self):
        """Create a Kubernetes configuration for testing."""
        return ClusterConfig(
            cluster_type="kubernetes",
            k8s_namespace="test-namespace",
            k8s_image="python:3.11-slim",
            k8s_service_account="test-account",
            k8s_pull_policy="Always",
            k8s_job_ttl_seconds=7200,
            k8s_backoff_limit=2,
            cleanup_on_success=True,
        )

    @pytest.fixture
    def mock_k8s_client(self):
        """Mock Kubernetes client."""
        with patch("kubernetes.client") as mock_client:
            # Mock BatchV1Api
            mock_batch_api = Mock()
            mock_client.BatchV1Api.return_value = mock_batch_api

            # Mock job creation response
            mock_job_response = Mock()
            mock_job_response.metadata.name = "test-job-123"
            mock_batch_api.create_namespaced_job.return_value = mock_job_response

            # Mock job status
            mock_job_status = Mock()
            mock_job_status.status.succeeded = 1
            mock_job_status.status.failed = None
            mock_job_status.status.active = None
            mock_batch_api.read_namespaced_job.return_value = mock_job_status

            # Mock CoreV1Api for pod logs
            mock_core_api = Mock()
            mock_client.CoreV1Api.return_value = mock_core_api

            # Mock pod listing
            mock_pod = Mock()
            mock_pod.metadata.name = "test-pod-123"
            mock_pod.metadata.namespace = "test-namespace"
            mock_pod.status.phase = "Succeeded"
            mock_pods_response = Mock()
            mock_pods_response.items = [mock_pod]
            mock_core_api.list_namespaced_pod.return_value = mock_pods_response

            # Mock pod logs
            mock_core_api.read_namespaced_pod_log.return_value = "CLUSTRIX_RESULT:42"

            yield mock_client

    @patch("kubernetes.config.load_kube_config")
    def test_kubernetes_job_submission_success(
        self, mock_load_config, k8s_config, mock_k8s_client
    ):
        """Test successful Kubernetes job submission."""
        executor = ClusterExecutor(k8s_config)

        # Mock cloudpickle
        with patch("clustrix.executor.cloudpickle") as mock_cloudpickle:
            mock_cloudpickle.dumps.return_value = b"serialized_data"

            func_data = {
                "func": lambda x: x * 2,
                "args": (21,),
                "kwargs": {},
                "requirements": {},
            }
            job_config = {"cores": 2, "memory": "4Gi"}

            job_id = executor._submit_k8s_job(func_data, job_config)

            # Verify job was submitted
            assert job_id == "test-job-123"

            # Verify Kubernetes API calls
            mock_k8s_client.BatchV1Api().create_namespaced_job.assert_called_once()
            call_args = mock_k8s_client.BatchV1Api().create_namespaced_job.call_args

            # Check namespace
            assert call_args[1]["namespace"] == "test-namespace"

            # Check job manifest
            job_manifest = call_args[1]["body"]
            assert job_manifest["kind"] == "Job"
            assert job_manifest["metadata"]["name"].startswith("clustrix-job-")

            # Check container configuration
            container = job_manifest["spec"]["template"]["spec"]["containers"][0]
            assert container["name"] == "clustrix-worker"
            assert container["image"] == "python:3.11-slim"
            assert container["resources"]["requests"]["cpu"] == "2"
            assert container["resources"]["requests"]["memory"] == "4Gi"

    def test_kubernetes_job_result_collection(self, k8s_config, mock_k8s_client):
        """Test collecting results from Kubernetes job."""
        with patch("kubernetes.config.load_kube_config"):
            executor = ClusterExecutor(k8s_config)

            # Set up active job
            job_id = "test-job-123"
            executor.active_jobs[job_id] = {
                "status": "submitted",
                "submit_time": time.time(),
                "k8s_job": True,
            }

            # Test result collection
            result = executor._get_k8s_result(job_id)
            assert result == 42

    def test_kubernetes_job_error_handling(self, k8s_config, mock_k8s_client):
        """Test error handling in Kubernetes jobs."""
        with patch("kubernetes.config.load_kube_config"):
            executor = ClusterExecutor(k8s_config)

            # Mock failed pod logs
            mock_k8s_client.CoreV1Api().read_namespaced_pod_log.return_value = (
                "CLUSTRIX_ERROR:Division by zero\n"
                "CLUSTRIX_TRACEBACK:Traceback (most recent call last):\n"
                '  File "<string>", line 1, in <module>\n'
                "ZeroDivisionError: division by zero"
            )

            job_id = "failed-job-123"
            error_log = executor._get_k8s_error_log(job_id)

            assert "CLUSTRIX_ERROR:Division by zero" in error_log
            assert "CLUSTRIX_TRACEBACK" in error_log

    def test_kubernetes_job_status_checking(self, k8s_config, mock_k8s_client):
        """Test Kubernetes job status checking."""
        with patch("kubernetes.config.load_kube_config"):
            executor = ClusterExecutor(k8s_config)

            job_id = "test-job-123"
            executor.active_jobs[job_id] = {
                "status": "submitted",
                "submit_time": time.time(),
                "k8s_job": True,
            }

            # Test completed status
            status = executor._check_job_status(job_id)
            assert status == "completed"

            # Test failed status
            mock_k8s_client.BatchV1Api().read_namespaced_job.return_value.status.succeeded = (
                None
            )
            mock_k8s_client.BatchV1Api().read_namespaced_job.return_value.status.failed = (
                1
            )

            status = executor._check_job_status(job_id)
            assert status == "failed"

    def test_kubernetes_job_cleanup(self, k8s_config, mock_k8s_client):
        """Test Kubernetes job cleanup."""
        with patch("kubernetes.config.load_kube_config"):
            executor = ClusterExecutor(k8s_config)

            job_id = "cleanup-job-123"
            executor._cleanup_k8s_job(job_id)

            # Verify deletion was called
            mock_k8s_client.BatchV1Api().delete_namespaced_job.assert_called_once_with(
                name=job_id,
                namespace="test-namespace",
                body=mock_k8s_client.V1DeleteOptions(propagation_policy="Foreground"),
            )


class TestCloudProviderIntegration:
    """Test cloud provider auto-configuration integration."""

    @pytest.fixture
    def aws_config(self):
        """Create AWS configuration for testing."""
        return ClusterConfig(
            cluster_type="kubernetes",
            cloud_provider="aws",
            cloud_auto_configure=True,
            cloud_region="us-west-2",
            eks_cluster_name="test-cluster",
            aws_profile="test-profile",
        )

    @pytest.fixture
    def azure_config(self):
        """Create Azure configuration for testing."""
        return ClusterConfig(
            cluster_type="kubernetes",
            cloud_provider="azure",
            cloud_auto_configure=True,
            cloud_region="westus2",
            aks_cluster_name="test-cluster",
            azure_resource_group="test-rg",
            azure_subscription_id="test-subscription",
        )

    @pytest.fixture
    def gcp_config(self):
        """Create GCP configuration for testing."""
        return ClusterConfig(
            cluster_type="kubernetes",
            cloud_provider="gcp",
            cloud_auto_configure=True,
            cloud_region="us-central1",
            gke_cluster_name="test-cluster",
            gcp_project_id="test-project",
            gcp_zone="us-central1-a",
        )

    def test_aws_auto_configuration_success(self, aws_config):
        """Test successful AWS EKS auto-configuration."""
        with patch("subprocess.run") as mock_run:
            # Mock successful aws eks update-kubeconfig
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Updated context"

            with patch("kubernetes.config.load_kube_config"):
                executor = ClusterExecutor(aws_config)
                executor._setup_kubernetes()

                # Verify aws command was called
                mock_run.assert_called()
                # Find the AWS call in all the calls made
                aws_call_found = False
                for call in mock_run.call_args_list:
                    call_args = call[0][0]
                    if "aws" in call_args:
                        aws_call_found = True
                        assert "eks" in call_args
                        assert "update-kubeconfig" in call_args
                        assert "test-cluster" in call_args
                        assert "us-west-2" in call_args
                        break
                assert aws_call_found, "AWS command was not called"

    def test_azure_auto_configuration_success(self, azure_config):
        """Test successful Azure AKS auto-configuration."""
        with patch("subprocess.run") as mock_run:
            # Mock successful az aks get-credentials
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Merged credentials"

            with patch("kubernetes.config.load_kube_config"):
                executor = ClusterExecutor(azure_config)
                executor._setup_kubernetes()

                # Verify az command was called
                mock_run.assert_called()
                # Find the Azure call in all the calls made
                az_call_found = False
                for call in mock_run.call_args_list:
                    call_args = call[0][0]
                    if "az" in call_args:
                        az_call_found = True
                        assert "aks" in call_args
                        assert "get-credentials" in call_args
                        assert "test-cluster" in call_args
                        assert "test-rg" in call_args
                        break
                assert az_call_found, "Azure command was not called"

    def test_gcp_auto_configuration_success(self, gcp_config):
        """Test successful GCP GKE auto-configuration."""
        with patch("subprocess.run") as mock_run:
            # Mock successful gcloud container clusters get-credentials
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Fetching cluster endpoint"

            with patch("kubernetes.config.load_kube_config"):
                executor = ClusterExecutor(gcp_config)
                executor._setup_kubernetes()

                # Verify gcloud command was called
                mock_run.assert_called()
                # Find the gcloud call in all the calls made
                gcloud_call_found = False
                for call in mock_run.call_args_list:
                    call_args = call[0][0]
                    if "gcloud" in call_args:
                        gcloud_call_found = True
                        assert "container" in call_args
                        assert "clusters" in call_args
                        assert "get-credentials" in call_args
                        assert "test-cluster" in call_args
                        break
                assert gcloud_call_found, "GCloud command was not called"

    def test_cloud_auto_configuration_disabled(self):
        """Test when cloud auto-configuration is disabled."""
        config = ClusterConfig(
            cluster_type="kubernetes",
            cloud_auto_configure=False,
        )

        with patch("kubernetes.config.load_kube_config"):
            executor = ClusterExecutor(config)
            # Should not raise any cloud provider errors
            executor._setup_kubernetes()

    def test_cloud_auto_configuration_failure_fallback(self, aws_config):
        """Test fallback when cloud auto-configuration fails."""
        with patch("subprocess.run") as mock_run:
            # Mock failed aws command
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "Cluster not found"

            with patch("kubernetes.config.load_kube_config"):
                # Should not raise exception, should fallback to manual config
                executor = ClusterExecutor(aws_config)
                executor._setup_kubernetes()


class TestKubernetesConfiguration:
    """Test Kubernetes-specific configuration options."""

    def test_kubernetes_config_defaults(self):
        """Test Kubernetes configuration defaults."""
        config = ClusterConfig(cluster_type="kubernetes")

        assert config.k8s_namespace == "default"
        assert config.k8s_image == "python:3.11-slim"
        assert config.k8s_service_account is None
        assert config.k8s_pull_policy == "IfNotPresent"
        assert config.k8s_job_ttl_seconds == 3600
        assert config.k8s_backoff_limit == 3

    def test_kubernetes_config_customization(self):
        """Test customizing Kubernetes configuration."""
        config = ClusterConfig(
            cluster_type="kubernetes",
            k8s_namespace="custom-namespace",
            k8s_image="python:3.12",
            k8s_service_account="my-service-account",
            k8s_pull_policy="Always",
            k8s_job_ttl_seconds=7200,
            k8s_backoff_limit=5,
        )

        assert config.k8s_namespace == "custom-namespace"
        assert config.k8s_image == "python:3.12"
        assert config.k8s_service_account == "my-service-account"
        assert config.k8s_pull_policy == "Always"
        assert config.k8s_job_ttl_seconds == 7200
        assert config.k8s_backoff_limit == 5

    def test_cloud_provider_config_defaults(self):
        """Test cloud provider configuration defaults."""
        config = ClusterConfig()

        assert config.cloud_provider == "manual"
        assert config.cloud_region is None
        assert config.cloud_auto_configure is False

    def test_aws_specific_config(self):
        """Test AWS-specific configuration."""
        config = ClusterConfig(
            cloud_provider="aws",
            eks_cluster_name="my-cluster",
            aws_profile="production",
        )

        assert config.eks_cluster_name == "my-cluster"
        assert config.aws_profile == "production"

    def test_azure_specific_config(self):
        """Test Azure-specific configuration."""
        config = ClusterConfig(
            cloud_provider="azure",
            aks_cluster_name="my-cluster",
            azure_resource_group="my-rg",
            azure_subscription_id="my-subscription",
        )

        assert config.aks_cluster_name == "my-cluster"
        assert config.azure_resource_group == "my-rg"
        assert config.azure_subscription_id == "my-subscription"

    def test_gcp_specific_config(self):
        """Test GCP-specific configuration."""
        config = ClusterConfig(
            cloud_provider="gcp",
            gke_cluster_name="my-cluster",
            gcp_project_id="my-project",
            gcp_zone="us-central1-a",
        )

        assert config.gke_cluster_name == "my-cluster"
        assert config.gcp_project_id == "my-project"
        assert config.gcp_zone == "us-central1-a"


class TestKubernetesErrorHandling:
    """Test error handling in Kubernetes operations."""

    def test_kubernetes_import_error(self):
        """Test handling of missing kubernetes package."""
        config = ClusterConfig(cluster_type="kubernetes")

        with patch.dict('sys.modules', {'kubernetes': None}):
            executor = ClusterExecutor(config)

            with pytest.raises(ImportError, match="kubernetes package required"):
                executor._setup_kubernetes()

    def test_kubernetes_job_submission_api_error(self):
        """Test handling of Kubernetes API errors during job submission."""
        config = ClusterConfig(cluster_type="kubernetes")

        with patch("kubernetes.config.load_kube_config"):
            with patch("kubernetes.client") as mock_client:
                # Mock API error
                mock_client.BatchV1Api().create_namespaced_job.side_effect = Exception(
                    "API Error"
                )

                executor = ClusterExecutor(config)
                executor._setup_kubernetes()

                func_data = {
                    "func": lambda: 42,
                    "args": (),
                    "kwargs": {},
                    "requirements": {},
                }
                job_config = {"cores": 1, "memory": "1Gi"}

                with pytest.raises(Exception, match="API Error"):
                    executor._submit_k8s_job(func_data, job_config)

    def test_kubernetes_result_collection_no_pods(self):
        """Test result collection when no pods are found."""
        config = ClusterConfig(cluster_type="kubernetes")

        with patch("kubernetes.config.load_kube_config"):
            with patch("kubernetes.client") as mock_client:
                # Mock empty pod list
                mock_pods_response = Mock()
                mock_pods_response.items = []
                mock_client.CoreV1Api().list_namespaced_pod.return_value = (
                    mock_pods_response
                )

                executor = ClusterExecutor(config)
                executor._setup_kubernetes()

                with pytest.raises(RuntimeError, match="No successful pod found"):
                    executor._get_k8s_result("test-job")

    def test_kubernetes_log_collection_error(self):
        """Test error handling when log collection fails."""
        config = ClusterConfig(cluster_type="kubernetes")

        with patch("kubernetes.config.load_kube_config"):
            with patch("kubernetes.client") as mock_client:
                # Mock pod with log collection error
                mock_pod = Mock()
                mock_pod.metadata.name = "test-pod"
                mock_pods_response = Mock()
                mock_pods_response.items = [mock_pod]
                mock_client.CoreV1Api().list_namespaced_pod.return_value = (
                    mock_pods_response
                )

                # Mock log collection failure
                mock_client.CoreV1Api().read_namespaced_pod_log.side_effect = Exception(
                    "Log error"
                )

                executor = ClusterExecutor(config)
                executor._setup_kubernetes()

                error_log = executor._get_k8s_error_log("test-job")
                assert "Failed to get logs - Log error" in error_log


class TestEndToEndKubernetesWorkflow:
    """Test complete Kubernetes workflow from submission to result collection."""

    @patch("kubernetes.config.load_kube_config")
    @patch("kubernetes.client")
    def test_complete_kubernetes_workflow(self, mock_client, mock_load_config):
        """Test complete workflow: submit -> monitor -> collect result."""
        config = ClusterConfig(
            cluster_type="kubernetes",
            k8s_namespace="test",
            cleanup_on_success=True,
        )

        # Set up mocks for successful workflow
        mock_batch_api = Mock()
        mock_core_api = Mock()
        mock_client.BatchV1Api.return_value = mock_batch_api
        mock_client.CoreV1Api.return_value = mock_core_api

        # Mock job creation
        mock_job_response = Mock()
        mock_job_response.metadata.name = "clustrix-job-123"
        mock_batch_api.create_namespaced_job.return_value = mock_job_response

        # Mock job status (completed)
        mock_job_status = Mock()
        mock_job_status.status.succeeded = 1
        mock_job_status.status.failed = None
        mock_job_status.status.active = None
        mock_batch_api.read_namespaced_job.return_value = mock_job_status

        # Mock pod listing and logs
        mock_pod = Mock()
        mock_pod.metadata.name = "test-pod"
        mock_pod.metadata.namespace = "test"
        mock_pod.status.phase = "Succeeded"
        mock_pods_response = Mock()
        mock_pods_response.items = [mock_pod]
        mock_core_api.list_namespaced_pod.return_value = mock_pods_response
        mock_core_api.read_namespaced_pod_log.return_value = (
            "CLUSTRIX_RESULT:Hello World"
        )

        executor = ClusterExecutor(config)

        # Test complete workflow
        func_data = {
            "func": lambda: "Hello World",
            "args": (),
            "kwargs": {},
            "requirements": {},
        }
        job_config = {"cores": 1, "memory": "1Gi"}

        # Submit job
        job_id = executor._submit_k8s_job(func_data, job_config)
        assert job_id == "clustrix-job-123"

        # Check status
        status = executor._check_job_status(job_id)
        assert status == "completed"

        # Collect result
        result = executor._get_k8s_result(job_id)
        assert result == "Hello World"

        # Verify cleanup was called
        executor._cleanup_k8s_job(job_id)
        mock_batch_api.delete_namespaced_job.assert_called_once()
