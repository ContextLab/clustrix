import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from clustrix.cloud_providers.huggingface_spaces import HuggingFaceSpacesProvider


class TestHuggingFaceSpacesProvider:
    """Test HuggingFace Spaces provider functionality."""

    @pytest.fixture
    def provider(self):
        """Create HuggingFaceSpacesProvider instance."""
        return HuggingFaceSpacesProvider()

    @pytest.fixture
    def authenticated_provider(self):
        """Create authenticated HuggingFaceSpacesProvider instance."""
        provider = HuggingFaceSpacesProvider()
        provider.authenticated = True
        provider.api_token = "test_token"
        provider.username = "test_user"
        provider.api = Mock()
        provider.credentials = {"token": "test_token", "username": "test_user"}
        return provider

    def test_initialization(self, provider):
        """Test provider initialization."""
        assert provider.api_token is None
        assert provider.username is None
        assert provider.api is None
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.huggingface_spaces.HF_AVAILABLE", True)
    @patch("clustrix.cloud_providers.huggingface_spaces.HfApi")
    def test_authenticate_success(self, mock_hf_api_class, provider):
        """Test successful authentication."""
        mock_api = Mock()
        mock_hf_api_class.return_value = mock_api
        mock_api.whoami.return_value = {"name": "test_user", "type": "user"}

        result = provider.authenticate(token="test_token", username="test_user")

        assert result is True
        assert provider.authenticated is True
        assert provider.api_token == "test_token"
        assert provider.username == "test_user"
        assert provider.api == mock_api

        mock_hf_api_class.assert_called_once_with(token="test_token")
        mock_api.whoami.assert_called_once()

    @patch("clustrix.cloud_providers.huggingface_spaces.HF_AVAILABLE", False)
    def test_authenticate_hf_not_available(self, provider):
        """Test authentication when HuggingFace hub not available."""
        result = provider.authenticate(token="test_token", username="test_user")

        assert result is False
        assert not provider.authenticated

    def test_authenticate_missing_token(self, provider):
        """Test authentication with missing token."""
        result = provider.authenticate(username="test_user")
        assert result is False

    def test_authenticate_missing_username(self, provider):
        """Test authentication with missing username."""
        result = provider.authenticate(token="test_token")
        assert result is False

    @patch("clustrix.cloud_providers.huggingface_spaces.HF_AVAILABLE", True)
    @patch("clustrix.cloud_providers.huggingface_spaces.HfApi")
    def test_authenticate_username_mismatch(self, mock_hf_api_class, provider):
        """Test authentication with username mismatch."""
        mock_api = Mock()
        mock_hf_api_class.return_value = mock_api
        mock_api.whoami.return_value = {"name": "different_user", "type": "user"}

        result = provider.authenticate(token="test_token", username="test_user")

        assert result is False
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.huggingface_spaces.HF_AVAILABLE", True)
    @patch("clustrix.cloud_providers.huggingface_spaces.HfApi")
    def test_authenticate_invalid_whoami(self, mock_hf_api_class, provider):
        """Test authentication with invalid whoami response."""
        mock_api = Mock()
        mock_hf_api_class.return_value = mock_api
        mock_api.whoami.return_value = None

        result = provider.authenticate(token="test_token", username="test_user")

        assert result is False
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.huggingface_spaces.HF_AVAILABLE", True)
    @patch("clustrix.cloud_providers.huggingface_spaces.HfApi")
    def test_authenticate_hf_hub_error(self, mock_hf_api_class, provider):
        """Test authentication with HuggingFace Hub HTTP error."""
        from clustrix.cloud_providers.huggingface_spaces import HfHubHTTPError

        mock_api = Mock()
        mock_hf_api_class.return_value = mock_api
        mock_api.whoami.side_effect = HfHubHTTPError("Invalid token")

        result = provider.authenticate(token="test_token", username="test_user")

        assert result is False
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.huggingface_spaces.HF_AVAILABLE", True)
    @patch("clustrix.cloud_providers.huggingface_spaces.HfApi")
    def test_authenticate_unexpected_error(self, mock_hf_api_class, provider):
        """Test authentication with unexpected error."""
        mock_api = Mock()
        mock_hf_api_class.return_value = mock_api
        mock_api.whoami.side_effect = Exception("Network error")

        result = provider.authenticate(token="test_token", username="test_user")

        assert result is False
        assert not provider.authenticated

    def test_validate_credentials_success(self, authenticated_provider):
        """Test successful credential validation."""
        authenticated_provider.api.whoami.return_value = {"name": "test_user"}

        result = authenticated_provider.validate_credentials()

        assert result is True

    def test_validate_credentials_failure(self, authenticated_provider):
        """Test failed credential validation."""
        authenticated_provider.api.whoami.return_value = None

        result = authenticated_provider.validate_credentials()

        assert result is False

    def test_validate_credentials_not_authenticated(self, provider):
        """Test credential validation when not authenticated."""
        result = provider.validate_credentials()

        assert result is False

    def test_validate_credentials_exception(self, authenticated_provider):
        """Test credential validation with exception."""
        authenticated_provider.api.whoami.side_effect = Exception("API error")

        result = authenticated_provider.validate_credentials()

        assert result is False

    @patch("clustrix.cloud_providers.huggingface_spaces.SpaceHardware")
    def test_create_space_success_basic(
        self, mock_space_hardware, authenticated_provider
    ):
        """Test successful space creation with basic hardware."""
        authenticated_provider.api.create_repo.return_value = (
            "https://huggingface.co/spaces/test_user/test-space"
        )

        with patch(
            "clustrix.cloud_providers.huggingface_spaces.datetime"
        ) as mock_datetime:
            mock_datetime.now.return_value.isoformat.return_value = (
                "2024-01-01T00:00:00+00:00"
            )
            mock_datetime.timezone = timezone

            result = authenticated_provider.create_space(
                space_name="test-space",
                hardware="cpu-basic",
                sdk="gradio",
                private=False,
            )

        assert result["space_name"] == "test-space"
        assert result["space_id"] == "test_user/test-space"
        assert (
            result["space_url"] == "https://huggingface.co/spaces/test_user/test-space"
        )
        assert result["sdk"] == "gradio"
        assert result["hardware"] == "cpu-basic"
        assert result["private"] is False
        assert result["status"] == "creating"

        authenticated_provider.api.create_repo.assert_called_once_with(
            repo_id="test_user/test-space",
            repo_type="space",
            space_sdk="gradio",
            private=False,
        )

    @patch("clustrix.cloud_providers.huggingface_spaces.SpaceHardware")
    def test_create_space_success_gpu(
        self, mock_space_hardware, authenticated_provider
    ):
        """Test successful space creation with GPU hardware."""
        authenticated_provider.api.create_repo.return_value = (
            "https://huggingface.co/spaces/test_user/gpu-space"
        )
        mock_space_hardware.T4_SMALL = "t4-small"

        result = authenticated_provider.create_space(
            space_name="gpu-space", hardware="t4-small", sdk="streamlit", private=True
        )

        assert result["space_name"] == "gpu-space"
        assert result["hardware"] == "t4-small"
        assert result["sdk"] == "streamlit"
        assert result["private"] is True

        # Verify hardware request was made
        authenticated_provider.api.request_space_hardware.assert_called_once_with(
            repo_id="test_user/gpu-space", hardware="t4-small"
        )

    @patch("clustrix.cloud_providers.huggingface_spaces.SpaceHardware")
    def test_create_space_unknown_hardware(
        self, mock_space_hardware, authenticated_provider
    ):
        """Test space creation with unknown hardware type."""
        authenticated_provider.api.create_repo.return_value = (
            "https://huggingface.co/spaces/test_user/test-space"
        )

        result = authenticated_provider.create_space(
            space_name="test-space", hardware="unknown-hardware"
        )

        # Should fallback to cpu-basic
        assert result["hardware"] == "cpu-basic"

        # Hardware request should not be called for unknown hardware
        authenticated_provider.api.request_space_hardware.assert_not_called()

    @patch("clustrix.cloud_providers.huggingface_spaces.SpaceHardware")
    def test_create_space_hardware_request_fails(
        self, mock_space_hardware, authenticated_provider
    ):
        """Test space creation when hardware request fails."""
        authenticated_provider.api.create_repo.return_value = (
            "https://huggingface.co/spaces/test_user/test-space"
        )
        authenticated_provider.api.request_space_hardware.side_effect = Exception(
            "Hardware request failed"
        )
        mock_space_hardware.T4_SMALL = "t4-small"

        result = authenticated_provider.create_space(
            space_name="test-space", hardware="t4-small"
        )

        # Should fallback to cpu-basic when hardware request fails
        assert result["hardware"] == "cpu-basic"

    def test_create_space_not_authenticated(self, provider):
        """Test space creation when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.create_space("test-space")

    def test_create_space_hf_hub_error(self, authenticated_provider):
        """Test space creation with HuggingFace Hub error."""
        from clustrix.cloud_providers.huggingface_spaces import HfHubHTTPError

        authenticated_provider.api.create_repo.side_effect = HfHubHTTPError(
            "Space already exists"
        )

        with pytest.raises(HfHubHTTPError):
            authenticated_provider.create_space("test-space")

    def test_create_space_unexpected_error(self, authenticated_provider):
        """Test space creation with unexpected error."""
        authenticated_provider.api.create_repo.side_effect = Exception(
            "Unexpected error"
        )

        with pytest.raises(Exception, match="Unexpected error"):
            authenticated_provider.create_space("test-space")

    def test_create_cluster(self, authenticated_provider):
        """Test create_cluster method."""
        with patch.object(authenticated_provider, "create_space") as mock_create:
            mock_create.return_value = {"space_id": "test_user/test-cluster"}

            result = authenticated_provider.create_cluster(
                "test-cluster", hardware="t4-small", sdk="gradio"
            )

            mock_create.assert_called_once_with(
                "test-cluster", hardware="t4-small", sdk="gradio"
            )
            assert result == {"space_id": "test_user/test-cluster"}

    def test_delete_cluster_success(self, authenticated_provider):
        """Test successful cluster deletion."""
        result = authenticated_provider.delete_cluster("test_user/test-space")

        assert result is True
        authenticated_provider.api.delete_repo.assert_called_once_with(
            repo_id="test_user/test-space", repo_type="space"
        )

    def test_delete_cluster_not_authenticated(self, provider):
        """Test cluster deletion when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.delete_cluster("test_user/test-space")

    def test_delete_cluster_hf_hub_error(self, authenticated_provider):
        """Test cluster deletion with HuggingFace Hub error."""
        from clustrix.cloud_providers.huggingface_spaces import HfHubHTTPError

        authenticated_provider.api.delete_repo.side_effect = HfHubHTTPError(
            "Space not found"
        )

        result = authenticated_provider.delete_cluster("test_user/test-space")

        assert result is False

    def test_delete_cluster_unexpected_error(self, authenticated_provider):
        """Test cluster deletion with unexpected error."""
        authenticated_provider.api.delete_repo.side_effect = Exception("Network error")

        result = authenticated_provider.delete_cluster("test_user/test-space")

        assert result is False

    def test_get_cluster_status_success(self, authenticated_provider):
        """Test successful cluster status retrieval."""
        mock_space_info = Mock()
        mock_space_info.sdk = "gradio"
        authenticated_provider.api.space_info.return_value = mock_space_info

        mock_runtime = Mock()
        mock_runtime.stage = "RUNNING"
        mock_runtime.hardware = "t4-small"
        authenticated_provider.api.get_space_runtime.return_value = mock_runtime

        result = authenticated_provider.get_cluster_status("test_user/test-space")

        assert result["space_id"] == "test_user/test-space"
        assert result["status"] == "running"
        assert result["hardware"] == "t4-small"
        assert result["sdk"] == "gradio"
        assert result["provider"] == "huggingface"
        assert result["cluster_type"] == "spaces"

    def test_get_cluster_status_runtime_error(self, authenticated_provider):
        """Test cluster status when runtime info fails."""
        mock_space_info = Mock()
        mock_space_info.sdk = "gradio"
        authenticated_provider.api.space_info.return_value = mock_space_info
        authenticated_provider.api.get_space_runtime.side_effect = Exception(
            "Runtime error"
        )

        result = authenticated_provider.get_cluster_status("test_user/test-space")

        assert result["status"] == "unknown"
        assert result["hardware"] == "unknown"
        assert result["sdk"] == "gradio"

    def test_get_cluster_status_not_found(self, authenticated_provider):
        """Test cluster status for non-existent space."""
        from clustrix.cloud_providers.huggingface_spaces import HfHubHTTPError

        authenticated_provider.api.space_info.side_effect = HfHubHTTPError(
            "404 Space not found"
        )

        result = authenticated_provider.get_cluster_status("test_user/nonexistent")

        assert result["space_id"] == "test_user/nonexistent"
        assert result["status"] == "not_found"
        assert result["provider"] == "huggingface"

    def test_get_cluster_status_not_authenticated(self, provider):
        """Test cluster status when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.get_cluster_status("test_user/test-space")

    def test_get_cluster_status_hf_hub_error(self, authenticated_provider):
        """Test cluster status with non-404 HuggingFace Hub error."""
        from clustrix.cloud_providers.huggingface_spaces import HfHubHTTPError

        authenticated_provider.api.space_info.side_effect = HfHubHTTPError(
            "500 Server error"
        )

        with pytest.raises(HfHubHTTPError):
            authenticated_provider.get_cluster_status("test_user/test-space")

    def test_get_cluster_status_unexpected_error(self, authenticated_provider):
        """Test cluster status with unexpected error."""
        authenticated_provider.api.space_info.side_effect = Exception("Network error")

        with pytest.raises(Exception, match="Network error"):
            authenticated_provider.get_cluster_status("test_user/test-space")

    def test_list_clusters_success(self, authenticated_provider):
        """Test successful cluster listing."""
        mock_space1 = Mock()
        mock_space1.id = "test_user/space1"
        mock_space1.sdk = "gradio"
        mock_space1.private = False

        mock_space2 = Mock()
        mock_space2.id = "test_user/space2"
        mock_space2.sdk = "streamlit"
        mock_space2.private = True

        authenticated_provider.api.list_spaces.return_value = [mock_space1, mock_space2]

        # Mock runtime info for each space
        def mock_runtime_side_effect(space_id):
            if space_id == "test_user/space1":
                runtime = Mock()
                runtime.stage = "RUNNING"
                runtime.hardware = "cpu-basic"
                return runtime
            elif space_id == "test_user/space2":
                runtime = Mock()
                runtime.stage = "BUILDING"
                runtime.hardware = "t4-small"
                return runtime
            else:
                raise Exception("Runtime error")

        authenticated_provider.api.get_space_runtime.side_effect = (
            mock_runtime_side_effect
        )

        result = authenticated_provider.list_clusters()

        assert len(result) == 2

        # Check first space
        assert result[0]["name"] == "space1"
        assert result[0]["space_id"] == "test_user/space1"
        assert result[0]["type"] == "space"
        assert result[0]["status"] == "running"
        assert result[0]["sdk"] == "gradio"
        assert result[0]["hardware"] == "cpu-basic"
        assert result[0]["private"] is False

        # Check second space
        assert result[1]["name"] == "space2"
        assert result[1]["space_id"] == "test_user/space2"
        assert result[1]["status"] == "building"
        assert result[1]["sdk"] == "streamlit"
        assert result[1]["hardware"] == "t4-small"
        assert result[1]["private"] is True

    def test_list_clusters_runtime_errors(self, authenticated_provider):
        """Test cluster listing with runtime errors."""
        mock_space = Mock()
        mock_space.id = "test_user/space1"
        mock_space.sdk = "gradio"
        mock_space.private = False

        authenticated_provider.api.list_spaces.return_value = [mock_space]
        authenticated_provider.api.get_space_runtime.side_effect = Exception(
            "Runtime error"
        )

        result = authenticated_provider.list_clusters()

        assert len(result) == 1
        assert result[0]["status"] == "unknown"
        assert result[0]["hardware"] == "unknown"

    def test_list_clusters_not_authenticated(self, provider):
        """Test cluster listing when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.list_clusters()

    def test_list_clusters_hf_hub_error(self, authenticated_provider):
        """Test cluster listing with HuggingFace Hub error."""
        from clustrix.cloud_providers.huggingface_spaces import HfHubHTTPError

        authenticated_provider.api.list_spaces.side_effect = HfHubHTTPError("API error")

        result = authenticated_provider.list_clusters()

        assert result == []

    def test_list_clusters_unexpected_error(self, authenticated_provider):
        """Test cluster listing with unexpected error."""
        authenticated_provider.api.list_spaces.side_effect = Exception("Network error")

        result = authenticated_provider.list_clusters()

        assert result == []

    def test_get_cluster_config_success(self, authenticated_provider):
        """Test successful cluster config retrieval."""
        mock_space_info = Mock()
        mock_space_info.sdk = "gradio"
        authenticated_provider.api.space_info.return_value = mock_space_info

        mock_runtime = Mock()
        mock_runtime.hardware = "t4-small"
        authenticated_provider.api.get_space_runtime.return_value = mock_runtime

        result = authenticated_provider.get_cluster_config("test_user/test-space")

        assert result["name"] == "HuggingFace Space - test_user/test-space"
        assert result["cluster_type"] == "api"
        assert (
            result["cluster_host"]
            == "https://huggingface.co/spaces/test_user/test-space"
        )
        assert (
            result["api_endpoint"]
            == "https://huggingface.co/spaces/test_user/test-space/api/predict"
        )
        assert result["default_cores"] == 4  # t4-small cores
        assert result["default_memory"] == "15GB"  # t4-small memory
        assert result["cost_monitoring"] is True
        assert result["provider"] == "huggingface"
        assert result["provider_config"]["space_id"] == "test_user/test-space"
        assert result["provider_config"]["hardware"] == "t4-small"
        assert result["provider_config"]["sdk"] == "gradio"
        assert result["provider_config"]["api_token"] == "***"

    def test_get_cluster_config_runtime_error(self, authenticated_provider):
        """Test cluster config when runtime info fails."""
        mock_space_info = Mock()
        mock_space_info.sdk = "gradio"
        authenticated_provider.api.space_info.return_value = mock_space_info
        authenticated_provider.api.get_space_runtime.side_effect = Exception(
            "Runtime error"
        )

        result = authenticated_provider.get_cluster_config("test_user/test-space")

        # Should use cpu-basic defaults when runtime fails
        assert result["default_cores"] == 2  # cpu-basic cores
        assert result["default_memory"] == "16GB"  # cpu-basic memory

    def test_get_cluster_config_not_authenticated(self, provider):
        """Test cluster config when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.get_cluster_config("test_user/test-space")

    def test_get_cluster_config_exception(self, authenticated_provider):
        """Test cluster config with exception."""
        authenticated_provider.api.space_info.side_effect = Exception("API error")

        result = authenticated_provider.get_cluster_config("test_user/test-space")

        # Should return basic config
        assert result["name"] == "HuggingFace Space - test_user/test-space"
        assert result["cluster_type"] == "api"
        assert (
            result["cluster_host"]
            == "https://huggingface.co/spaces/test_user/test-space"
        )
        assert result["provider"] == "huggingface"

    def test_estimate_cost_cpu_basic(self, provider):
        """Test cost estimation for CPU basic (free)."""
        result = provider.estimate_cost(hardware="cpu-basic", hours=10)

        assert result["compute"] == 0.0
        assert result["total"] == 0.0

    def test_estimate_cost_gpu(self, provider):
        """Test cost estimation for GPU hardware."""
        result = provider.estimate_cost(hardware="t4-small", hours=5)

        assert result["compute"] == 0.60 * 5
        assert result["total"] == 0.60 * 5

    def test_estimate_cost_unknown_hardware(self, provider):
        """Test cost estimation with unknown hardware."""
        result = provider.estimate_cost(hardware="unknown", hours=2)

        assert result["compute"] == 0.0  # Default to free
        assert result["total"] == 0.0

    def test_estimate_cost_defaults(self, provider):
        """Test cost estimation with default values."""
        result = provider.estimate_cost()

        assert result["compute"] == 0.0  # cpu-basic for 1 hour
        assert result["total"] == 0.0

    def test_get_available_instance_types(self, provider):
        """Test available instance types."""
        result = provider.get_available_instance_types()

        expected_types = [
            "cpu-basic",
            "cpu-upgrade",
            "t4-small",
            "t4-medium",
            "a10g-small",
            "a10g-large",
            "a100-large",
        ]

        assert result == expected_types

    def test_get_available_instance_types_with_region(self, provider):
        """Test available instance types with region parameter."""
        result = provider.get_available_instance_types(region="us-east-1")

        # Should return same types regardless of region
        assert "cpu-basic" in result
        assert "t4-small" in result

    def test_get_available_regions(self, provider):
        """Test available regions."""
        result = provider.get_available_regions()

        assert result == ["global"]

    def test_hardware_to_cores_mapping(self, provider):
        """Test hardware to cores mapping."""
        assert provider._hardware_to_cores("cpu-basic") == 2
        assert provider._hardware_to_cores("cpu-upgrade") == 8
        assert provider._hardware_to_cores("t4-small") == 4
        assert provider._hardware_to_cores("t4-medium") == 8
        assert provider._hardware_to_cores("a10g-small") == 4
        assert provider._hardware_to_cores("a10g-large") == 12
        assert provider._hardware_to_cores("a100-large") == 12
        assert provider._hardware_to_cores("unknown") == 2  # Default

    def test_hardware_to_memory_mapping(self, provider):
        """Test hardware to memory mapping."""
        assert provider._hardware_to_memory("cpu-basic") == "16GB"
        assert provider._hardware_to_memory("cpu-upgrade") == "32GB"
        assert provider._hardware_to_memory("t4-small") == "15GB"
        assert provider._hardware_to_memory("t4-medium") == "15GB"
        assert provider._hardware_to_memory("a10g-small") == "24GB"
        assert provider._hardware_to_memory("a10g-large") == "96GB"
        assert provider._hardware_to_memory("a100-large") == "142GB"
        assert provider._hardware_to_memory("unknown") == "16GB"  # Default


class TestHuggingFaceSpacesProviderEdgeCases:
    """Test edge cases and error handling."""

    def test_create_space_defaults(self):
        """Test space creation with default parameters."""
        provider = HuggingFaceSpacesProvider()
        provider.authenticated = True
        provider.username = "test_user"
        provider.api = Mock()
        provider.api.create_repo.return_value = (
            "https://huggingface.co/spaces/test_user/test-space"
        )

        result = provider.create_space("test-space")

        # Check defaults
        assert result["hardware"] == "cpu-basic"
        assert result["sdk"] == "gradio"
        assert result["private"] is False

    def test_list_clusters_complex_space_names(self):
        """Test cluster listing with complex space names."""
        provider = HuggingFaceSpacesProvider()
        provider.authenticated = True
        provider.username = "test_user"
        provider.api = Mock()

        mock_space = Mock()
        mock_space.id = "test_user/my-complex-space-name"
        mock_space.sdk = "gradio"
        mock_space.private = False

        provider.api.list_spaces.return_value = [mock_space]
        provider.api.get_space_runtime.side_effect = Exception("Runtime error")

        result = provider.list_clusters()

        assert len(result) == 1
        assert result[0]["name"] == "my-complex-space-name"
        assert result[0]["space_id"] == "test_user/my-complex-space-name"

    def test_get_cluster_status_no_space_info(self):
        """Test cluster status when space_info returns None."""
        provider = HuggingFaceSpacesProvider()
        provider.authenticated = True
        provider.api = Mock()
        provider.api.space_info.return_value = None
        provider.api.get_space_runtime.side_effect = Exception("Runtime error")

        result = provider.get_cluster_status("test_user/test-space")

        assert result["sdk"] == "unknown"

    def test_get_cluster_config_no_space_info(self):
        """Test cluster config when space_info returns None."""
        provider = HuggingFaceSpacesProvider()
        provider.authenticated = True
        provider.api = Mock()
        provider.api.space_info.return_value = None
        provider.api.get_space_runtime.side_effect = Exception("Runtime error")

        result = provider.get_cluster_config("test_user/test-space")

        assert result["provider_config"]["sdk"] == "unknown"

    def test_create_space_all_hardware_types(self):
        """Test space creation with all supported hardware types."""
        provider = HuggingFaceSpacesProvider()
        provider.authenticated = True
        provider.username = "test_user"
        provider.api = Mock()
        provider.api.create_repo.return_value = (
            "https://huggingface.co/spaces/test_user/test-space"
        )

        # Test all hardware types from the mapping
        hardware_types = [
            "cpu-upgrade",
            "t4-small",
            "t4-medium",
            "a10g-small",
            "a10g-large",
            "a100-large",
        ]

        with patch(
            "clustrix.cloud_providers.huggingface_spaces.SpaceHardware"
        ) as mock_hardware:
            # Mock all hardware enum values
            mock_hardware.CPU_UPGRADE = "cpu-upgrade"
            mock_hardware.T4_SMALL = "t4-small"
            mock_hardware.T4_MEDIUM = "t4-medium"
            mock_hardware.A10G_SMALL = "a10g-small"
            mock_hardware.A10G_LARGE = "a10g-large"
            mock_hardware.A100_LARGE = "a100-large"

            for hardware in hardware_types:
                result = provider.create_space(
                    f"test-space-{hardware}", hardware=hardware
                )
                assert result["hardware"] == hardware

                # Verify hardware request was made
                provider.api.request_space_hardware.assert_called_with(
                    repo_id=f"test_user/test-space-{hardware}", hardware=hardware
                )

    def test_estimate_cost_all_hardware_types(self):
        """Test cost estimation for all hardware types."""
        provider = HuggingFaceSpacesProvider()

        expected_costs = {
            "cpu-basic": 0.0,
            "cpu-upgrade": 0.03,
            "t4-small": 0.60,
            "t4-medium": 0.90,
            "a10g-small": 1.05,
            "a10g-large": 3.15,
            "a100-large": 4.13,
        }

        for hardware, expected_cost in expected_costs.items():
            result = provider.estimate_cost(hardware=hardware, hours=1)
            assert result["compute"] == expected_cost
            assert result["total"] == expected_cost
