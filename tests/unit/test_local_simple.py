#!/usr/bin/env python3

"""
Simple test of local Kubernetes execution without pytest.
"""

import logging
import os
import sys
import time

from clustrix import cluster
from clustrix.config import ClusterConfig
import clustrix.config as config_module

# Import local test functions
sys.path.append(os.path.dirname(__file__))
from test_functions import simple_computation as base_simple_computation  # noqa: E402

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def test_local_execution():
    """Test local Kubernetes execution."""

    # Set up local config
    local_config = ClusterConfig()
    local_config.cluster_type = "kubernetes"
    local_config.auto_provision_k8s = True
    local_config.k8s_from_scratch = True
    local_config.k8s_provider = "local"
    local_config.k8s_region = "local"
    local_config.k8s_node_count = 2
    local_config.k8s_cleanup_on_exit = True
    local_config.k8s_cluster_name = f"test-simple-{int(time.time())}"

    # Override global config
    original_config = config_module._config
    config_module._config = local_config

    try:
        logger.info("🧪 Testing simple function execution on local Kubernetes cluster")

        # Create decorated function using imported base function
        simple_computation = cluster(
            platform="kubernetes",
            auto_provision=True,
            provider="local",
            node_count=2,
            cores=1,  # Explicitly set cores
            memory="512Mi",  # Explicitly set memory in K8s format
            cluster_name=local_config.k8s_cluster_name,
        )(base_simple_computation)

        # Execute function
        logger.info(
            "🚀 Starting function execution (will auto-provision local cluster)"
        )
        start_time = time.time()

        result = simple_computation(7, 11)
        execution_time = time.time() - start_time

        # Verify results
        logger.info(f"✅ Function executed successfully in {execution_time:.1f}s")
        logger.info(f"📊 Result: {result}")

        return True

    finally:
        # Restore original config
        config_module._config = original_config


if __name__ == "__main__":
    success = test_local_execution()
    print(f"Test {'PASSED' if success else 'FAILED'}")
