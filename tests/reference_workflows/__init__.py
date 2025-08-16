"""
Reference workflow patterns for clustrix testing.

This module provides authentic user workflow patterns that all tests should follow.
These patterns demonstrate exactly how real users would interact with clustrix,
including proper @cluster decorator usage, realistic configurations, and
meaningful computations.
"""

# Import only the modules we've created so far
from .basic_usage import (
    test_basic_data_analysis_workflow,
    test_simple_computation_workflow,
    test_file_processing_workflow,
)

from .kubernetes_workflows import (
    test_kubernetes_auto_provisioning_workflow,
    test_kubernetes_multi_node_workflow,
    test_kubernetes_gpu_workflow,
)

from .data_analysis_workflows import (
    test_pandas_analysis_workflow,
    test_numpy_computation_workflow,
    test_machine_learning_workflow,
)

__all__ = [
    # Basic usage
    "test_basic_data_analysis_workflow",
    "test_simple_computation_workflow",
    "test_file_processing_workflow",
    # Kubernetes
    "test_kubernetes_auto_provisioning_workflow",
    "test_kubernetes_multi_node_workflow",
    "test_kubernetes_gpu_workflow",
    # Data analysis
    "test_pandas_analysis_workflow",
    "test_numpy_computation_workflow",
    "test_machine_learning_workflow",
]
