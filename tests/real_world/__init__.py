"""
Real-world testing infrastructure for Clustrix.

This module contains tests that use actual external resources:
- File system operations
- SSH connections
- API calls to cloud providers
- Database operations
- Visual verification of widgets

These tests are designed to validate that our code works correctly
with real external dependencies, not just mocked versions.
"""

import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any
import tempfile
import uuid

# Configure logging for real-world tests
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


class RealWorldTestManager:
    """Manager for real-world test resources and cost control."""

    def __init__(self):
        self.api_calls_today = 0
        self.daily_limit = 100
        self.cost_limit_usd = 5.0
        self.current_cost = 0.0
        self.test_session_id = str(uuid.uuid4())[:8]

    def can_make_api_call(self, estimated_cost: float = 0.01) -> bool:
        """Check if we can make an API call within cost limits."""
        if self.api_calls_today >= self.daily_limit:
            logger.warning(
                f"Daily API limit reached: {self.api_calls_today}/{self.daily_limit}"
            )
            return False
        if self.current_cost + estimated_cost > self.cost_limit_usd:
            logger.warning(
                f"Cost limit would be exceeded: ${self.current_cost + estimated_cost:.2f} > ${self.cost_limit_usd}"
            )
            return False
        return True

    def record_api_call(self, cost: float = 0.01) -> None:
        """Record an API call and its cost."""
        self.api_calls_today += 1
        self.current_cost += cost
        logger.info(
            f"API call recorded: {self.api_calls_today}/{self.daily_limit}, cost: ${self.current_cost:.2f}"
        )


class TestCredentials:
    """Manage test credentials from environment variables and 1Password."""

    def __init__(self):
        """Initialize with credential manager."""
        from .credential_manager import get_credential_manager

        self._manager = get_credential_manager()

    def get_aws_credentials(self) -> Optional[Dict[str, str]]:
        """Get AWS credentials from available sources."""
        return self._manager.get_aws_credentials()

    def get_azure_credentials(self) -> Optional[Dict[str, str]]:
        """Get Azure credentials from available sources."""
        return self._manager.get_azure_credentials()

    def get_gcp_credentials(self) -> Optional[Dict[str, str]]:
        """Get GCP credentials from available sources."""
        return self._manager.get_gcp_credentials()

    def get_ssh_credentials(self) -> Optional[Dict[str, str]]:
        """Get SSH credentials from available sources."""
        return self._manager.get_ssh_credentials()

    def get_slurm_credentials(self) -> Optional[Dict[str, str]]:
        """Get SLURM credentials from available sources."""
        return self._manager.get_slurm_credentials()

    def get_huggingface_credentials(self) -> Optional[Dict[str, str]]:
        """Get HuggingFace credentials from available sources."""
        return self._manager.get_huggingface_credentials()

    def get_lambda_cloud_credentials(self) -> Optional[Dict[str, str]]:
        """Get Lambda Cloud credentials from available sources."""
        return self._manager.get_lambda_cloud_credentials()

    def get_credential_status(self) -> Dict[str, bool]:
        """Get status of all credential types."""
        return self._manager.get_credential_status()

    def print_credential_status(self) -> None:
        """Print credential status for debugging."""
        return self._manager.print_credential_status()


class TempResourceManager:
    """Manager for temporary resources during testing."""

    def __init__(self):
        self.temp_dirs = []
        self.temp_files = []
        self.ssh_connections = []

    def create_temp_dir(self) -> Path:
        """Create a temporary directory for testing."""
        temp_dir = Path(tempfile.mkdtemp(prefix="clustrix_test_"))
        self.temp_dirs.append(temp_dir)
        return temp_dir

    def create_temp_file(self, content: str = "", suffix: str = "") -> Path:
        """Create a temporary file for testing."""
        fd, temp_path = tempfile.mkstemp(prefix="clustrix_test_", suffix=suffix)
        temp_file = Path(temp_path)

        with os.fdopen(fd, "w") as f:
            f.write(content)

        self.temp_files.append(temp_file)
        return temp_file

    def cleanup(self) -> None:
        """Clean up all temporary resources."""
        # Clean up temp files
        for temp_file in self.temp_files:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except Exception as e:
                logger.warning(f"Failed to cleanup temp file {temp_file}: {e}")

        # Clean up temp directories
        for temp_dir in self.temp_dirs:
            try:
                if temp_dir.exists():
                    import shutil

                    shutil.rmtree(temp_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup temp dir {temp_dir}: {e}")

        # Clean up SSH connections
        for connection in self.ssh_connections:
            try:
                connection.close()
            except Exception as e:
                logger.warning(f"Failed to close SSH connection: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()


# Global test manager instance
test_manager = RealWorldTestManager()
credentials = TestCredentials()
