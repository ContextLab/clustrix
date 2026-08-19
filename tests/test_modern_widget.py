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
        """A template ships for every backend, with local selected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            names = pm.get_profile_names()
            assert set(names) == set(ProfileManager.BUILTIN_PROFILES)
            assert pm.active_profile == "Local single-core"

    def test_every_backend_has_a_starting_profile(self):
        """A dropdown with one entry is a text field with extra steps."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            offered = {pm.load_profile(n).cluster_type for n in pm.get_profile_names()}
            assert offered == {
                "local",
                "ssh",
                "slurm",
                "huggingface",
            }

    def test_gpu_template_does_not_pre_authorise_spending(self):
        """GPU flavors bill by the second; picking a profile must not opt in."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            gpu = pm.load_profile("HuggingFace Jobs (GPU)")
            assert gpu.hf_allow_gpu_flavors is False

    def test_reading_a_profile_does_not_select_it(self):
        """load_profile used to set active_profile, so merely inspecting
        profiles in a loop left the last one inspected marked active."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            for name in pm.get_profile_names():
                pm.load_profile(name)

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
            before = len(pm.get_profile_names())
            new_name = pm.clone_profile("Local single-core")

            assert new_name == "Local single-core (copy)"
            assert len(pm.get_profile_names()) == before + 1
            assert pm.active_profile == new_name

    def test_remove_profile(self):
        """Test removing a profile."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            # Add another profile first
            config = ClusterConfig(cluster_type="ssh", default_cores=4)
            pm.create_profile("SSH Cluster", config)

            before = len(pm.get_profile_names())

            # Remove the original profile
            pm.remove_profile("Local single-core")

            assert "Local single-core" not in pm.get_profile_names()
            assert len(pm.get_profile_names()) == before - 1
            assert pm.active_profile == "SSH Cluster"

    def test_cannot_remove_last_profile(self):
        """Removing every template must still leave something selected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pm = ProfileManager(config_dir=temp_dir)

            names = pm.get_profile_names()
            for name in names[:-1]:
                pm.remove_profile(name)

            assert len(pm.get_profile_names()) == 1
            with pytest.raises(
                ValueError, match="Cannot remove the last remaining profile"
            ):
                pm.remove_profile(pm.get_profile_names()[0])

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

            assert len(pm2.get_profile_names()) == len(pm.get_profile_names())
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
                cluster_type="slurm",
                default_cores=8,
                default_memory="32GB",
                default_time="04:00:00",
            )
            pm.create_profile("SLURM Cluster", config)

            # Export the profile
            export_file = os.path.join(temp_dir, "slurm_profile.yml")
            pm.export_profile("SLURM Cluster", export_file)

            assert os.path.exists(export_file)

            # Create new ProfileManager and import
            pm2 = ProfileManager(config_dir=temp_dir)
            imported_name = pm2.import_profile(export_file, "Imported SLURM")

            assert imported_name == "Imported SLURM"
            assert "Imported SLURM" in pm2.get_profile_names()

            imported_config = pm2.load_profile("Imported SLURM")
            assert imported_config.cluster_type == "slurm"
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
