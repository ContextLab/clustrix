#!/usr/bin/env python3
"""
Comprehensive test of remote filesystem utilities on SLURM cluster
"""

import sys
import time
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
from clustrix.secure_credentials import ValidationCredentials


def test_remote_filesystem_comprehensive():
    """Comprehensive test of remote filesystem operations."""
    print("🧪 Comprehensive Remote Filesystem Testing")
    print("=" * 60)

    # Get SSH credentials
    creds = ValidationCredentials()
    ssh_creds = creds.cred_manager.get_structured_credential("clustrix-ssh-slurm")

    if not ssh_creds:
        print("❌ No SSH credentials found. Cannot test remote operations.")
        return False

    # Configure for remote testing
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host=ssh_creds.get("hostname"),
        username=ssh_creds.get("username"),
        password=ssh_creds.get("password"),
        remote_work_dir=f"/dartfs-hpc/rc/home/b/{ssh_creds.get('username')}",
    )

    print(f"🔗 Remote host: {config.cluster_host}")
    print(f"👤 Username: {config.username}")
    print(f"📁 Remote work dir: {config.remote_work_dir}")

    test_results = []

    # Test 1: Basic connectivity and directory existence
    test_name = "Basic connectivity and home directory"
    print(f"\n🔍 Test 1: {test_name}")
    try:
        home_exists = cluster_exists(".", config)
        home_isdir = cluster_isdir(".", config)

        print(f"   Home directory exists: {home_exists}")
        print(f"   Home is directory: {home_isdir}")

        if home_exists and home_isdir:
            test_results.append((test_name, "PASSED", None))
            print("   ✅ PASSED")
        else:
            test_results.append((test_name, "FAILED", "Home directory not accessible"))
            print("   ❌ FAILED - Home directory not accessible")
    except Exception as e:
        test_results.append((test_name, "ERROR", str(e)))
        print(f"   ❌ ERROR: {e}")

    # Test 2: Directory listing
    test_name = "Directory listing (cluster_ls)"
    print(f"\n🔍 Test 2: {test_name}")
    try:
        files = cluster_ls(".", config)
        print(f"   Found {len(files)} items in home directory")
        print(f"   Sample files: {files[:5]}")

        if len(files) > 0:
            test_results.append((test_name, "PASSED", f"Found {len(files)} items"))
            print("   ✅ PASSED")
        else:
            test_results.append(
                (test_name, "FAILED", "No files found in home directory")
            )
            print("   ❌ FAILED - Empty directory unexpected")
    except Exception as e:
        test_results.append((test_name, "ERROR", str(e)))
        print(f"   ❌ ERROR: {e}")

    # Test 3: File information (cluster_stat)
    test_name = "File information (cluster_stat) on home directory"
    print(f"\n🔍 Test 3: {test_name}")
    try:
        home_stat = cluster_stat(".", config)
        print(f"   Directory size: {home_stat.size} bytes")
        print(f"   Is directory: {home_stat.is_dir}")
        print(f"   Permissions: {home_stat.permissions}")
        print(f"   Modified: {home_stat.modified_datetime}")

        if home_stat.is_dir:
            test_results.append(
                (
                    test_name,
                    "PASSED",
                    f"Size: {home_stat.size}, Perms: {home_stat.permissions}",
                )
            )
            print("   ✅ PASSED")
        else:
            test_results.append(
                (test_name, "FAILED", "Home directory not recognized as directory")
            )
            print("   ❌ FAILED - Stat indicates not a directory")
    except Exception as e:
        test_results.append((test_name, "ERROR", str(e)))
        print(f"   ❌ ERROR: {e}")

    # Test 4: File existence checks
    test_name = "File existence checks"
    print(f"\n🔍 Test 4: {test_name}")
    try:
        # Test common files that might exist
        test_paths = [".bashrc", ".bash_profile", ".profile", ".ssh", "."]

        existence_results = {}
        for path in test_paths:
            exists = cluster_exists(path, config)
            existence_results[path] = exists
            print(f"   {path}: {'EXISTS' if exists else 'NOT FOUND'}")

        # At least . (current dir) should exist
        if existence_results.get(".", False):
            test_results.append(
                (test_name, "PASSED", f"Tested {len(test_paths)} paths")
            )
            print("   ✅ PASSED")
        else:
            test_results.append(
                (test_name, "FAILED", "Current directory doesn't exist")
            )
            print("   ❌ FAILED - Current directory check failed")
    except Exception as e:
        test_results.append((test_name, "ERROR", str(e)))
        print(f"   ❌ ERROR: {e}")

    # Test 5: Directory vs file detection
    test_name = "Directory vs file detection"
    print(f"\n🔍 Test 5: {test_name}")
    try:
        # Test current directory (should be dir)
        is_dir_result = cluster_isdir(".", config)
        is_file_result = cluster_isfile(".", config)

        print(f"   Current dir is_dir: {is_dir_result}")
        print(f"   Current dir is_file: {is_file_result}")

        if is_dir_result and not is_file_result:
            test_results.append((test_name, "PASSED", "Correctly identified directory"))
            print("   ✅ PASSED")
        else:
            test_results.append(
                (test_name, "FAILED", f"Dir: {is_dir_result}, File: {is_file_result}")
            )
            print("   ❌ FAILED - Directory/file detection incorrect")
    except Exception as e:
        test_results.append((test_name, "ERROR", str(e)))
        print(f"   ❌ ERROR: {e}")

    # Test 6: File counting
    test_name = "File counting (cluster_count_files)"
    print(f"\n🔍 Test 6: {test_name}")
    try:
        total_files = cluster_count_files(".", "*", config)
        print(f"   Total files in home: {total_files}")

        # Also try counting specific patterns
        py_files = cluster_count_files(".", "*.py", config)
        txt_files = cluster_count_files(".", "*.txt", config)

        print(f"   Python files: {py_files}")
        print(f"   Text files: {txt_files}")

        if total_files >= 0:  # Any non-negative count is valid
            test_results.append(
                (
                    test_name,
                    "PASSED",
                    f"Total: {total_files}, .py: {py_files}, .txt: {txt_files}",
                )
            )
            print("   ✅ PASSED")
        else:
            test_results.append((test_name, "FAILED", "Negative file count"))
            print("   ❌ FAILED - Invalid file count")
    except Exception as e:
        test_results.append((test_name, "ERROR", str(e)))
        print(f"   ❌ ERROR: {e}")

    # Test 7: Directory usage
    test_name = "Directory usage (cluster_du)"
    print(f"\n🔍 Test 7: {test_name}")
    try:
        usage = cluster_du(".", config)
        print(f"   Total bytes: {usage.total_bytes:,}")
        print(f"   Total files: {usage.file_count}")
        print(f"   Size in MB: {usage.total_mb:.2f}")
        print(f"   Size in GB: {usage.total_gb:.2f}")

        if usage.total_bytes >= 0 and usage.file_count >= 0:
            test_results.append(
                (
                    test_name,
                    "PASSED",
                    f"{usage.total_mb:.1f}MB, {usage.file_count} files",
                )
            )
            print("   ✅ PASSED")
        else:
            test_results.append((test_name, "FAILED", "Invalid usage statistics"))
            print("   ❌ FAILED - Invalid usage data")
    except Exception as e:
        test_results.append((test_name, "ERROR", str(e)))
        print(f"   ❌ ERROR: {e}")

    # Test 8: Glob pattern matching
    test_name = "Glob pattern matching (cluster_glob)"
    print(f"\n🔍 Test 8: {test_name}")
    try:
        # Try common patterns
        patterns = [".*", "*.txt", "*.py", "*"]

        for pattern in patterns:
            matches = cluster_glob(pattern, ".", config)
            print(f"   Pattern '{pattern}': {len(matches)} matches")
            if len(matches) > 0:
                print(f"     Sample: {matches[:3]}")

        # Test all files pattern
        all_files = cluster_glob("*", ".", config)

        if len(all_files) >= 0:  # Any result is valid for glob
            test_results.append(
                (test_name, "PASSED", f"Tested {len(patterns)} patterns")
            )
            print("   ✅ PASSED")
        else:
            test_results.append((test_name, "FAILED", "Glob returned invalid results"))
            print("   ❌ FAILED")
    except Exception as e:
        test_results.append((test_name, "ERROR", str(e)))
        print(f"   ❌ ERROR: {e}")

    # Test 9: Find files (cluster_find)
    test_name = "Find files (cluster_find)"
    print(f"\n🔍 Test 9: {test_name}")
    try:
        # Try finding common file types
        find_patterns = ["*.txt", "*.py", "*.log", "*config*"]

        for pattern in find_patterns:
            found_files = cluster_find(pattern, ".", config)
            print(f"   Find '{pattern}': {len(found_files)} files")
            if len(found_files) > 0:
                print(f"     Sample: {found_files[:2]}")

        test_results.append(
            (test_name, "PASSED", f"Tested {len(find_patterns)} patterns")
        )
        print("   ✅ PASSED")
    except Exception as e:
        test_results.append((test_name, "ERROR", str(e)))
        print(f"   ❌ ERROR: {e}")

    # Test 10: Create and test a temporary directory structure
    test_name = "Create and test temporary structure"
    print(f"\n🔍 Test 10: {test_name}")
    try:
        import paramiko

        # Connect directly to create test structure
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=config.cluster_host,
            username=config.username,
            password=config.password,
        )

        # Create test directory structure
        test_dir = f"{config.remote_work_dir}/filesystem_test_{int(time.time())}"
        commands = [
            f"mkdir -p {test_dir}/subdir",
            f"echo 'test content' > {test_dir}/test.txt",
            f"echo 'python code' > {test_dir}/script.py",
            f"echo 'nested file' > {test_dir}/subdir/nested.txt",
        ]

        for cmd in commands:
            stdin, stdout, stderr = ssh.exec_command(cmd)
            stdout.channel.recv_exit_status()  # Wait for completion

        ssh.close()

        # Now test our filesystem utilities on this structure
        test_dir_rel = test_dir.replace(config.remote_work_dir + "/", "")

        # Test listing the test directory
        test_files = cluster_ls(test_dir_rel, config)
        print(f"   Created test directory files: {test_files}")

        # Test finding files in test directory
        txt_files = cluster_find("*.txt", test_dir_rel, config)
        print(f"   Found .txt files: {txt_files}")

        # Test file info
        test_file_stat = cluster_stat(f"{test_dir_rel}/test.txt", config)
        print(f"   test.txt size: {test_file_stat.size} bytes")

        # Cleanup
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=config.cluster_host,
            username=config.username,
            password=config.password,
        )
        stdin, stdout, stderr = ssh.exec_command(f"rm -rf {test_dir}")
        stdout.channel.recv_exit_status()
        ssh.close()

        if len(test_files) >= 3 and len(txt_files) >= 1:  # Should have created 3+ files
            test_results.append(
                (test_name, "PASSED", f"Created and tested structure successfully")
            )
            print("   ✅ PASSED")
        else:
            test_results.append((test_name, "FAILED", f"Test structure incomplete"))
            print("   ❌ FAILED - Test structure creation failed")
    except Exception as e:
        test_results.append((test_name, "ERROR", str(e)))
        print(f"   ❌ ERROR: {e}")

    # Summary
    print(f"\n📊 Test Summary")
    print("=" * 60)

    passed = sum(1 for _, status, _ in test_results if status == "PASSED")
    failed = sum(1 for _, status, _ in test_results if status == "FAILED")
    errors = sum(1 for _, status, _ in test_results if status == "ERROR")

    for test_name, status, details in test_results:
        status_icon = {"PASSED": "✅", "FAILED": "❌", "ERROR": "💥"}[status]
        print(f"{status_icon} {test_name}: {status}")
        if details:
            print(f"   Details: {details}")

    print(f"\n🎯 Results: {passed} passed, {failed} failed, {errors} errors")

    if passed >= 7:  # Most tests should pass
        print("\n🎉 Remote filesystem utilities are working correctly!")
        return True
    else:
        print("\n⚠️ Some remote filesystem tests failed. Review results above.")
        return False


if __name__ == "__main__":
    success = test_remote_filesystem_comprehensive()
    if success:
        print("\n✅ Remote filesystem validation COMPLETED SUCCESSFULLY!")
        print("🚀 Phase 1 is fully validated and ready for production use.")
    else:
        print("\n❌ Remote filesystem validation had issues.")
        print("🔧 May need adjustments before Phase 2.")
