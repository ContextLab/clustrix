import pytest
import requests
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from clustrix.cloud_providers.lambda_cloud import LambdaCloudProvider


class TestLambdaCloudProvider:
    """Test Lambda Cloud provider functionality."""

    @pytest.fixture
    def provider(self):
        """Create LambdaCloudProvider instance."""
        return LambdaCloudProvider()

    @pytest.fixture
    def authenticated_provider(self):
        """Create authenticated LambdaCloudProvider instance."""
        provider = LambdaCloudProvider()
        provider.authenticated = True
        provider.api_key = "test_api_key"
        provider.session = Mock()
        provider.credentials = {"api_key": "test_api_key"}
        return provider

    def test_initialization(self, provider):
        """Test provider initialization."""
        assert provider.api_key is None
        assert provider.base_url == "https://cloud.lambdalabs.com/api/v1"
        assert provider.session is None
        assert not provider.authenticated

    @patch("requests.Session")
    def test_authenticate_success(self, mock_session_class, provider):
        """Test successful authentication."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        mock_session.get.return_value = mock_response

        result = provider.authenticate(api_key="test_api_key")

        assert result is True
        assert provider.authenticated is True
        assert provider.api_key == "test_api_key"
        assert provider.session == mock_session

        # Verify session headers were set
        mock_session.headers.update.assert_called_once_with(
            {
                "Authorization": "Bearer test_api_key",
                "Content-Type": "application/json",
            }
        )

        # Verify API call was made
        mock_session.get.assert_called_once_with(
            "https://cloud.lambdalabs.com/api/v1/instance-types"
        )

    @patch("requests.Session")
    def test_authenticate_invalid_api_key(self, mock_session_class, provider):
        """Test authentication with invalid API key."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # Mock 401 response
        mock_response = Mock()
        mock_response.status_code = 401
        mock_session.get.return_value = mock_response

        result = provider.authenticate(api_key="invalid_key")

        assert result is False
        assert not provider.authenticated

    @patch("requests.Session")
    def test_authenticate_missing_api_key(self, mock_session_class, provider):
        """Test authentication without API key."""
        result = provider.authenticate()

        assert result is False
        assert not provider.authenticated

    @patch("requests.Session")
    def test_authenticate_api_error(self, mock_session_class, provider):
        """Test authentication with API error."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # Mock 500 response
        mock_response = Mock()
        mock_response.status_code = 500
        mock_session.get.return_value = mock_response

        result = provider.authenticate(api_key="test_api_key")

        assert result is False
        assert not provider.authenticated

    @patch("requests.Session")
    def test_authenticate_connection_error(self, mock_session_class, provider):
        """Test authentication with connection error."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = requests.RequestException("Connection failed")

        result = provider.authenticate(api_key="test_api_key")

        assert result is False
        assert not provider.authenticated

    @patch("requests.Session")
    def test_authenticate_unexpected_error(self, mock_session_class, provider):
        """Test authentication with unexpected error."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = Exception("Unexpected error")

        result = provider.authenticate(api_key="test_api_key")

        assert result is False
        assert not provider.authenticated

    def test_validate_credentials_success(self, authenticated_provider):
        """Test successful credential validation."""
        mock_response = Mock()
        mock_response.status_code = 200
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.validate_credentials()

        assert result is True

    def test_validate_credentials_failure(self, authenticated_provider):
        """Test failed credential validation."""
        mock_response = Mock()
        mock_response.status_code = 401
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.validate_credentials()

        assert result is False

    def test_validate_credentials_not_authenticated(self, provider):
        """Test credential validation when not authenticated."""
        result = provider.validate_credentials()

        assert result is False

    def test_validate_credentials_exception(self, authenticated_provider):
        """Test credential validation with exception."""
        authenticated_provider.session.get.side_effect = Exception("Network error")

        result = authenticated_provider.validate_credentials()

        assert result is False

    def test_create_instance_success(self, authenticated_provider):
        """Test successful instance creation."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"instance_ids": ["i-12345"]}
        authenticated_provider.session.post.return_value = mock_response

        with patch("clustrix.cloud_providers.lambda_cloud.datetime") as mock_datetime:
            mock_datetime.now.return_value.isoformat.return_value = (
                "2024-01-01T00:00:00+00:00"
            )
            mock_datetime.timezone = timezone

            result = authenticated_provider.create_instance(
                instance_name="test-instance",
                instance_type="gpu_1x_a10",
                region="us-east-1",
                ssh_key_name="my-key",
            )

        expected_data = {
            "region_name": "us-east-1",
            "instance_type_name": "gpu_1x_a10",
            "ssh_key_names": ["my-key"],
            "file_system_names": [],
            "quantity": 1,
            "name": "test-instance",
        }

        authenticated_provider.session.post.assert_called_once_with(
            "https://cloud.lambdalabs.com/api/v1/instance-operations/launch",
            json=expected_data,
        )

        assert result["instance_name"] == "test-instance"
        assert result["instance_id"] == "i-12345"
        assert result["instance_type"] == "gpu_1x_a10"
        assert result["region"] == "us-east-1"
        assert result["status"] == "booting"

    def test_create_instance_no_ssh_key(self, authenticated_provider):
        """Test instance creation without SSH key."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"instance_ids": ["i-12345"]}
        authenticated_provider.session.post.return_value = mock_response

        authenticated_provider.create_instance(
            instance_name="test-instance",
            instance_type="gpu_1x_a10",
            region="us-east-1",
        )

        call_args = authenticated_provider.session.post.call_args[1]["json"]
        assert call_args["ssh_key_names"] == []

    def test_create_instance_not_authenticated(self, provider):
        """Test instance creation when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.create_instance("test-instance")

    def test_create_instance_no_instance_ids(self, authenticated_provider):
        """Test instance creation with no instance IDs returned."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"instance_ids": []}
        authenticated_provider.session.post.return_value = mock_response

        with pytest.raises(RuntimeError, match="No instance ID returned"):
            authenticated_provider.create_instance("test-instance")

    def test_create_instance_api_error(self, authenticated_provider):
        """Test instance creation with API error."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"error": "Invalid instance type"}
        authenticated_provider.session.post.return_value = mock_response

        with pytest.raises(
            RuntimeError, match="Failed to create instance.*Invalid instance type"
        ):
            authenticated_provider.create_instance("test-instance")

    def test_create_instance_api_error_no_json(self, authenticated_provider):
        """Test instance creation with non-JSON API error."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.headers = {"content-type": "text/plain"}
        authenticated_provider.session.post.return_value = mock_response

        with pytest.raises(RuntimeError, match="Failed to create instance: 400"):
            authenticated_provider.create_instance("test-instance")

    def test_create_instance_request_exception(self, authenticated_provider):
        """Test instance creation with request exception."""
        authenticated_provider.session.post.side_effect = requests.RequestException(
            "Network error"
        )

        with pytest.raises(requests.RequestException):
            authenticated_provider.create_instance("test-instance")

    def test_create_instance_unexpected_exception(self, authenticated_provider):
        """Test instance creation with unexpected exception."""
        authenticated_provider.session.post.side_effect = Exception("Unexpected error")

        with pytest.raises(Exception, match="Unexpected error"):
            authenticated_provider.create_instance("test-instance")

    def test_create_cluster(self, authenticated_provider):
        """Test create_cluster method."""
        with patch.object(authenticated_provider, "create_instance") as mock_create:
            mock_create.return_value = {"instance_id": "i-12345"}

            result = authenticated_provider.create_cluster(
                "test-cluster", instance_type="gpu_1x_a10", region="us-east-1"
            )

            mock_create.assert_called_once_with(
                "test-cluster", instance_type="gpu_1x_a10", region="us-east-1"
            )
            assert result == {"instance_id": "i-12345"}

    def test_delete_cluster_success(self, authenticated_provider):
        """Test successful cluster deletion."""
        mock_response = Mock()
        mock_response.status_code = 200
        authenticated_provider.session.post.return_value = mock_response

        result = authenticated_provider.delete_cluster("i-12345")

        assert result is True
        authenticated_provider.session.post.assert_called_once_with(
            "https://cloud.lambdalabs.com/api/v1/instance-operations/terminate",
            json={"instance_ids": ["i-12345"]},
        )

    def test_delete_cluster_not_authenticated(self, provider):
        """Test cluster deletion when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.delete_cluster("i-12345")

    def test_delete_cluster_api_error(self, authenticated_provider):
        """Test cluster deletion with API error."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"error": "Instance not found"}
        authenticated_provider.session.post.return_value = mock_response

        result = authenticated_provider.delete_cluster("i-12345")

        assert result is False

    def test_delete_cluster_request_exception(self, authenticated_provider):
        """Test cluster deletion with request exception."""
        authenticated_provider.session.post.side_effect = requests.RequestException(
            "Network error"
        )

        result = authenticated_provider.delete_cluster("i-12345")

        assert result is False

    def test_delete_cluster_unexpected_exception(self, authenticated_provider):
        """Test cluster deletion with unexpected exception."""
        authenticated_provider.session.post.side_effect = Exception("Unexpected error")

        result = authenticated_provider.delete_cluster("i-12345")

        assert result is False

    def test_get_cluster_status_success(self, authenticated_provider):
        """Test successful cluster status retrieval."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "running",
            "instance_type": {"name": "gpu_1x_a10"},
            "region": {"name": "us-east-1"},
        }
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.get_cluster_status("i-12345")

        assert result["instance_id"] == "i-12345"
        assert result["status"] == "running"
        assert result["instance_type"] == "gpu_1x_a10"
        assert result["region"] == "us-east-1"
        assert result["provider"] == "lambda"
        assert result["cluster_type"] == "ssh"

    def test_get_cluster_status_not_found(self, authenticated_provider):
        """Test cluster status for non-existent instance."""
        mock_response = Mock()
        mock_response.status_code = 404
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.get_cluster_status("i-12345")

        assert result["instance_id"] == "i-12345"
        assert result["status"] == "not_found"
        assert result["provider"] == "lambda"

    def test_get_cluster_status_not_authenticated(self, provider):
        """Test cluster status when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.get_cluster_status("i-12345")

    def test_get_cluster_status_api_error(self, authenticated_provider):
        """Test cluster status with API error."""
        mock_response = Mock()
        mock_response.status_code = 500
        authenticated_provider.session.get.return_value = mock_response

        with pytest.raises(RuntimeError, match="Failed to get instance status"):
            authenticated_provider.get_cluster_status("i-12345")

    def test_get_cluster_status_request_exception(self, authenticated_provider):
        """Test cluster status with request exception."""
        authenticated_provider.session.get.side_effect = requests.RequestException(
            "Network error"
        )

        with pytest.raises(requests.RequestException):
            authenticated_provider.get_cluster_status("i-12345")

    def test_get_cluster_status_unexpected_exception(self, authenticated_provider):
        """Test cluster status with unexpected exception."""
        authenticated_provider.session.get.side_effect = Exception("Unexpected error")

        with pytest.raises(Exception, match="Unexpected error"):
            authenticated_provider.get_cluster_status("i-12345")

    def test_list_clusters_success(self, authenticated_provider):
        """Test successful cluster listing."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "name": "instance-1",
                    "id": "i-12345",
                    "status": "running",
                    "instance_type": {"name": "gpu_1x_a10"},
                    "region": {"name": "us-east-1"},
                },
                {
                    "id": "i-67890",  # No name field
                    "status": "stopped",
                    "instance_type": {"name": "gpu_1x_a100"},
                    "region": {"name": "us-west-2"},
                },
            ]
        }
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.list_clusters()

        assert len(result) == 2

        # First instance
        assert result[0]["name"] == "instance-1"
        assert result[0]["instance_id"] == "i-12345"
        assert result[0]["type"] == "gpu"
        assert result[0]["status"] == "running"
        assert result[0]["instance_type"] == "gpu_1x_a10"
        assert result[0]["region"] == "us-east-1"

        # Second instance (no name)
        assert result[1]["name"] == "i-67890"
        assert result[1]["instance_id"] == "i-67890"
        assert result[1]["status"] == "stopped"

    def test_list_clusters_not_authenticated(self, provider):
        """Test cluster listing when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.list_clusters()

    def test_list_clusters_api_error(self, authenticated_provider):
        """Test cluster listing with API error."""
        mock_response = Mock()
        mock_response.status_code = 500
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.list_clusters()

        assert result == []

    def test_list_clusters_request_exception(self, authenticated_provider):
        """Test cluster listing with request exception."""
        authenticated_provider.session.get.side_effect = requests.RequestException(
            "Network error"
        )

        result = authenticated_provider.list_clusters()

        assert result == []

    def test_list_clusters_unexpected_exception(self, authenticated_provider):
        """Test cluster listing with unexpected exception."""
        authenticated_provider.session.get.side_effect = Exception("Unexpected error")

        result = authenticated_provider.list_clusters()

        assert result == []

    def test_get_cluster_config_success(self, authenticated_provider):
        """Test successful cluster config retrieval."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "ip": "1.2.3.4",
            "instance_type": {"name": "gpu_1x_a10"},
            "region": {"name": "us-east-1"},
        }
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.get_cluster_config("i-12345")

        assert result["name"] == "Lambda Cloud - i-12345"
        assert result["cluster_type"] == "ssh"
        assert result["cluster_host"] == "1.2.3.4"
        assert result["username"] == "ubuntu"
        assert result["cluster_port"] == 22
        assert result["default_cores"] == 8
        assert result["default_memory"] == "32GB"
        assert result["remote_work_dir"] == "/home/ubuntu/clustrix"
        assert result["package_manager"] == "conda"
        assert result["cost_monitoring"] is True
        assert result["provider"] == "lambda"
        assert result["provider_config"]["instance_id"] == "i-12345"
        assert result["provider_config"]["instance_type"] == "gpu_1x_a10"
        assert result["provider_config"]["region"] == "us-east-1"

    def test_get_cluster_config_not_authenticated(self, provider):
        """Test cluster config when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.get_cluster_config("i-12345")

    def test_get_cluster_config_api_error(self, authenticated_provider):
        """Test cluster config with API error."""
        mock_response = Mock()
        mock_response.status_code = 404
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.get_cluster_config("i-12345")

        # Should return basic config
        assert result["name"] == "Lambda Cloud - i-12345"
        assert result["cluster_type"] == "ssh"
        assert result["cluster_host"] == "placeholder.lambdalabs.com"
        assert result["username"] == "ubuntu"
        assert result["provider"] == "lambda"

    def test_get_cluster_config_exception(self, authenticated_provider):
        """Test cluster config with exception."""
        authenticated_provider.session.get.side_effect = Exception("Network error")

        result = authenticated_provider.get_cluster_config("i-12345")

        # Should return basic config
        assert result["name"] == "Lambda Cloud - i-12345"
        assert result["cluster_type"] == "ssh"
        assert result["cluster_host"] == "placeholder.lambdalabs.com"
        assert result["username"] == "ubuntu"
        assert result["provider"] == "lambda"

    def test_estimate_cost_default(self, provider):
        """Test cost estimation with default values."""
        result = provider.estimate_cost()

        assert "gpu_instance" in result
        assert "total" in result
        assert result["gpu_instance"] == 0.75  # gpu_1x_a10 default price
        assert result["total"] == 0.75

    def test_estimate_cost_custom(self, provider):
        """Test cost estimation with custom values."""
        result = provider.estimate_cost(instance_type="gpu_1x_h100", hours=5)

        assert result["gpu_instance"] == 1.99 * 5
        assert result["total"] == 1.99 * 5

    def test_estimate_cost_unknown_instance(self, provider):
        """Test cost estimation with unknown instance type."""
        result = provider.estimate_cost(instance_type="unknown_type", hours=2)

        assert result["gpu_instance"] == 1.0 * 2  # Default price
        assert result["total"] == 1.0 * 2

    def test_get_available_instance_types_not_authenticated(self, provider):
        """Test instance types when not authenticated."""
        result = provider.get_available_instance_types()

        # Should return default list
        assert "gpu_1x_a10" in result
        assert "gpu_1x_h100" in result
        assert "gpu_8x_a100" in result

    def test_get_available_instance_types_success(self, authenticated_provider):
        """Test successful instance types retrieval."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"name": "gpu_1x_a10"},
                {"name": "gpu_2x_a10"},
                {"name": "gpu_4x_a10"},
                {"name": "gpu_8x_a100"},
            ]
        }
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.get_available_instance_types()

        # Should be sorted by GPU count
        assert result == ["gpu_1x_a10", "gpu_2x_a10", "gpu_4x_a10", "gpu_8x_a100"]

    def test_get_available_instance_types_api_error(self, authenticated_provider):
        """Test instance types with API error."""
        mock_response = Mock()
        mock_response.status_code = 500
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.get_available_instance_types()

        # Should return default list
        assert "gpu_1x_a10" in result
        assert "gpu_1x_h100" in result

    def test_get_available_instance_types_exception(self, authenticated_provider):
        """Test instance types with exception."""
        authenticated_provider.session.get.side_effect = Exception("Network error")

        result = authenticated_provider.get_available_instance_types()

        # Should return default list
        assert "gpu_1x_a10" in result
        assert "gpu_1x_h100" in result

    def test_get_available_regions_not_authenticated(self, provider):
        """Test regions when not authenticated."""
        result = provider.get_available_regions()

        assert result == ["us-east-1", "us-west-1", "us-west-2"]

    def test_get_available_regions_success(self, authenticated_provider):
        """Test successful regions retrieval."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "regions_with_capacity_available": [
                        {"name": "us-east-1"},
                        {"name": "us-west-2"},
                        "eu-central-1",  # String format
                    ]
                },
                {
                    "regions_with_capacity_available": [
                        {"name": "us-west-1"},
                        {"name": "us-east-1"},  # Duplicate
                    ]
                },
            ]
        }
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.get_available_regions()

        # Should be sorted and deduplicated
        assert "us-east-1" in result
        assert "us-west-1" in result
        assert "us-west-2" in result
        assert "eu-central-1" in result
        assert len(set(result)) == len(result)  # No duplicates

    def test_get_available_regions_no_regions(self, authenticated_provider):
        """Test regions when no regions returned."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.get_available_regions()

        # Should return fallback
        assert result == ["us-east-1", "us-west-1", "us-west-2"]

    def test_get_available_regions_api_error(self, authenticated_provider):
        """Test regions with API error."""
        mock_response = Mock()
        mock_response.status_code = 500
        authenticated_provider.session.get.return_value = mock_response

        result = authenticated_provider.get_available_regions()

        assert result == ["us-east-1", "us-west-1", "us-west-2"]

    def test_get_available_regions_exception(self, authenticated_provider):
        """Test regions with exception."""
        authenticated_provider.session.get.side_effect = Exception("Network error")

        result = authenticated_provider.get_available_regions()

        assert result == ["us-east-1", "us-west-1", "us-west-2"]


class TestLambdaCloudProviderEdgeCases:
    """Test edge cases and error handling."""

    def test_instance_type_sorting_edge_cases(self):
        """Test instance type sorting with edge cases."""
        provider = LambdaCloudProvider()
        provider.authenticated = True
        provider.session = Mock()

        # Mock response with edge case instance names
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"name": "gpu_8x_a100"},
                {"name": "gpu_1x_a10"},
                {"name": "invalid_name"},  # No x in name
                {"name": "gpu_ax_v100"},  # Invalid GPU count
                {"name": "gpu_2x_a6000"},
            ]
        }
        provider.session.get.return_value = mock_response

        result = provider.get_available_instance_types()

        # Valid instances should be sorted first, invalid ones last
        assert result[0] == "gpu_1x_a10"  # 1 GPU
        assert result[1] == "gpu_2x_a6000"  # 2 GPUs
        assert result[2] == "gpu_8x_a100"  # 8 GPUs
        # Invalid ones at the end (sorted by string comparison)
        assert "invalid_name" in result
        assert "gpu_ax_v100" in result

    def test_instance_type_sorting_no_parts(self):
        """Test instance type sorting with malformed names."""
        provider = LambdaCloudProvider()
        provider.authenticated = True
        provider.session = Mock()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"name": "short"},
                {"name": "gpu_1x_a10"},
            ]
        }
        provider.session.get.return_value = mock_response

        result = provider.get_available_instance_types()

        assert "gpu_1x_a10" in result
        assert "short" in result

    def test_get_cluster_config_no_ip(self):
        """Test cluster config when no IP is returned."""
        provider = LambdaCloudProvider()
        provider.authenticated = True
        provider.session = Mock()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            # No ip field
            "instance_type": {"name": "gpu_1x_a10"},
            "region": {"name": "us-east-1"},
        }
        provider.session.get.return_value = mock_response

        result = provider.get_cluster_config("i-12345")

        assert result["cluster_host"] == ""  # Empty string when no IP

    def test_get_cluster_status_missing_fields(self):
        """Test cluster status with missing fields."""
        provider = LambdaCloudProvider()
        provider.authenticated = True
        provider.session = Mock()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            # Missing status, instance_type, region fields
        }
        provider.session.get.return_value = mock_response

        result = provider.get_cluster_status("i-12345")

        assert result["status"] == "unknown"
        assert result["instance_type"] == "unknown"
        assert result["region"] == "unknown"

    def test_list_clusters_missing_fields(self):
        """Test cluster listing with missing fields."""
        provider = LambdaCloudProvider()
        provider.authenticated = True
        provider.session = Mock()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    # Missing most fields, only has id
                    "id": "i-12345"
                }
            ]
        }
        provider.session.get.return_value = mock_response

        result = provider.list_clusters()

        assert len(result) == 1
        assert result[0]["name"] == "i-12345"
        assert result[0]["instance_id"] == "i-12345"
        assert result[0]["type"] == "gpu"
        assert result[0]["status"] == "unknown"
        assert result[0]["instance_type"] == "unknown"
        assert result[0]["region"] == "unknown"

    def test_regions_empty_data_structure(self):
        """Test regions parsing with empty data structures."""
        provider = LambdaCloudProvider()
        provider.authenticated = True
        provider.session = Mock()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"regions_with_capacity_available": []},  # Empty regions
                {
                    # No regions_with_capacity_available field
                },
            ]
        }
        provider.session.get.return_value = mock_response

        result = provider.get_available_regions()

        # Should return fallback
        assert result == ["us-east-1", "us-west-1", "us-west-2"]
