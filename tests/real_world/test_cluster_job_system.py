#!/usr/bin/env python3
"""
Simple test to verify the cluster job testing system works.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.real_world.credential_manager import get_credential_manager
from clustrix import cluster, configure


def test_system_components():
    """Test that all system components can be imported and initialized."""

    print("🔧 Testing Cluster Job Testing System Components...")

    # Test credential manager
    try:
        manager = get_credential_manager()
        print("✅ Credential manager: OK")
    except Exception as e:
        print(f"❌ Credential manager: {e}")
        return False

    # Test cluster decorator
    try:

        @cluster(cores=1, memory="1GB")
        def test_function(x):
            return x * 2

        # Check that decorator applied correctly
        assert hasattr(test_function, "_cluster_config")
        assert test_function._cluster_config["cores"] == 1
        assert test_function._cluster_config["memory"] == "1GB"
        print("✅ Cluster decorator: OK")
    except Exception as e:
        print(f"❌ Cluster decorator: {e}")
        return False

    # Test cluster job validator
    try:
        from tests.real_world.cluster_job_validator import (
            ClusterJobValidator,
            ClusterType,
        )

        print("✅ Cluster job validator: OK")
    except Exception as e:
        print(f"❌ Cluster job validator: {e}")
        return False

    # Test test files exist
    test_files = [
        "tests/real_world/test_slurm_job_submission_real.py",
        "tests/real_world/test_pbs_job_submission_real.py",
        "tests/real_world/test_sge_job_submission_real.py",
        "tests/real_world/test_kubernetes_job_submission_real.py",
        "tests/real_world/test_ssh_job_execution_real.py",
    ]

    project_root = Path(__file__).parent.parent
    for test_file in test_files:
        file_path = project_root / test_file
        if file_path.exists():
            print(f"✅ {test_file}: OK")
        else:
            print(f"❌ {test_file}: Missing")
            return False

    print("✅ All system components verified!")
    return True


def demo_cluster_function():
    """Demonstrate a simple cluster function."""

    print("\n🎯 Demonstrating Cluster Function Creation...")

    @cluster(cores=2, memory="2GB", time="00:10:00")
    def fibonacci(n: int) -> int:
        """Calculate Fibonacci number."""
        if n <= 1:
            return n
        a, b = 0, 1
        for _ in range(2, n + 1):
            a, b = b, a + b
        return b

    # Show that function is decorated properly
    print(f"✅ Function decorated with cluster config:")
    print(f"   - cores: {fibonacci._cluster_config['cores']}")
    print(f"   - memory: {fibonacci._cluster_config['memory']}")
    print(f"   - time: {fibonacci._cluster_config['time']}")

    # Note: We won't actually execute this since no cluster is configured
    print("✅ Function ready for cluster execution!")

    return True


def main():
    """Main test function."""
    print("🚀 Cluster Job Testing System Verification")
    print("=" * 50)

    # Test system components
    if not test_system_components():
        print("\n❌ System component test failed!")
        return 1

    # Demo cluster function
    if not demo_cluster_function():
        print("\n❌ Cluster function demo failed!")
        return 1

    print("\n🎉 All tests passed!")
    print("\nThe cluster job testing system is ready to use!")
    print("\nNext steps:")
    print("1. Configure cluster credentials (see docs/CREDENTIAL_SETUP.md)")
    print("2. Run cluster availability check:")
    print("   python scripts/run_cluster_job_tests.py --check-only")
    print("3. Run actual cluster job tests:")
    print("   python scripts/run_cluster_job_tests.py --cluster ssh --tests basic")

    return 0


if __name__ == "__main__":
    sys.exit(main())
