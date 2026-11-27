"""
Real-world testing infrastructure for Clustrix.

This package contains tests that use actual external resources: the
filesystem, a real sshd, a real SLURM controller and the HuggingFace API.
Nothing here is mocked.
"""

import os
import logging
from pathlib import Path
import tempfile

# Configure logging for real-world tests
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


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
