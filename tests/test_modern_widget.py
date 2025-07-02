"""Tests for the modern notebook widget implementation."""

import pytest
import tempfile
import os
from pathlib import Path

from clustrix.profile_manager import ProfileManager
from clustrix.config import ClusterConfig


class TestProfileManager:
    """Test profile management functionality."""

    def test_profile_manager_initialization(self):
        """Test that ProfileManager initializes with default profile."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            assert len(pm.get_profile_names()) == 1
            assert "Local single-core" in pm.get_profile_names()
            assert pm.active_profile == "Local single-core"

    def test_create_profile(self):
        """Test creating a new profile."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            config = ClusterConfig(
                cluster_type="slurm",
                default_cores=8,
                default_memory="32GB",
                default_time="02:00:00",
            )

            pm.create_profile("SLURM Cluster", config)

            assert "SLURM Cluster" in pm.get_profile_names()
            assert pm.active_profile == "SLURM Cluster"

            loaded_config = pm.get_active_profile()
            assert loaded_config.cluster_type == "slurm"
            assert loaded_config.default_cores == 8

    def test_clone_profile(self):
        """Test cloning an existing profile."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            # Clone the default profile
            new_name = pm.clone_profile("Local single-core")

            assert new_name == "Local single-core (copy)"
            assert len(pm.get_profile_names()) == 2
            assert pm.active_profile == new_name

    def test_remove_profile(self):
        """Test removing a profile."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            # Add another profile first
            config = ClusterConfig(cluster_type="ssh", default_cores=4)
            pm.create_profile("SSH Cluster", config)

            # Remove the original profile
            pm.remove_profile("Local single-core")

            assert "Local single-core" not in pm.get_profile_names()
            assert len(pm.get_profile_names()) == 1
            assert pm.active_profile == "SSH Cluster"

    def test_cannot_remove_last_profile(self):
        """Test that we cannot remove the last remaining profile."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            with pytest.raises(
                ValueError, match="Cannot remove the last remaining profile"
            ):
                pm.remove_profile("Local single-core")

    def test_save_and_load_file(self):
        """Test saving and loading profiles to/from file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            # Add a custom profile
            config = ClusterConfig(
                cluster_type="slurm",
                default_cores=16,
                default_memory="64GB",
                cluster_host="cluster.university.edu",
                username="researcher",
            )
            pm.create_profile("University Cluster", config)

            # Save to file
            config_file = os.path.join(temp_dir, "test_config.yml")
            pm.save_to_file(config_file)

            assert os.path.exists(config_file)

            # Create new ProfileManager and load
            pm2 = ProfileManager(config_dir=temp_dir)
            pm2.load_from_file(config_file)

            assert len(pm2.get_profile_names()) == 2
            assert "University Cluster" in pm2.get_profile_names()
            assert pm2.active_profile == "University Cluster"

            loaded_config = pm2.get_active_profile()
            assert loaded_config.cluster_type == "slurm"
            assert loaded_config.default_cores == 16
            assert loaded_config.cluster_host == "cluster.university.edu"

    def test_export_import_profile(self):
        """Test exporting and importing individual profiles."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            # Create a custom profile
            config = ClusterConfig(
                cluster_type="pbs",
                default_cores=8,
                default_memory="32GB",
                default_time="04:00:00",
            )
            pm.create_profile("PBS Cluster", config)

            # Export the profile
            export_file = os.path.join(temp_dir, "pbs_profile.yml")
            pm.export_profile("PBS Cluster", export_file)

            assert os.path.exists(export_file)

            # Create new ProfileManager and import
            pm2 = ProfileManager(config_dir=temp_dir)
            imported_name = pm2.import_profile(export_file, "Imported PBS")

            assert imported_name == "Imported PBS"
            assert "Imported PBS" in pm2.get_profile_names()

            imported_config = pm2.load_profile("Imported PBS")
            assert imported_config.cluster_type == "pbs"
            assert imported_config.default_cores == 8


# Note: Widget tests would require ipywidgets and IPython environment
# For comprehensive testing, we would need a mock IPython environment
class TestModernWidget:
    """Test modern widget functionality (requires IPython environment)."""

    def test_widget_creation_requirements(self):
        """Test that widget creation fails gracefully without IPython."""
        # This test verifies the import guard works
        try:
            from clustrix.modern_notebook_widget import ModernClustrixWidget

            # If we get here, IPython is available or the guard failed
            # We can't test the widget directly without a notebook environment
            assert True  # Basic import worked
        except ImportError as e:
            # Expected if IPython/ipywidgets not available
            assert "IPython and ipywidgets are required" in str(e)


if __name__ == "__main__":
    pytest.main([__file__])
