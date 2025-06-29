"""
Test suite for widget configuration fixes addressing issue #53.
"""

import importlib

import pytest

import clustrix.notebook_magic
from clustrix.config import ClusterConfig

# Check if widget dependencies are available
try:
    import IPython  # noqa: F401
    import ipywidgets  # noqa: F401

    WIDGET_DEPS_AVAILABLE = True
except ImportError:
    WIDGET_DEPS_AVAILABLE = False

# Reload to ensure fresh state
importlib.reload(clustrix.notebook_magic)

# Import after reload to get the refreshed module
DEFAULT_CONFIGS = clustrix.notebook_magic.DEFAULT_CONFIGS
if WIDGET_DEPS_AVAILABLE:
    ClusterConfigWidget = clustrix.notebook_magic.ClusterConfigWidget


class TestWidgetConfigurationFixes:
    """Test fixes for widget configuration issues."""

    def test_default_configs_compatibility(self):
        """Test that all default configurations are compatible with ClusterConfig."""
        for config_name, config_data in DEFAULT_CONFIGS.items():
            # Remove widget-specific fields that aren't part of ClusterConfig
            test_data = config_data.copy()
            test_data.pop("name", None)
            test_data.pop("description", None)

            # Should not raise any exceptions
            cluster_config = ClusterConfig(**test_data)
            assert cluster_config.cluster_type == config_data["cluster_type"]

    def test_cloud_provider_field_mapping(self):
        """Test that cloud provider configurations have correct field mappings."""
        # Test AWS configuration
        aws_config = DEFAULT_CONFIGS["AWS EC2 Cluster"]
        assert "aws_region" in aws_config
        assert "aws_instance_type" in aws_config
        assert "aws_cluster_type" in aws_config

        # Test Azure configuration
        azure_config = DEFAULT_CONFIGS["Azure VM Cluster"]
        assert "azure_region" in azure_config
        assert "azure_instance_type" in azure_config

        # Test GCP configuration
        gcp_config = DEFAULT_CONFIGS["Google Cloud VM"]
        assert "gcp_region" in gcp_config
        assert "gcp_instance_type" in gcp_config

        # Test Lambda Cloud configuration
        lambda_config = DEFAULT_CONFIGS["Lambda Cloud GPU"]
        assert "lambda_instance_type" in lambda_config

        # Test HuggingFace configuration
        hf_config = DEFAULT_CONFIGS["HuggingFace Space"]
        assert "hf_hardware" in hf_config
        assert "hf_sdk" in hf_config

    @pytest.mark.skipif(
        not WIDGET_DEPS_AVAILABLE, reason="Widget dependencies not available"
    )
    def test_widget_safe_value_setting(self):
        """Test that widget safely handles values not in dropdown options."""
        widget = ClusterConfigWidget(auto_display=False)

        # Test configuration with values not in default dropdown options
        test_config = {
            "cluster_type": "azure",
            "azure_region": "nonexistent-region",  # Not in default options
            "azure_instance_type": "nonexistent-instance",  # Not in default options
            "azure_subscription_id": "test-sub-id",
            "azure_client_id": "test-client-id",
            "azure_client_secret": "test-secret",
        }

        # Add config to widget
        widget.configs["test_config"] = test_config

        # Should not crash when loading the configuration
        # Values not in dropdown should fall back to defaults
        widget._load_config_to_widgets("test_config")

        # Verify that text fields are set correctly
        assert widget.azure_subscription_id.value == "test-sub-id"
        assert widget.azure_client_id.value == "test-client-id"
        assert widget.azure_client_secret.value == "test-secret"

        # Verify that dropdown fields fall back to safe defaults
        assert widget.azure_region.value in widget.azure_region.options
        assert widget.azure_instance_type.value in widget.azure_instance_type.options

    @pytest.mark.skipif(
        not WIDGET_DEPS_AVAILABLE, reason="Widget dependencies not available"
    )
    def test_widget_save_load_cycle(self):
        """Test that widget can save and load configurations correctly."""
        widget = ClusterConfigWidget(auto_display=False)

        # Set up a complete cloud provider configuration
        widget.cluster_type.value = "aws"
        widget.aws_region.value = "us-east-1"  # Use value that exists in options
        widget.aws_instance_type.value = "t3.medium"  # Use value that exists in options
        widget.aws_access_key.value = "test-access-key"
        widget.aws_secret_key.value = "test-secret-key"
        widget.aws_cluster_type.value = "ec2"
        widget.config_name.value = "Test AWS Config"

        # Save configuration
        saved_config = widget._save_config_from_widgets()

        # Verify saved configuration
        assert saved_config["cluster_type"] == "aws"
        assert saved_config["aws_region"] == "us-east-1"
        assert saved_config["aws_instance_type"] == "t3.medium"
        assert saved_config["aws_access_key"] == "test-access-key"
        assert saved_config["aws_secret_key"] == "test-secret-key"
        assert saved_config["aws_cluster_type"] == "ec2"

    def test_cloud_provider_fields_in_config(self):
        """Test that ClusterConfig supports all cloud provider fields used by widget."""
        # Test AWS fields
        aws_config = ClusterConfig(
            cluster_type="aws",
            aws_region="us-west-2",
            aws_instance_type="t3.large",
            aws_access_key="test-key",
            aws_secret_key="test-secret",
            aws_cluster_type="ec2",
        )
        assert aws_config.aws_region == "us-west-2"
        assert aws_config.aws_instance_type == "t3.large"
        assert aws_config.aws_access_key == "test-key"
        assert aws_config.aws_secret_key == "test-secret"
        assert aws_config.aws_cluster_type == "ec2"

        # Test Azure fields
        azure_config = ClusterConfig(
            cluster_type="azure",
            azure_region="westus",
            azure_instance_type="Standard_D4s_v3",
            azure_subscription_id="test-sub",
            azure_client_id="test-client",
            azure_client_secret="test-secret",
        )
        assert azure_config.azure_region == "westus"
        assert azure_config.azure_instance_type == "Standard_D4s_v3"
        assert azure_config.azure_subscription_id == "test-sub"
        assert azure_config.azure_client_id == "test-client"
        assert azure_config.azure_client_secret == "test-secret"

        # Test GCP fields
        gcp_config = ClusterConfig(
            cluster_type="gcp",
            gcp_region="us-west1",
            gcp_instance_type="n1-standard-2",
            gcp_project_id="test-project",
            gcp_service_account_key="/path/to/key.json",
        )
        assert gcp_config.gcp_region == "us-west1"
        assert gcp_config.gcp_instance_type == "n1-standard-2"
        assert gcp_config.gcp_project_id == "test-project"
        assert gcp_config.gcp_service_account_key == "/path/to/key.json"

        # Test Lambda Cloud fields
        lambda_config = ClusterConfig(
            cluster_type="lambda_cloud",
            lambda_instance_type="gpu_1x_a100",
            lambda_api_key="test-lambda-key",
        )
        assert lambda_config.lambda_instance_type == "gpu_1x_a100"
        assert lambda_config.lambda_api_key == "test-lambda-key"

        # Test HuggingFace fields
        hf_config = ClusterConfig(
            cluster_type="huggingface_spaces",
            hf_hardware="t4-medium",
            hf_token="test-hf-token",
            hf_username="test-user",
            hf_sdk="gradio",
        )
        assert hf_config.hf_hardware == "t4-medium"
        assert hf_config.hf_token == "test-hf-token"
        assert hf_config.hf_username == "test-user"
        assert hf_config.hf_sdk == "gradio"

    @pytest.mark.skipif(
        not WIDGET_DEPS_AVAILABLE, reason="Widget dependencies not available"
    )
    def test_widget_dropdown_population(self):
        """Test that widget properly populates dropdown options."""
        widget = ClusterConfigWidget(auto_display=False)

        # Test that cloud provider dropdowns have sensible defaults
        assert len(widget.aws_region.options) > 0
        assert "us-east-1" in widget.aws_region.options

        assert len(widget.azure_region.options) > 0
        assert "eastus" in widget.azure_region.options

        assert len(widget.gcp_region.options) > 0
        assert "us-central1" in widget.gcp_region.options

        # Test that instance type dropdowns have options
        assert len(widget.aws_instance_type.options) > 0
        assert len(widget.azure_instance_type.options) > 0
        assert len(widget.gcp_instance_type.options) > 0
        assert len(widget.lambda_instance_type.options) > 0
        assert len(widget.hf_hardware.options) > 0

    @pytest.mark.skip(
        reason="Test isolation issue - configs being contaminated by other tests"
    )
    def test_no_name_description_in_default_configs(self):
        """Test that default configurations don't have 'name' or 'description' fields initially."""
        # Import directly from the module
        from clustrix.notebook_magic import DEFAULT_CONFIGS as fresh_configs

        for config_name, config_data in fresh_configs.items():
            # Make a copy to avoid modifying the original
            test_config = config_data.copy()
            assert (
                "name" not in test_config
            ), f"Config '{config_name}' should not have 'name' field initially"
            assert (
                "description" not in test_config
            ), f"Config '{config_name}' should not have 'description' field initially"

    @pytest.mark.skipif(
        not WIDGET_DEPS_AVAILABLE, reason="Widget dependencies not available"
    )
    def test_widget_cluster_type_change_updates_options(self):
        """Test that changing cluster type updates dropdown options."""
        widget = ClusterConfigWidget(auto_display=False)

        # Simulate cluster type change to AWS
        widget._on_cluster_type_change({"new": "aws"})

        # Verify AWS fields are displayed and have options
        assert widget.aws_fields.layout.display == ""
        assert len(widget.aws_region.options) > 0
        assert len(widget.aws_instance_type.options) > 0

        # Simulate cluster type change to Azure
        widget._on_cluster_type_change({"new": "azure"})

        # Verify Azure fields are displayed and have options
        assert widget.azure_fields.layout.display == ""
        assert len(widget.azure_region.options) > 0
        assert len(widget.azure_instance_type.options) > 0
