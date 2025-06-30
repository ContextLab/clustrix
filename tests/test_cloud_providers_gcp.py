import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from clustrix.cloud_providers.gcp import GCPProvider


class TestGCPProvider:
    """Test GCP provider functionality."""

    @pytest.fixture
    def provider(self):
        """Create GCPProvider instance."""
        return GCPProvider()

    @pytest.fixture
    def authenticated_provider(self):
        """Create authenticated GCPProvider instance."""
        provider = GCPProvider()
        provider.authenticated = True
        provider.project_id = "test-project"
        provider.region = "us-central1"
        provider.zone = "us-central1-a"
        provider.compute_client = Mock()
        provider.container_client = Mock()
        provider.service_account_info = {
            "type": "service_account",
            "project_id": "test-project",
        }
        provider.credentials = {
            "project_id": "test-project",
            "service_account_key": '{"type": "service_account", "project_id": "test-project"}',
        }
        return provider

    def test_initialization(self, provider):
        """Test provider initialization."""
        assert provider.project_id is None
        assert provider.region == "us-central1"
        assert provider.zone == "us-central1-a"
        assert provider.compute_client is None
        assert provider.container_client is None
        assert provider.service_account_info is None
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.gcp.GCP_AVAILABLE", True)
    @patch("clustrix.cloud_providers.gcp.service_account")
    @patch("clustrix.cloud_providers.gcp.compute_v1")
    @patch("clustrix.cloud_providers.gcp.container_v1")
    def test_authenticate_success(
        self, mock_container, mock_compute, mock_service_account, provider
    ):
        """Test successful authentication."""
        # Mock service account credentials
        mock_creds = Mock()
        mock_service_account.Credentials.from_service_account_info.return_value = (
            mock_creds
        )

        # Mock clients
        mock_compute_client = Mock()
        mock_container_client = Mock()
        mock_compute.InstancesClient.return_value = mock_compute_client
        mock_container.ClusterManagerClient.return_value = mock_container_client

        # Mock successful API call
        mock_compute_client.list.return_value = []

        service_account_key = (
            '{"type": "service_account", "project_id": "test-project"}'
        )
        result = provider.authenticate(
            project_id="test-project",
            service_account_key=service_account_key,
            region="us-west1",
        )

        assert result is True
        assert provider.authenticated is True
        assert provider.project_id == "test-project"
        assert provider.region == "us-west1"
        assert provider.zone == "us-west1-a"
        assert provider.compute_client == mock_compute_client
        assert provider.container_client == mock_container_client

    @patch("clustrix.cloud_providers.gcp.GCP_AVAILABLE", False)
    def test_authenticate_gcp_not_available(self, provider):
        """Test authentication when GCP packages not available."""
        result = provider.authenticate(
            project_id="test-project", service_account_key='{"type": "service_account"}'
        )

        assert result is False
        assert not provider.authenticated

    def test_authenticate_missing_credentials(self, provider):
        """Test authentication with missing credentials."""
        result = provider.authenticate(project_id="test-project")
        assert result is False

        result = provider.authenticate(
            service_account_key='{"type": "service_account"}'
        )
        assert result is False

    @patch("clustrix.cloud_providers.gcp.GCP_AVAILABLE", True)
    def test_authenticate_invalid_json(self, provider):
        """Test authentication with invalid JSON."""
        result = provider.authenticate(
            project_id="test-project", service_account_key="invalid json"
        )

        assert result is False
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.gcp.GCP_AVAILABLE", True)
    @patch("clustrix.cloud_providers.gcp.service_account")
    @patch("clustrix.cloud_providers.gcp.compute_v1")
    @patch("clustrix.cloud_providers.gcp.container_v1")
    def test_authenticate_api_failure(
        self, mock_container, mock_compute, mock_service_account, provider
    ):
        """Test authentication with API failure."""
        mock_creds = Mock()
        mock_service_account.Credentials.from_service_account_info.return_value = (
            mock_creds
        )

        mock_compute_client = Mock()
        mock_compute.InstancesClient.return_value = mock_compute_client
        mock_compute_client.list.side_effect = Exception("API error")

        result = provider.authenticate(
            project_id="test-project", service_account_key='{"type": "service_account"}'
        )

        assert result is False
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.gcp.GCP_AVAILABLE", True)
    @patch("clustrix.cloud_providers.gcp.service_account")
    def test_authenticate_credentials_error(self, mock_service_account, provider):
        """Test authentication with credentials error."""
        from clustrix.cloud_providers.gcp import DefaultCredentialsError

        mock_service_account.Credentials.from_service_account_info.side_effect = (
            DefaultCredentialsError()
        )

        result = provider.authenticate(
            project_id="test-project", service_account_key='{"type": "service_account"}'
        )

        assert result is False
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.gcp.GCP_AVAILABLE", True)
    @patch("clustrix.cloud_providers.gcp.service_account")
    def test_authenticate_dict_service_account_key(
        self, mock_service_account, provider
    ):
        """Test authentication with dict service account key."""
        mock_creds = Mock()
        mock_service_account.Credentials.from_service_account_info.return_value = (
            mock_creds
        )

        with patch("clustrix.cloud_providers.gcp.compute_v1") as mock_compute, patch(
            "clustrix.cloud_providers.gcp.container_v1"
        ) as mock_container:
            mock_compute_client = Mock()
            mock_compute.InstancesClient.return_value = mock_compute_client
            mock_compute_client.list.return_value = []

            service_account_dict = {
                "type": "service_account",
                "project_id": "test-project",
            }
            result = provider.authenticate(
                project_id="test-project", service_account_key=service_account_dict
            )

            assert result is True
            assert provider.service_account_info == service_account_dict

    def test_validate_credentials_success(self, authenticated_provider):
        """Test successful credential validation."""
        authenticated_provider.compute_client.list.return_value = []

        result = authenticated_provider.validate_credentials()

        assert result is True
        authenticated_provider.compute_client.list.assert_called_once_with(
            project="test-project", zone="us-central1-a"
        )

    def test_validate_credentials_failure(self, authenticated_provider):
        """Test failed credential validation."""
        authenticated_provider.compute_client.list.side_effect = Exception("API error")

        result = authenticated_provider.validate_credentials()

        assert result is False

    def test_validate_credentials_not_authenticated(self, provider):
        """Test credential validation when not authenticated."""
        result = provider.validate_credentials()

        assert result is False

    def test_validate_credentials_no_client(self, provider):
        """Test credential validation with no compute client."""
        provider.authenticated = True
        provider.compute_client = None

        result = provider.validate_credentials()

        assert result is False

    @patch("clustrix.cloud_providers.gcp.compute_v1")
    @patch("clustrix.cloud_providers.gcp.service_account")
    def test_create_compute_instance_success(
        self, mock_service_account, mock_compute, authenticated_provider
    ):
        """Test successful compute instance creation."""
        # Mock images client
        mock_images_client = Mock()
        mock_image = Mock()
        mock_image.self_link = "projects/ubuntu-os-cloud/global/images/ubuntu-2004-lts"
        mock_images_client.get_from_family.return_value = mock_image
        mock_compute.ImagesClient.return_value = mock_images_client

        # Mock instance creation
        mock_operation = Mock()
        mock_operation.name = "operation-12345"
        authenticated_provider.compute_client.insert.return_value = mock_operation

        with patch("clustrix.cloud_providers.gcp.datetime") as mock_datetime:
            mock_datetime.now.return_value.isoformat.return_value = (
                "2024-01-01T00:00:00+00:00"
            )
            mock_datetime.timezone = timezone

            result = authenticated_provider.create_compute_instance(
                instance_name="test-instance",
                machine_type="e2-medium",
                image_family="ubuntu-2004-lts",
                image_project="ubuntu-os-cloud",
            )

        assert result["instance_name"] == "test-instance"
        assert result["instance_id"] == "test-instance"
        assert result["machine_type"] == "e2-medium"
        assert result["zone"] == "us-central1-a"
        assert result["region"] == "us-central1"
        assert result["status"] == "creating"
        assert result["operation"] == "operation-12345"

        # Verify API calls
        mock_images_client.get_from_family.assert_called_once_with(
            project="ubuntu-os-cloud", family="ubuntu-2004-lts"
        )
        authenticated_provider.compute_client.insert.assert_called_once()

    def test_create_compute_instance_not_authenticated(self, provider):
        """Test compute instance creation when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.create_compute_instance("test-instance")

    @patch("clustrix.cloud_providers.gcp.compute_v1")
    def test_create_compute_instance_exception(
        self, mock_compute, authenticated_provider
    ):
        """Test compute instance creation with exception."""
        mock_images_client = Mock()
        mock_images_client.get_from_family.side_effect = Exception("API error")
        mock_compute.ImagesClient.return_value = mock_images_client

        with pytest.raises(Exception):
            authenticated_provider.create_compute_instance("test-instance")

    @patch("clustrix.cloud_providers.gcp.container_v1")
    def test_create_gke_cluster_success(self, mock_container, authenticated_provider):
        """Test successful GKE cluster creation."""
        mock_operation = Mock()
        mock_operation.name = "operation-gke-12345"
        authenticated_provider.container_client.create_cluster.return_value = (
            mock_operation
        )

        with patch("clustrix.cloud_providers.gcp.datetime") as mock_datetime:
            mock_datetime.now.return_value.isoformat.return_value = (
                "2024-01-01T00:00:00+00:00"
            )
            mock_datetime.timezone = timezone

            result = authenticated_provider.create_gke_cluster(
                cluster_name="test-cluster",
                node_count=5,
                machine_type="e2-standard-4",
                kubernetes_version="1.25.0",
                disk_size_gb=200,
            )

        assert result["cluster_name"] == "test-cluster"
        assert result["status"] == "creating"
        assert result["region"] == "us-central1"
        assert result["zone"] == "us-central1-a"
        assert result["provider"] == "gcp"
        assert result["cluster_type"] == "gke"
        assert result["project_id"] == "test-project"
        assert result["node_count"] == 5
        assert result["machine_type"] == "e2-standard-4"
        assert result["disk_size_gb"] == 200
        assert result["kubernetes_version"] == "1.25.0"
        assert result["operation_name"] == "operation-gke-12345"

        # Verify API call
        authenticated_provider.container_client.create_cluster.assert_called_once()

    def test_create_gke_cluster_not_authenticated(self, provider):
        """Test GKE cluster creation when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.create_gke_cluster("test-cluster")

    def test_create_gke_cluster_exception(self, authenticated_provider):
        """Test GKE cluster creation with exception."""
        authenticated_provider.container_client.create_cluster.side_effect = Exception(
            "API error"
        )

        with pytest.raises(Exception, match="API error"):
            authenticated_provider.create_gke_cluster("test-cluster")

    def test_create_cluster_compute(self, authenticated_provider):
        """Test create_cluster with compute type."""
        with patch.object(
            authenticated_provider, "create_compute_instance"
        ) as mock_create:
            mock_create.return_value = {"instance_id": "test-instance"}

            result = authenticated_provider.create_cluster(
                "test-cluster", cluster_type="compute", machine_type="e2-medium"
            )

            mock_create.assert_called_once_with(
                "test-cluster", machine_type="e2-medium"
            )
            assert result == {"instance_id": "test-instance"}

    def test_create_cluster_gke(self, authenticated_provider):
        """Test create_cluster with GKE type."""
        with patch.object(authenticated_provider, "create_gke_cluster") as mock_create:
            mock_create.return_value = {"cluster_name": "test-cluster"}

            result = authenticated_provider.create_cluster(
                "test-cluster", cluster_type="gke", node_count=3
            )

            mock_create.assert_called_once_with("test-cluster", node_count=3)
            assert result == {"cluster_name": "test-cluster"}

    def test_create_cluster_unknown_type(self, authenticated_provider):
        """Test create_cluster with unknown type."""
        with pytest.raises(ValueError, match="Unknown cluster type"):
            authenticated_provider.create_cluster(
                "test-cluster", cluster_type="unknown"
            )

    def test_delete_cluster_compute_success(self, authenticated_provider):
        """Test successful compute instance deletion."""
        mock_operation = Mock()
        mock_operation.name = "delete-operation-12345"
        authenticated_provider.compute_client.delete.return_value = mock_operation

        result = authenticated_provider.delete_cluster(
            "test-instance", cluster_type="compute"
        )

        assert result is True
        authenticated_provider.compute_client.delete.assert_called_once_with(
            project="test-project", zone="us-central1-a", instance="test-instance"
        )

    def test_delete_cluster_gke_success(self, authenticated_provider):
        """Test successful GKE cluster deletion."""
        mock_operation = Mock()
        mock_operation.name = "delete-gke-operation-12345"
        authenticated_provider.container_client.delete_cluster.return_value = (
            mock_operation
        )

        result = authenticated_provider.delete_cluster(
            "test-cluster", cluster_type="gke"
        )

        assert result is True
        authenticated_provider.container_client.delete_cluster.assert_called_once()

    def test_delete_cluster_not_authenticated(self, provider):
        """Test cluster deletion when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.delete_cluster("test-cluster")

    def test_delete_cluster_unknown_type(self, authenticated_provider):
        """Test cluster deletion with unknown type."""
        result = authenticated_provider.delete_cluster(
            "test-cluster", cluster_type="unknown"
        )
        assert result is False

    def test_delete_cluster_exception(self, authenticated_provider):
        """Test cluster deletion with exception."""
        authenticated_provider.compute_client.delete.side_effect = Exception(
            "API error"
        )

        result = authenticated_provider.delete_cluster(
            "test-instance", cluster_type="compute"
        )

        assert result is False

    def test_get_cluster_status_compute_success(self, authenticated_provider):
        """Test successful compute instance status retrieval."""
        mock_instance = Mock()
        mock_instance.status = "RUNNING"
        mock_instance.machine_type = "zones/us-central1-a/machineTypes/e2-medium"
        authenticated_provider.compute_client.get.return_value = mock_instance

        result = authenticated_provider.get_cluster_status(
            "test-instance", cluster_type="compute"
        )

        assert result["instance_name"] == "test-instance"
        assert result["status"] == "running"
        assert result["machine_type"] == "e2-medium"
        assert result["zone"] == "us-central1-a"
        assert result["provider"] == "gcp"
        assert result["cluster_type"] == "compute"

    def test_get_cluster_status_gke_success(self, authenticated_provider):
        """Test successful GKE cluster status retrieval."""
        mock_cluster = Mock()
        mock_cluster.status.name = "RUNNING"
        mock_cluster.endpoint = "1.2.3.4"
        mock_cluster.current_master_version = "1.25.0"
        mock_cluster.current_node_version = "1.25.0"
        mock_cluster.current_node_count = 3
        mock_cluster.location = "us-central1-a"
        mock_cluster.zone = "us-central1-a"
        mock_cluster.create_time = "2024-01-01T00:00:00Z"
        authenticated_provider.container_client.get_cluster.return_value = mock_cluster

        result = authenticated_provider.get_cluster_status(
            "test-cluster", cluster_type="gke"
        )

        assert result["cluster_name"] == "test-cluster"
        assert result["status"] == "running"
        assert result["endpoint"] == "1.2.3.4"
        assert result["current_master_version"] == "1.25.0"
        assert result["current_node_version"] == "1.25.0"
        assert result["node_count"] == 3
        assert result["location"] == "us-central1-a"
        assert result["zone"] == "us-central1-a"
        assert result["provider"] == "gcp"
        assert result["cluster_type"] == "gke"
        assert result["project_id"] == "test-project"

    def test_get_cluster_status_not_authenticated(self, provider):
        """Test cluster status when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.get_cluster_status("test-cluster")

    def test_get_cluster_status_unknown_type(self, authenticated_provider):
        """Test cluster status with unknown type."""
        with pytest.raises(ValueError, match="Unknown cluster type"):
            authenticated_provider.get_cluster_status(
                "test-cluster", cluster_type="unknown"
            )

    def test_get_cluster_status_exception(self, authenticated_provider):
        """Test cluster status with exception."""
        authenticated_provider.compute_client.get.side_effect = Exception("API error")

        with pytest.raises(Exception, match="API error"):
            authenticated_provider.get_cluster_status(
                "test-instance", cluster_type="compute"
            )

    def test_list_clusters_success(self, authenticated_provider):
        """Test successful cluster listing."""
        # Mock compute instances
        mock_instance = Mock()
        mock_instance.name = "clustrix-instance"
        mock_instance.status = "RUNNING"
        mock_instance.machine_type = "zones/us-central1-a/machineTypes/e2-medium"
        mock_instance.tags = Mock()
        mock_instance.tags.items = ["clustrix-managed", "http-server"]
        authenticated_provider.compute_client.list.return_value = [mock_instance]

        # Mock GKE clusters
        mock_cluster = Mock()
        mock_cluster.name = "clustrix-gke"
        mock_cluster.status.name = "RUNNING"
        mock_cluster.endpoint = "1.2.3.4"
        mock_cluster.current_master_version = "1.25.0"
        mock_cluster.current_node_count = 3
        mock_cluster.location = "us-central1-a"
        mock_cluster.zone = "us-central1-a"
        mock_cluster.resource_labels = {"created_by": "clustrix"}

        mock_response = Mock()
        mock_response.clusters = [mock_cluster]
        authenticated_provider.container_client.list_clusters.return_value = (
            mock_response
        )

        result = authenticated_provider.list_clusters()

        assert len(result) == 2

        # Check compute instance
        compute_result = next(r for r in result if r["type"] == "compute")
        assert compute_result["name"] == "clustrix-instance"
        assert compute_result["instance_id"] == "clustrix-instance"
        assert compute_result["status"] == "running"
        assert compute_result["machine_type"] == "e2-medium"

        # Check GKE cluster
        gke_result = next(r for r in result if r["type"] == "gke")
        assert gke_result["name"] == "clustrix-gke"
        assert gke_result["cluster_id"] == "clustrix-gke"
        assert gke_result["status"] == "running"
        assert gke_result["endpoint"] == "1.2.3.4"

    def test_list_clusters_not_authenticated(self, provider):
        """Test cluster listing when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.list_clusters()

    def test_list_clusters_no_clustrix_instances(self, authenticated_provider):
        """Test cluster listing with no Clustrix-managed instances."""
        # Mock instance without clustrix-managed tag
        mock_instance = Mock()
        mock_instance.name = "other-instance"
        mock_instance.tags = Mock()
        mock_instance.tags.items = ["http-server"]  # No clustrix-managed tag
        authenticated_provider.compute_client.list.return_value = [mock_instance]

        # Mock GKE cluster without clustrix label
        mock_cluster = Mock()
        mock_cluster.name = "other-gke"
        mock_cluster.resource_labels = {"created_by": "other"}  # Not clustrix

        mock_response = Mock()
        mock_response.clusters = [mock_cluster]
        authenticated_provider.container_client.list_clusters.return_value = (
            mock_response
        )

        result = authenticated_provider.list_clusters()

        assert len(result) == 0

    def test_list_clusters_exceptions(self, authenticated_provider):
        """Test cluster listing with exceptions."""
        # Mock compute client exception
        authenticated_provider.compute_client.list.side_effect = Exception(
            "Compute API error"
        )

        # Mock container client exception
        authenticated_provider.container_client.list_clusters.side_effect = Exception(
            "GKE API error"
        )

        result = authenticated_provider.list_clusters()

        assert result == []

    def test_get_cluster_config_compute_success(self, authenticated_provider):
        """Test successful compute cluster config retrieval."""
        mock_instance = Mock()
        mock_access_config = Mock()
        mock_access_config.nat_i_p = "35.1.2.3"
        mock_interface = Mock()
        mock_interface.access_configs = [mock_access_config]
        mock_instance.network_interfaces = [mock_interface]
        authenticated_provider.compute_client.get.return_value = mock_instance

        result = authenticated_provider.get_cluster_config(
            "test-instance", cluster_type="compute"
        )

        assert result["name"] == "GCP Compute - test-instance"
        assert result["cluster_type"] == "ssh"
        assert result["cluster_host"] == "35.1.2.3"
        assert result["username"] == "ubuntu"
        assert result["cluster_port"] == 22
        assert result["default_cores"] == 2
        assert result["default_memory"] == "4GB"
        assert result["remote_work_dir"] == "/home/ubuntu/clustrix"
        assert result["package_manager"] == "conda"
        assert result["cost_monitoring"] is True
        assert result["provider"] == "gcp"
        assert result["provider_config"]["instance_name"] == "test-instance"
        assert result["provider_config"]["zone"] == "us-central1-a"
        assert result["provider_config"]["project_id"] == "test-project"

    def test_get_cluster_config_compute_no_ip(self, authenticated_provider):
        """Test compute cluster config with no external IP."""
        mock_instance = Mock()
        mock_access_config = Mock()
        mock_access_config.nat_i_p = None  # No external IP
        mock_interface = Mock()
        mock_interface.access_configs = [mock_access_config]
        mock_instance.network_interfaces = [mock_interface]
        authenticated_provider.compute_client.get.return_value = mock_instance

        result = authenticated_provider.get_cluster_config(
            "test-instance", cluster_type="compute"
        )

        assert result["cluster_host"] == ""

    def test_get_cluster_config_compute_exception(self, authenticated_provider):
        """Test compute cluster config with exception."""
        authenticated_provider.compute_client.get.side_effect = Exception("API error")

        result = authenticated_provider.get_cluster_config(
            "test-instance", cluster_type="compute"
        )

        # Should return basic config
        assert result["name"] == "GCP Compute - test-instance"
        assert result["cluster_type"] == "ssh"
        assert result["cluster_host"] == "placeholder.gcp.com"
        assert result["provider"] == "gcp"

    def test_get_cluster_config_gke(self, authenticated_provider):
        """Test GKE cluster config retrieval."""
        result = authenticated_provider.get_cluster_config(
            "test-cluster", cluster_type="gke"
        )

        assert result["name"] == "GCP GKE - test-cluster"
        assert result["cluster_type"] == "kubernetes"
        assert result["cluster_host"] == "test-cluster.gke.us-central1.gcp.com"
        assert result["cluster_port"] == 443
        assert result["k8s_namespace"] == "default"
        assert result["k8s_image"] == "python:3.11"
        assert result["default_cores"] == 2
        assert result["default_memory"] == "4GB"
        assert result["cost_monitoring"] is True
        assert result["provider"] == "gcp"
        assert result["provider_config"]["cluster_name"] == "test-cluster"
        assert result["provider_config"]["region"] == "us-central1"
        assert result["provider_config"]["project_id"] == "test-project"

    def test_get_cluster_config_unknown_type(self, authenticated_provider):
        """Test cluster config with unknown type."""
        with pytest.raises(ValueError, match="Unknown cluster type"):
            authenticated_provider.get_cluster_config(
                "test-cluster", cluster_type="unknown"
            )

    def test_estimate_cost_compute(self, provider):
        """Test cost estimation for compute instances."""
        result = provider.estimate_cost(
            cluster_type="compute", machine_type="e2-medium", hours=10
        )

        assert "instance" in result
        assert "total" in result
        assert result["instance"] == 0.0225 * 10
        assert result["total"] == 0.0225 * 10

    def test_estimate_cost_gke(self, provider):
        """Test cost estimation for GKE clusters."""
        result = provider.estimate_cost(
            cluster_type="gke", machine_type="e2-standard-2", hours=5
        )

        cluster_fee = 0.10 * 5
        node_cost = 0.0450 * 5
        total = cluster_fee + node_cost

        assert "cluster_management" in result
        assert "nodes" in result
        assert "total" in result
        assert result["cluster_management"] == cluster_fee
        assert result["nodes"] == node_cost
        assert result["total"] == total

    def test_estimate_cost_unknown_machine_type(self, provider):
        """Test cost estimation with unknown machine type."""
        result = provider.estimate_cost(machine_type="unknown-type", hours=2)

        assert result["instance"] == 0.05 * 2  # Default price
        assert result["total"] == 0.05 * 2

    def test_estimate_cost_defaults(self, provider):
        """Test cost estimation with default values."""
        result = provider.estimate_cost()

        assert result["instance"] == 0.0225  # e2-medium for 1 hour
        assert result["total"] == 0.0225

    def test_get_available_instance_types_not_authenticated(self, provider):
        """Test instance types when not authenticated."""
        result = provider.get_available_instance_types()

        # Should return default list
        assert "e2-micro" in result
        assert "e2-medium" in result
        assert "n1-standard-1" in result
        assert "c2-standard-4" in result

    @patch("clustrix.cloud_providers.gcp.compute_v1")
    @patch("clustrix.cloud_providers.gcp.service_account")
    def test_get_available_instance_types_success(
        self, mock_service_account, mock_compute, authenticated_provider
    ):
        """Test successful instance types retrieval."""
        mock_machine_type1 = Mock()
        mock_machine_type1.name = "e2-micro"
        mock_machine_type2 = Mock()
        mock_machine_type2.name = "e2-medium"
        mock_machine_type3 = Mock()
        mock_machine_type3.name = "n1-standard-2"
        mock_machine_type4 = Mock()
        mock_machine_type4.name = "c2-standard-4"

        mock_machine_types_client = Mock()
        mock_machine_types_client.list.return_value = [
            mock_machine_type1,
            mock_machine_type2,
            mock_machine_type3,
            mock_machine_type4,
        ]
        mock_compute.MachineTypesClient.return_value = mock_machine_types_client

        result = authenticated_provider.get_available_instance_types()

        # Should contain the mocked machine types
        assert "e2-micro" in result
        assert "e2-medium" in result
        assert "n1-standard-2" in result
        assert "c2-standard-4" in result

    @patch("clustrix.cloud_providers.gcp.compute_v1")
    @patch("clustrix.cloud_providers.gcp.service_account")
    def test_get_available_instance_types_custom_region(
        self, mock_service_account, mock_compute, authenticated_provider
    ):
        """Test instance types retrieval for custom region."""
        mock_machine_types_client = Mock()
        mock_machine_types_client.list.return_value = []
        mock_compute.MachineTypesClient.return_value = mock_machine_types_client

        result = authenticated_provider.get_available_instance_types(
            region="europe-west1"
        )

        # Should query the correct zone
        mock_machine_types_client.list.assert_called_once_with(
            project="test-project", zone="europe-west1-a"
        )

    @patch("clustrix.cloud_providers.gcp.compute_v1")
    @patch("clustrix.cloud_providers.gcp.service_account")
    def test_get_available_instance_types_exception(
        self, mock_service_account, mock_compute, authenticated_provider
    ):
        """Test instance types retrieval with exception."""
        mock_machine_types_client = Mock()
        mock_machine_types_client.list.side_effect = Exception("API error")
        mock_compute.MachineTypesClient.return_value = mock_machine_types_client

        result = authenticated_provider.get_available_instance_types()

        # Should return default list
        assert "e2-micro" in result
        assert "e2-medium" in result

    def test_get_available_regions_not_authenticated(self, provider):
        """Test regions when not authenticated."""
        result = provider.get_available_regions()

        assert "us-central1" in result
        assert "us-east1" in result
        assert "europe-west1" in result
        assert "asia-southeast1" in result

    @patch("clustrix.cloud_providers.gcp.compute_v1")
    @patch("clustrix.cloud_providers.gcp.service_account")
    def test_get_available_regions_success(
        self, mock_service_account, mock_compute, authenticated_provider
    ):
        """Test successful regions retrieval."""
        mock_region1 = Mock()
        mock_region1.name = "us-central1"
        mock_region2 = Mock()
        mock_region2.name = "europe-west1"
        mock_region3 = Mock()
        mock_region3.name = "asia-southeast1"
        mock_region4 = Mock()
        mock_region4.name = "us-west3"  # Not in priority list

        mock_regions_client = Mock()
        mock_regions_client.list.return_value = [
            mock_region1,
            mock_region2,
            mock_region3,
            mock_region4,
        ]
        mock_compute.RegionsClient.return_value = mock_regions_client

        result = authenticated_provider.get_available_regions()

        # Priority regions should come first
        assert result[0] == "us-central1"
        assert result[1] == "europe-west1"
        assert result[2] == "asia-southeast1"
        assert "us-west3" in result

    @patch("clustrix.cloud_providers.gcp.compute_v1")
    @patch("clustrix.cloud_providers.gcp.service_account")
    def test_get_available_regions_exception(
        self, mock_service_account, mock_compute, authenticated_provider
    ):
        """Test regions retrieval with exception."""
        mock_regions_client = Mock()
        mock_regions_client.list.side_effect = Exception("API error")
        mock_compute.RegionsClient.return_value = mock_regions_client

        result = authenticated_provider.get_available_regions()

        # Should return default list
        assert "us-central1" in result
        assert "us-east1" in result


class TestGCPProviderEdgeCases:
    """Test edge cases and error handling."""

    def test_machine_type_sorting_edge_cases(self):
        """Test machine type name parsing with edge cases."""
        # This tests the sorting key function indirectly
        provider = GCPProvider()
        provider.authenticated = True
        provider.project_id = "test-project"
        provider.service_account_info = {"test": "info"}

        with patch("clustrix.cloud_providers.gcp.compute_v1") as mock_compute, patch(
            "clustrix.cloud_providers.gcp.service_account"
        ):
            # Mock machine types with various formats
            mock_types = []
            for name in [
                "e2-micro",
                "e2-medium",
                "e2-standard-2",
                "e2-standard-10",
                "invalid-name",
            ]:
                mock_type = Mock()
                mock_type.name = name
                mock_types.append(mock_type)

            mock_machine_types_client = Mock()
            mock_machine_types_client.list.return_value = mock_types
            mock_compute.MachineTypesClient.return_value = mock_machine_types_client

            result = provider.get_available_instance_types()

            # Should handle various formats without error
            assert len(result) > 0

    def test_list_clusters_missing_attributes(self):
        """Test list_clusters with instances missing attributes."""
        provider = GCPProvider()
        provider.authenticated = True
        provider.project_id = "test-project"
        provider.zone = "us-central1-a"
        provider.compute_client = Mock()
        provider.container_client = Mock()

        # Mock instance without tags attribute
        mock_instance = Mock()
        mock_instance.name = "test-instance"
        mock_instance.status = "RUNNING"
        mock_instance.machine_type = None  # Missing machine type
        del mock_instance.tags  # Remove tags attribute
        provider.compute_client.list.return_value = [mock_instance]

        # Mock empty GKE response
        mock_response = Mock()
        mock_response.clusters = []
        provider.container_client.list_clusters.return_value = mock_response

        result = provider.list_clusters()

        # Should handle missing attributes gracefully
        assert result == []

    def test_list_clusters_no_gke_labels(self):
        """Test list_clusters with GKE cluster without resource_labels."""
        provider = GCPProvider()
        provider.authenticated = True
        provider.project_id = "test-project"
        provider.zone = "us-central1-a"
        provider.compute_client = Mock()
        provider.container_client = Mock()

        # Mock empty compute response
        provider.compute_client.list.return_value = []

        # Mock GKE cluster without resource_labels
        mock_cluster = Mock()
        mock_cluster.name = "test-cluster"
        mock_cluster.resource_labels = None

        mock_response = Mock()
        mock_response.clusters = [mock_cluster]
        provider.container_client.list_clusters.return_value = mock_response

        result = provider.list_clusters()

        # Should handle missing labels gracefully
        assert result == []

    def test_get_cluster_config_no_access_configs(self):
        """Test cluster config with instance having no access configs."""
        provider = GCPProvider()
        provider.authenticated = True
        provider.project_id = "test-project"
        provider.zone = "us-central1-a"
        provider.compute_client = Mock()

        mock_instance = Mock()
        mock_interface = Mock()
        mock_interface.access_configs = []  # No access configs
        mock_instance.network_interfaces = [mock_interface]
        provider.compute_client.get.return_value = mock_instance

        result = provider.get_cluster_config("test-instance", cluster_type="compute")

        assert result["cluster_host"] == ""

    def test_get_cluster_config_no_network_interfaces(self):
        """Test cluster config with instance having no network interfaces."""
        provider = GCPProvider()
        provider.authenticated = True
        provider.project_id = "test-project"
        provider.zone = "us-central1-a"
        provider.compute_client = Mock()

        mock_instance = Mock()
        mock_instance.network_interfaces = []  # No network interfaces
        provider.compute_client.get.return_value = mock_instance

        result = provider.get_cluster_config("test-instance", cluster_type="compute")

        assert result["cluster_host"] == ""

    def test_gke_cluster_status_no_zone(self):
        """Test GKE cluster status when zone is None."""
        provider = GCPProvider()
        provider.authenticated = True
        provider.project_id = "test-project"
        provider.zone = "us-central1-a"
        provider.container_client = Mock()

        mock_cluster = Mock()
        mock_cluster.status.name = "RUNNING"
        mock_cluster.endpoint = "1.2.3.4"
        mock_cluster.current_master_version = "1.25.0"
        mock_cluster.current_node_version = "1.25.0"
        mock_cluster.current_node_count = 3
        mock_cluster.location = "us-central1"
        mock_cluster.zone = None  # No zone (regional cluster)
        mock_cluster.create_time = "2024-01-01T00:00:00Z"
        provider.container_client.get_cluster.return_value = mock_cluster

        result = provider.get_cluster_status("test-cluster", cluster_type="gke")

        assert result["zone"] is None
