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
    ClusterConfigWidget = clustrix.notebook_magic.EnhancedClusterConfigWidget


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

    def test_default_configs_only_offer_retained_backends(self):
        """Every shipped template must name a backend clustrix still has."""
        retained = {"local", "ssh", "slurm", "huggingface"}
        for config_name, config_data in DEFAULT_CONFIGS.items():
            assert (
                config_data["cluster_type"] in retained
            ), f"Config '{config_name}' targets removed backend {config_data['cluster_type']}"

    def test_huggingface_config_field_mapping(self):
        """The HuggingFace template carries the fields hf_jobs.py reads."""
        hf_config = DEFAULT_CONFIGS["HuggingFace Jobs"]
        assert hf_config["cluster_type"] == "huggingface"
        assert "hf_hardware" in hf_config

    @pytest.mark.skipif(
        not WIDGET_DEPS_AVAILABLE, reason="Widget dependencies not available"
    )
    def test_widget_safe_value_setting(self):
        """Test that widget safely handles values not in dropdown options."""
        widget = ClusterConfigWidget(auto_display=False)

        # A hardware flavor that postdates the widget's hardcoded option list.
        test_config = {
            "cluster_type": "huggingface",
            "hf_hardware": "nonexistent-flavor",
            "hf_token": "test-hf-token",
        }

        # Add config to widget
        widget.configs["test_config"] = test_config

        # Should not crash when loading the configuration: the saved value is
        # authoritative, so _set_choice widens the options rather than raising
        # TraitError (issue #53).
        widget._load_config_to_widgets("test_config")

        assert widget.hf_token_field.value == "test-hf-token"
        assert widget.hf_hardware_field.value == "nonexistent-flavor"
        assert "nonexistent-flavor" in widget.hf_hardware_field.options

    @pytest.mark.skipif(
        not WIDGET_DEPS_AVAILABLE, reason="Widget dependencies not available"
    )
    def test_widget_save_load_cycle(self):
        """Test that widget can save and load configurations correctly."""
        widget = ClusterConfigWidget(auto_display=False)

        widget.cluster_type.value = "huggingface"
        widget.hf_hardware_field.value = "t4-small"
        widget.hf_token_field.value = "test-hf-token"

        # Save configuration
        saved_config = widget._save_config_from_widgets()

        # Verify saved configuration
        assert saved_config["cluster_type"] == "huggingface"
        assert saved_config["hf_hardware"] == "t4-small"
        # The token is saved under the field name hf_jobs.py reads.
        assert saved_config["hf_token"] == "test-hf-token"

    def test_huggingface_fields_in_config(self):
        """Test that ClusterConfig supports the HF fields the widget writes."""
        hf_config = ClusterConfig(
            cluster_type="huggingface",
            hf_hardware="t4-medium",
            hf_token="test-hf-token",
            hf_username="test-user",
        )
        assert hf_config.hf_hardware == "t4-medium"
        assert hf_config.hf_token == "test-hf-token"
        assert hf_config.hf_username == "test-user"

    @pytest.mark.skipif(
        not WIDGET_DEPS_AVAILABLE, reason="Widget dependencies not available"
    )
    def test_widget_dropdown_population(self):
        """Test that widget properly populates dropdown options."""
        widget = ClusterConfigWidget(auto_display=False)

        # The cluster type dropdown offers exactly the retained backends.
        assert list(widget.cluster_type.options) == [
            "local",
            "ssh",
            "slurm",
            "huggingface",
        ]

        # HuggingFace hardware flavors have sensible defaults
        assert len(widget.hf_hardware_field.options) > 0
        assert "cpu-basic" in widget.hf_hardware_field.options

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
    def test_widget_cluster_type_change_updates_sections(self):
        """Test that changing cluster type shows the right section."""
        widget = ClusterConfigWidget(auto_display=False)

        widget._on_cluster_type_change({"new": "huggingface"})
        assert widget.hf_fields.layout.display == ""
        assert widget.connection_fields.layout.display == "none"

        widget._on_cluster_type_change({"new": "ssh"})
        assert widget.connection_fields.layout.display == ""
        assert widget.hf_fields.layout.display == "none"

        widget._on_cluster_type_change({"new": "local"})
        assert widget.connection_fields.layout.display == "none"
        assert widget.hf_fields.layout.display == "none"
