#!/usr/bin/env python3
"""
Test the reference workflow patterns to ensure they work correctly.

This validates that our reference patterns are actually functional
and can serve as the basis for refactoring other tests.
"""

import pytest
import os
from pathlib import Path

# Import reference workflows
from tests.reference_workflows.basic_usage import (
    test_basic_data_analysis_workflow,
    test_simple_computation_workflow,
    test_file_processing_workflow,
)

from tests.reference_workflows.kubernetes_workflows import (
    test_kubernetes_auto_provisioning_workflow,
    test_kubernetes_multi_node_workflow,
)

from tests.reference_workflows.data_analysis_workflows import (
    test_pandas_analysis_workflow,
    test_numpy_computation_workflow,
    test_machine_learning_workflow,
)


class TestReferenceWorkflows:
    """Test suite for reference workflow patterns."""

    @pytest.mark.real_world
    def test_basic_workflows_local(self):
        """Test basic workflows with local execution."""
        # Set environment for local testing
        os.environ["TEST_CLUSTER_TYPE"] = "local"

        # Test each basic workflow
        test_simple_computation_workflow()
        test_file_processing_workflow()

    @pytest.mark.real_world
    def test_data_analysis_workflows_local(self):
        """Test data analysis workflows with local execution."""
        os.environ["TEST_CLUSTER_TYPE"] = "local"

        # Test each data analysis workflow
        test_pandas_analysis_workflow()
        test_numpy_computation_workflow()
        test_machine_learning_workflow()

    @pytest.mark.real_world
    @pytest.mark.skipif(
        not os.getenv("K8S_TEST_ENABLED", "false").lower() == "true",
        reason="Kubernetes testing not enabled",
    )
    def test_kubernetes_workflows(self):
        """Test Kubernetes workflows with real provisioning."""
        # Use local provider for CI testing
        os.environ["K8S_TEST_PROVIDER"] = "local"

        test_kubernetes_auto_provisioning_workflow()
        test_kubernetes_multi_node_workflow()

    @pytest.mark.real_world
    @pytest.mark.skipif(
        not os.getenv("SLURM_TEST_ENABLED", "false").lower() == "true",
        reason="SLURM testing not enabled",
    )
    def test_slurm_workflows(self):
        """Test workflows with real SLURM cluster."""
        # Requires SLURM credentials in environment
        test_basic_data_analysis_workflow()


if __name__ == "__main__":
    # Run tests directly
    print("Testing Reference Workflows")
    print("=" * 70)

    # Test local workflows (should always work)
    print("\n📋 Testing Local Workflows...")
    os.environ["TEST_CLUSTER_TYPE"] = "local"

    try:
        print("  ✓ Testing simple computation...")
        test_simple_computation_workflow()
        print("    ✅ Simple computation workflow passed")
    except Exception as e:
        print(f"    ❌ Simple computation workflow failed: {e}")

    try:
        print("  ✓ Testing file processing...")
        test_file_processing_workflow()
        print("    ✅ File processing workflow passed")
    except Exception as e:
        print(f"    ❌ File processing workflow failed: {e}")

    try:
        print("  ✓ Testing pandas analysis...")
        test_pandas_analysis_workflow()
        print("    ✅ Pandas analysis workflow passed")
    except Exception as e:
        print(f"    ❌ Pandas analysis workflow failed: {e}")

    try:
        print("  ✓ Testing numpy computation...")
        test_numpy_computation_workflow()
        print("    ✅ Numpy computation workflow passed")
    except Exception as e:
        print(f"    ❌ Numpy computation workflow failed: {e}")

    try:
        print("  ✓ Testing machine learning...")
        test_machine_learning_workflow()
        print("    ✅ Machine learning workflow passed")
    except Exception as e:
        print(f"    ❌ Machine learning workflow failed: {e}")

    print("\n✅ Reference workflow testing complete!")
