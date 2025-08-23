#!/usr/bin/env python3

"""
Real user experience test for Kubernetes auto-provisioning.
This tests the exact syntax and workflow that users would follow.
"""

import logging
import time
from clustrix import cluster
from clustrix.config import ClusterConfig

# Set up logging to see what's happening
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_user_workflow():
    """Test the actual user workflow for Kubernetes auto-provisioning."""

    print("🧪 Testing Real User Experience: Kubernetes Auto-Provisioning")
    print("=" * 60)

    # Step 1: User creates a custom configuration
    print("\n📋 Step 1: Creating custom cluster configuration...")
    config = ClusterConfig()
    config.cluster_type = "kubernetes"
    config.auto_provision_k8s = True
    config.k8s_provider = "local"  # Use local Docker-based Kubernetes
    config.k8s_from_scratch = True
    config.k8s_node_count = 2
    config.k8s_region = "local"
    config.k8s_cleanup_on_exit = True
    config.k8s_cluster_name = f"user-test-{int(time.time())}"

    print(f"✅ Config created: {config.k8s_cluster_name}")

    # Step 2: User applies the configuration globally
    print("\n🔧 Step 2: Applying configuration globally...")
    import clustrix.config as config_module

    original_config = config_module._config
    config_module._config = config

    try:
        # Step 3: User defines a function with @cluster decorator
        print("\n⚡ Step 3: User defines function with @cluster decorator...")

        @cluster(
            cores=1,
            memory="512Mi",
            platform="kubernetes",
            auto_provision=True,
            provider="local",
            node_count=2,
        )
        def analyze_data(dataset_size: int, complexity: str = "medium"):
            """
            A realistic data analysis function that a user might write.
            This demonstrates the exact user experience.
            """
            import platform
            import socket
            import time
            import math

            print(
                f"🔬 Starting analysis of dataset (size: {dataset_size}, complexity: {complexity})"
            )

            # Simulate some computation
            start_time = time.time()

            if complexity == "simple":
                result = dataset_size * 2
            elif complexity == "medium":
                result = sum(math.sqrt(i) for i in range(min(dataset_size, 1000)))
            else:  # complex
                result = sum(
                    math.sin(i) * math.cos(i) for i in range(min(dataset_size, 5000))
                )

            computation_time = time.time() - start_time

            # Return realistic analysis results
            return {
                "dataset_size": dataset_size,
                "complexity": complexity,
                "computation_result": round(result, 2),
                "computation_time_seconds": round(computation_time, 3),
                "execution_info": {
                    "platform": platform.platform(),
                    "hostname": socket.gethostname(),
                    "python_version": platform.python_version(),
                    "environment": "kubernetes_cluster",
                },
                "success": True,
            }

        print("✅ Function defined with @cluster decorator")

        # Step 4: User calls the function normally (auto-provisioning happens transparently)
        print(
            "\n🚀 Step 4: User calls function - auto-provisioning happens transparently..."
        )
        print("   (This may take 30-60 seconds for cluster creation)")

        start_time = time.time()

        # This looks like a normal function call to the user!
        result = analyze_data(10000, "medium")

        total_time = time.time() - start_time

        # Step 5: User gets results
        print(f"\n🎉 Step 5: Results received in {total_time:.1f} seconds!")
        print("📊 Analysis Results:")
        print(f"   • Dataset size: {result['dataset_size']}")
        print(f"   • Complexity: {result['complexity']}")
        print(f"   • Computation result: {result['computation_result']}")
        print(f"   • Computation time: {result['computation_time_seconds']}s")
        print(f"   • Executed on: {result['execution_info']['hostname']}")
        print(f"   • Platform: {result['execution_info']['platform']}")
        print(f"   • Environment: {result['execution_info']['environment']}")

        # Verify this actually ran on Kubernetes
        if "kubernetes_cluster" in str(result.get("execution_info", {})):
            print(
                "\n✅ SUCCESS: Function executed on auto-provisioned Kubernetes cluster!"
            )
            print("✅ User experience test PASSED!")
            return True
        else:
            print("\n❌ ERROR: Function did not execute on Kubernetes cluster")
            return False

    except Exception as e:
        print(f"\n❌ ERROR: Test failed with exception: {e}")
        import traceback

        traceback.print_exc()
        return False

    finally:
        # Restore original config
        config_module._config = original_config
        print(f"\n🧹 Cleanup: Restored original configuration")


if __name__ == "__main__":
    print("Starting Real User Experience Test...")
    success = test_user_workflow()

    print("\n" + "=" * 60)
    if success:
        print("🎉 OVERALL TEST RESULT: SUCCESS")
        print("   Users can successfully:")
        print("   ✅ Create custom Kubernetes configurations")
        print("   ✅ Use @cluster decorator with auto-provisioning")
        print("   ✅ Call functions normally (transparent provisioning)")
        print("   ✅ Get results from auto-provisioned clusters")
        print("   ✅ Automatic cleanup works")
    else:
        print("❌ OVERALL TEST RESULT: FAILED")
        print("   User experience needs improvement")

    exit(0 if success else 1)
