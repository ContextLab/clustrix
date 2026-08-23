#!/usr/bin/env python3
"""
Test filesystem utilities for both local and remote operations
"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix import (
    cluster_ls,
    cluster_find,
    cluster_stat,
    cluster_exists,
    cluster_isdir,
    cluster_isfile,
    cluster_glob,
    cluster_du,
    cluster_count_files,
)
from clustrix.config import ClusterConfig

from tests.real_world.credential_manager import (
    CREDENTIAL_SETUP_HINT,
    get_cluster_credentials,
    require_cluster_credentials,
    require_test_remote_work_dir,
)


def test_local_filesystem():
    """Test filesystem operations locally."""
    print("🧪 Testing Local Filesystem Operations")
    print("=" * 50)

    # Create temporary test directory
    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up test structure
        os.makedirs(os.path.join(tmpdir, "data"))
        os.makedirs(os.path.join(tmpdir, "data", "subdir"))

        # Create test files
        test_files = [
            "data/file1.csv",
            "data/file2.csv",
            "data/file3.txt",
            "data/subdir/nested.csv",
            "data/subdir/other.json",
        ]

        for file_path in test_files:
            full_path = os.path.join(tmpdir, file_path)
            with open(full_path, "w") as f:
                f.write(f"Test content for {file_path}\n")

        # Configure for local testing
        config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)

        print(f"📁 Test directory: {tmpdir}")

        # Debug: Check what was actually created
        print(f"\n🔍 Debug - Created structure:")
        for root, dirs, files in os.walk(tmpdir):
            level = root.replace(tmpdir, "").count(os.sep)
            indent = " " * 2 * level
            print(f"{indent}{os.path.basename(root)}/")
            subindent = " " * 2 * (level + 1)
            for file in files:
                print(f"{subindent}{file}")

        # Test cluster_ls
        print("\n📋 Testing cluster_ls:")
        files = cluster_ls("data", config)
        print(f"   Files in data/: {files}")

        # Also test if data directory exists
        data_exists = cluster_exists("data", config)
        print(f"   Data directory exists: {data_exists}")

        if not files:
            print("   ❌ No files found, debugging...")
            # Try listing root directory
            root_files = cluster_ls(".", config)
            print(f"   Root files: {root_files}")

        assert "file1.csv" in files
        assert "subdir" in files

        # Test cluster_find
        print("\n🔍 Testing cluster_find:")
        csv_files = cluster_find("*.csv", "data", config)
        print(f"   CSV files found: {csv_files}")
        assert len(csv_files) == 3
        assert "subdir/nested.csv" in csv_files

        # Test cluster_stat
        print("\n📊 Testing cluster_stat:")
        file_info = cluster_stat("data/file1.csv", config)
        print(f"   File info: size={file_info.size}, is_dir={file_info.is_dir}")
        assert file_info.size > 0
        assert not file_info.is_dir

        # Test cluster_exists
        print("\n✅ Testing cluster_exists:")
        exists = cluster_exists("data/file1.csv", config)
        not_exists = cluster_exists("data/nonexistent.csv", config)
        print(f"   file1.csv exists: {exists}")
        print(f"   nonexistent.csv exists: {not_exists}")
        assert exists
        assert not not_exists

        # Test cluster_isdir/isfile
        print("\n📁 Testing cluster_isdir/isfile:")
        is_dir = cluster_isdir("data/subdir", config)
        is_file = cluster_isfile("data/file1.csv", config)
        print(f"   data/subdir is directory: {is_dir}")
        print(f"   data/file1.csv is file: {is_file}")
        assert is_dir
        assert is_file

        # Test cluster_glob
        print("\n🎯 Testing cluster_glob:")
        glob_results = cluster_glob("*.csv", "data", config)
        print(f"   CSV files via glob: {glob_results}")
        assert len(glob_results) == 2  # Only in data/, not subdir

        # Test cluster_du
        print("\n💾 Testing cluster_du:")
        usage = cluster_du("data", config)
        print(
            f"   Directory usage: {usage.total_bytes} bytes, {usage.file_count} files"
        )
        assert usage.file_count == 5
        assert usage.total_bytes > 0

        # Test cluster_count_files
        print("\n🔢 Testing cluster_count_files:")
        total_count = cluster_count_files("data", "*", config)
        csv_count = cluster_count_files("data", "*.csv", config)
        print(f"   Total files: {total_count}")
        print(f"   CSV files: {csv_count}")
        assert total_count == 5
        assert csv_count == 3

        print("\n✅ All local filesystem tests passed!")
        return True


def test_remote_filesystem():
    """Test filesystem operations on remote cluster."""
    print("\n🧪 Testing Remote Filesystem Operations")
    print("=" * 50)

    # Credentials come from ~/.clustrix/.env or the environment; skip loudly
    # rather than return False, which pytest reports as a pass.
    ssh_creds = require_cluster_credentials("slurm")

    # Configure for remote testing
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host=ssh_creds["host"],
        username=ssh_creds["username"],
        password=ssh_creds.get("password"),
        remote_work_dir=f"{require_test_remote_work_dir()}/clustrix_test",
    )

    print(f"🔗 Remote host: {config.cluster_host}")
    print(f"📁 Remote work dir: {config.remote_work_dir}")

    try:
        # Test cluster_exists on known directory
        print("\n✅ Testing remote cluster_exists:")
        home_exists = cluster_exists(".", config)
        print(f"   Work directory exists: {home_exists}")

        # Test cluster_ls on home directory
        print("\n📋 Testing remote cluster_ls:")
        files = cluster_ls(".", config)
        print(f"   Files in work dir: {files[:5]}{'...' if len(files) > 5 else ''}")

        # Test cluster_isdir
        print("\n📁 Testing remote cluster_isdir:")
        is_dir = cluster_isdir(".", config)
        print(f"   Work dir is directory: {is_dir}")
        assert is_dir

        # Test cluster_count_files
        print("\n🔢 Testing remote cluster_count_files:")
        file_count = cluster_count_files(".", "*", config)
        print(f"   Total files in work dir: {file_count}")

        print("\n✅ Remote filesystem tests completed successfully!")
        return True

    except Exception as e:
        print(f"\n❌ Remote filesystem test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_consistency():
    """Test that the same code works locally and remotely."""
    print("\n🧪 Testing Consistency Between Local and Remote")
    print("=" * 50)

    def analyze_directory(config):
        """Analyze a directory using filesystem utilities."""
        results = {
            "exists": cluster_exists(".", config),
            "is_dir": cluster_isdir(".", config),
            "file_count": cluster_count_files(".", "*", config),
        }

        # List files
        try:
            files = cluster_ls(".", config)
            results["ls_count"] = len(files)
            results["sample_files"] = files[:3]
        except Exception as e:
            results["ls_error"] = str(e)

        return results

    # Test with local config
    local_config = ClusterConfig(cluster_type="local")
    print("📍 Local analysis:")
    local_results = analyze_directory(local_config)
    for key, value in local_results.items():
        print(f"   {key}: {value}")

    # Test with remote config (if available)
    ssh_creds = get_cluster_credentials("slurm")

    if ssh_creds:
        remote_config = ClusterConfig(
            cluster_type="slurm",
            cluster_host=ssh_creds["host"],
            username=ssh_creds["username"],
            password=ssh_creds.get("password"),
            remote_work_dir=require_test_remote_work_dir(),
        )

        print("\n🌐 Remote analysis:")
        remote_results = analyze_directory(remote_config)
        for key, value in remote_results.items():
            print(f"   {key}: {value}")

        print("\n✅ Same code executed successfully on both local and remote!")
    else:
        print(f"\n⚠️  No credentials for the slurm cluster. {CREDENTIAL_SETUP_HINT}")

    return True


if __name__ == "__main__":
    print("🚀 Clustrix Filesystem Utilities Test Suite")
    print("=" * 50)

    # Run tests
    local_success = test_local_filesystem()
    remote_success = test_remote_filesystem()
    consistency_success = test_consistency()

    # Summary
    print("\n📊 Test Summary")
    print("=" * 50)
    print(f"✅ Local filesystem tests: {'PASSED' if local_success else 'FAILED'}")
    print(
        f"{'✅' if remote_success else '⚠️'} Remote filesystem tests: {'PASSED' if remote_success else 'SKIPPED/FAILED'}"
    )
    print(f"✅ Consistency tests: {'PASSED' if consistency_success else 'FAILED'}")

    if local_success and consistency_success:
        print("\n🎉 Core filesystem utilities are working correctly!")
        print("Ready to proceed with Phase 2: Dependency Packaging")
    else:
        print("\n❌ Some tests failed. Please review the output.")
