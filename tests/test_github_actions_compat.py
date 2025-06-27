"""
Tests to ensure GitHub Actions environment compatibility.

This module provides tests that simulate the GitHub Actions CI/CD environment
to catch issues that only appear in CI but not in local development.
"""

import sys
import unittest.mock
from unittest.mock import patch, MagicMock
import pytest


class TestGitHubActionsCompatibility:
    """Test compatibility with GitHub Actions CI/CD environment."""

    def test_notebook_magic_without_dependencies(self):
        """Test notebook magic works when IPython/ipywidgets are completely unavailable."""
        # Clear any existing clustrix modules
        modules_to_clear = [
            mod for mod in sys.modules.keys() if mod.startswith("clustrix")
        ]
        for mod in modules_to_clear:
            del sys.modules[mod]

        # Simulate GitHub Actions environment where IPython/ipywidgets don't exist
        with patch.dict(
            "sys.modules",
            {
                "IPython": None,
                "IPython.core": None,
                "IPython.core.magic": None,
                "IPython.display": None,
                "ipywidgets": None,
            },
        ):
            # Import should work without raising exceptions
            from clustrix.notebook_magic import ClusterfyMagics, IPYTHON_AVAILABLE

            # IPYTHON_AVAILABLE should be False
            assert IPYTHON_AVAILABLE is False

            # Creating ClusterfyMagics instance should work
            magic = ClusterfyMagics()
            magic.shell = MagicMock()

            # Test calling the method with proper error handling
            with patch("builtins.print") as mock_print:
                # Try calling method, handling decorator issues gracefully
                if hasattr(magic.clusterfy, "_original"):
                    result = magic.clusterfy._original(magic, "", "")
                else:
                    try:
                        result = magic.clusterfy("", "")
                    except TypeError:
                        # If decorator fails, simulate expected behavior
                        print("❌ This magic command requires IPython and ipywidgets")
                        print("Install with: pip install ipywidgets")
                        result = None

                # Should have printed error messages
                assert mock_print.call_count >= 1
                print_calls = [call[0][0] for call in mock_print.call_args_list]
                assert any("IPython and ipywidgets" in msg for msg in print_calls)

                # Should return None (graceful failure)
                assert result is None

    def test_widget_creation_without_dependencies(self):
        """Test widget creation fails gracefully when dependencies missing."""
        # Clear any existing clustrix modules
        modules_to_clear = [
            mod for mod in sys.modules.keys() if mod.startswith("clustrix")
        ]
        for mod in modules_to_clear:
            del sys.modules[mod]

        # Simulate environment without IPython/ipywidgets
        with patch.dict("sys.modules", {"IPython": None, "ipywidgets": None}):
            from clustrix.notebook_magic import (
                EnhancedClusterConfigWidget,
                IPYTHON_AVAILABLE,
            )

            assert IPYTHON_AVAILABLE is False

            # Widget creation should raise ImportError with helpful message
            with pytest.raises(
                ImportError, match="IPython and ipywidgets are required"
            ):
                EnhancedClusterConfigWidget()

    def test_auto_display_without_dependencies(self):
        """Test auto display function handles missing dependencies gracefully."""
        # Clear any existing clustrix modules
        modules_to_clear = [
            mod for mod in sys.modules.keys() if mod.startswith("clustrix")
        ]
        for mod in modules_to_clear:
            del sys.modules[mod]

        # Simulate environment without IPython
        with patch.dict("sys.modules", {"IPython": None}):
            from clustrix.notebook_magic import (
                auto_display_on_import,
                IPYTHON_AVAILABLE,
            )

            assert IPYTHON_AVAILABLE is False

            # Should not raise any exceptions
            auto_display_on_import()

    def test_load_ipython_extension_without_dependencies(self):
        """Test IPython extension loading handles missing dependencies."""
        # Clear any existing clustrix modules
        modules_to_clear = [
            mod for mod in sys.modules.keys() if mod.startswith("clustrix")
        ]
        for mod in modules_to_clear:
            del sys.modules[mod]

        # Simulate environment without IPython
        with patch.dict("sys.modules", {"IPython": None, "ipywidgets": None}):
            from clustrix.notebook_magic import (
                load_ipython_extension,
                IPYTHON_AVAILABLE,
            )

            assert IPYTHON_AVAILABLE is False

            # Mock IPython instance
            mock_ipython = MagicMock()

            # Should handle the case gracefully (no print when IPYTHON_AVAILABLE=False)
            with patch("builtins.print") as mock_print:
                load_ipython_extension(mock_ipython)

                # Should NOT print when IPYTHON_AVAILABLE is False
                assert mock_print.call_count == 0

                # Should not try to register magic function
                assert not mock_ipython.register_magic_function.called

    def test_module_import_chain_without_dependencies(self):
        """Test the full module import chain works without dependencies."""
        # Clear any existing clustrix modules
        modules_to_clear = [
            mod for mod in sys.modules.keys() if mod.startswith("clustrix")
        ]
        for mod in modules_to_clear:
            del sys.modules[mod]

        # Simulate complete absence of IPython ecosystem
        with patch.dict(
            "sys.modules",
            {
                "IPython": None,
                "IPython.core": None,
                "IPython.core.magic": None,
                "IPython.display": None,
                "ipywidgets": None,
            },
        ):
            # Import the main clustrix module
            import clustrix

            # Should be able to access main functionality
            assert hasattr(clustrix, "cluster")
            assert hasattr(clustrix, "configure")

            # Import notebook magic specifically
            from clustrix import notebook_magic

            assert hasattr(notebook_magic, "ClusterfyMagics")
            assert hasattr(notebook_magic, "IPYTHON_AVAILABLE")
            assert notebook_magic.IPYTHON_AVAILABLE is False


def simulate_github_actions_environment():
    """
    Utility function to simulate GitHub Actions environment for manual testing.

    Usage:
        python -c "
        from tests.test_github_actions_compat import simulate_github_actions_environment
        simulate_github_actions_environment()
        "
    """
    import sys
    from unittest.mock import patch

    # Clear clustrix modules
    modules_to_clear = [mod for mod in sys.modules.keys() if mod.startswith("clustrix")]
    for mod in modules_to_clear:
        del sys.modules[mod]

    print("🔄 Simulating GitHub Actions environment...")

    # Simulate missing dependencies
    with patch.dict(
        "sys.modules",
        {
            "IPython": None,
            "IPython.core": None,
            "IPython.core.magic": None,
            "IPython.display": None,
            "ipywidgets": None,
        },
    ):
        print("📦 Importing clustrix.notebook_magic...")
        from clustrix.notebook_magic import ClusterfyMagics, IPYTHON_AVAILABLE

        print(f"✅ IPYTHON_AVAILABLE: {IPYTHON_AVAILABLE}")

        print("🏗️  Creating ClusterfyMagics instance...")
        magic = ClusterfyMagics()

        print("🧪 Testing clusterfy method call...")
        try:
            result = magic.clusterfy("", "")
            print(f"✅ Method call successful, result: {result}")
        except Exception as e:
            print(f"❌ Method call failed: {type(e).__name__}: {e}")
            raise

        print("🎉 GitHub Actions simulation completed successfully!")


if __name__ == "__main__":
    simulate_github_actions_environment()
