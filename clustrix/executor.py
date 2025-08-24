"""Main executor module providing backward-compatible imports.

This module maintains backward compatibility by importing and re-exporting
the main ClusterExecutor class from the refactored executor_core module.

The original large executor.py has been split into focused modules:
- executor_connections.py: SSH and Kubernetes connection management
- executor_schedulers.py: SLURM, PBS, SGE job submission/monitoring
- executor_kubernetes.py: K8s-specific operations
- executor_cloud.py: Cloud provider workflows
- executor_core.py: Main ClusterExecutor coordination class

All imports from this module continue to work as before for backward compatibility.
"""

import logging

# Import the main class from the core module
from .executor_core import ClusterExecutor

# Import sub-managers for advanced usage
from .executor_connections import ConnectionManager
from .executor_schedulers import SchedulerManager
from .executor_scheduler_status import SchedulerStatusManager
from .executor_kubernetes import KubernetesJobManager
from .executor_cloud import CloudJobManager

# For backward compatibility with tests
logger = logging.getLogger(__name__)

# Backward compatibility exports
__all__ = [
    "ClusterExecutor",
    "ConnectionManager",
    "SchedulerManager",
    "SchedulerStatusManager",
    "KubernetesJobManager",
    "CloudJobManager",
]
