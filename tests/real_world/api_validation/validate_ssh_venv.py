#!/usr/bin/env python3
"""
SSH Virtual Environment Validation Script

This script specifically tests virtual environment creation and management
on remote SSH clusters, which is critical for Clustrix functionality.
"""

import sys
import time
import logging
from pathlib import Path

# Add the clustrix package to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_venv_creation(hostname, username, password=None, key_file=None):
    """Test virtual environment creation and package installation."""
    print(f"🐍 Virtual Environment Test: {username}@{hostname}")
    print("=" * 60)

    try:
        import paramiko
    except ImportError:
        print("❌ paramiko not available")
        return False

    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # Connect
        connect_kwargs = {"hostname": hostname, "username": username, "timeout": 30}

        if key_file:
            connect_kwargs["key_filename"] = key_file
        elif password:
            connect_kwargs["password"] = password

        ssh_client.connect(**connect_kwargs)
        print("✅ SSH connection established")

        # Create unique test directory
        test_id = int(time.time())
        test_dir = f"/tmp/clustrix_venv_test_{test_id}"
        venv_name = f"clustrix_test_env_{test_id}"
        venv_path = f"{test_dir}/{venv_name}"

        print(f"📁 Creating test directory: {test_dir}")
        stdin, stdout, stderr = ssh_client.exec_command(f"mkdir -p {test_dir}")
        error = stderr.read().decode().strip()
        if error:
            print(f"❌ Failed to create test directory: {error}")
            return False

        # Test 1: Check venv module availability
        print("🔍 Testing venv module availability...")
        stdin, stdout, stderr = ssh_client.exec_command("python3 -m venv --help")
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()

        if "usage:" in output.lower() and not error:
            print("✅ python3 -m venv available")
            use_virtualenv = False
        else:
            print(f"❌ venv module not available: {error}")
            # Try alternative method
            print("🔍 Trying virtualenv...")
            stdin, stdout, stderr = ssh_client.exec_command("virtualenv --help")
            output = stdout.read().decode().strip()
            error = stderr.read().decode().strip()

            if "usage:" in output.lower():
                print("✅ virtualenv available as fallback")
                use_virtualenv = True
            else:
                print(f"❌ No virtual environment tools available: {error}")
                return False

        # Test 2: Create virtual environment
        print(f"🏗️  Creating virtual environment: {venv_path}")
        if use_virtualenv:
            venv_cmd = f"cd {test_dir} && virtualenv {venv_name}"
        else:
            venv_cmd = f"cd {test_dir} && python3 -m venv {venv_name}"

        stdin, stdout, stderr = ssh_client.exec_command(venv_cmd, timeout=120)
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()

        if error and "error" in error.lower():
            print(f"❌ Failed to create virtual environment: {error}")
            return False
        else:
            print("✅ Virtual environment created successfully")

        # Test 3: Verify virtual environment structure
        print("🔍 Verifying virtual environment structure...")
        stdin, stdout, stderr = ssh_client.exec_command(f"ls -la {venv_path}")
        output = stdout.read().decode().strip()

        required_dirs = ["bin", "lib"]
        found_dirs = []
        for line in output.split("\n"):
            parts = line.split()
            if len(parts) >= 9:  # Standard ls -la format
                permissions = parts[0]
                name = parts[-1]
                if permissions.startswith("d") and name in required_dirs:
                    found_dirs.append(name)

        if len(found_dirs) >= 2:
            print(f"✅ Virtual environment structure valid: {found_dirs}")
        else:
            print(f"❌ Invalid venv structure. Found: {found_dirs}")
            print(f"   Directory listing: {output}")
            return False

        # Test 4: Activate virtual environment and test Python
        print("🔄 Testing virtual environment activation...")
        activate_and_test = f"""
        cd {test_dir} && \\
        source {venv_name}/bin/activate && \\
        which python3 && \\
        python3 --version && \\
        echo "VENV_PYTHON_PATH=$(which python3)" && \\
        echo "VENV_TEST_SUCCESS"
        """

        stdin, stdout, stderr = ssh_client.exec_command(activate_and_test, timeout=60)
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()

        if "VENV_TEST_SUCCESS" in output and venv_name in output:
            print("✅ Virtual environment activation successful")
            print(
                f"   Python path in venv: {[line for line in output.split(chr(10)) if 'VENV_PYTHON_PATH=' in line][0] if any('VENV_PYTHON_PATH=' in line for line in output.split(chr(10))) else 'Not found'}"
            )
        else:
            print(f"❌ Virtual environment activation failed")
            print(f"   Output: {output}")
            print(f"   Error: {error}")
            return False

        # Test 5: Install packages in virtual environment
        print("📦 Testing package installation in virtual environment...")
        install_packages = f"""
        cd {test_dir} && \\
        source {venv_name}/bin/activate && \\
        pip3 install --quiet requests cloudpickle && \\
        python3 -c "import requests, cloudpickle; print('PACKAGE_IMPORT_SUCCESS')" && \\
        pip3 list | grep -E "(requests|cloudpickle)" && \\
        echo "INSTALL_TEST_SUCCESS"
        """

        stdin, stdout, stderr = ssh_client.exec_command(install_packages, timeout=180)
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()

        if "PACKAGE_IMPORT_SUCCESS" in output and "INSTALL_TEST_SUCCESS" in output:
            print("✅ Package installation successful")
            # Show installed packages
            package_lines = [
                line
                for line in output.split("\\n")
                if any(pkg in line.lower() for pkg in ["requests", "cloudpickle"])
            ]
            for line in package_lines:
                print(f"   {line}")
        else:
            print(f"❌ Package installation failed")
            print(f"   Output: {output[-300:]}")  # Last 300 chars
            print(f"   Error: {error[-300:]}")
            return False

        # Test 6: Test isolation from system Python
        print("🔒 Testing virtual environment isolation...")
        isolation_test = f"""
        cd {test_dir} && \\
        echo "=== SYSTEM PYTHON ===" && \\
        python3 -c "import sys; print('System Python:', sys.executable)" && \\
        echo "=== VENV PYTHON ===" && \\
        source {venv_name}/bin/activate && \\
        python3 -c "import sys; print('Venv Python:', sys.executable)" && \\
        echo "ISOLATION_TEST_SUCCESS"
        """

        stdin, stdout, stderr = ssh_client.exec_command(isolation_test, timeout=60)
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()

        if "ISOLATION_TEST_SUCCESS" in output:
            system_python = None
            venv_python = None

            lines = output.split("\\n")
            for line in lines:
                if "System Python:" in line:
                    system_python = line.split(":")[1].strip()
                elif "Venv Python:" in line:
                    venv_python = line.split(":")[1].strip()

            if system_python and venv_python and system_python != venv_python:
                print("✅ Virtual environment isolation working")
                print(f"   System Python: {system_python}")
                print(f"   Venv Python: {venv_python}")
            else:
                print("⚠️  Virtual environment isolation unclear")
                print(f"   System: {system_python}, Venv: {venv_python}")
        else:
            print("❌ Isolation test failed")
            print(f"   Error: {error}")

        # Test 7: Test Clustrix-like workflow
        print("🧪 Testing Clustrix-like workflow in venv...")
        clustrix_workflow = f"""
        cd {test_dir} && \\
        source {venv_name}/bin/activate && \\
        python3 -c "
import sys
import json
import cloudpickle
from datetime import datetime

def clustrix_test_function(n=1000):
    '''Simulate a Clustrix function execution in isolated venv.'''
    result = sum(i**2 for i in range(n))
    return {{
        'computation_result': result,
        'python_executable': sys.executable,
        'venv_isolated': '{venv_name}' in sys.executable,
        'timestamp': datetime.now().isoformat(),
        'success': True
    }}

# Serialize function (like Clustrix does)
serialized_func = cloudpickle.dumps(clustrix_test_function)
deserialized_func = cloudpickle.loads(serialized_func)

# Execute deserialized function
result = deserialized_func(500)
print('CLUSTRIX_WORKFLOW_RESULT:', json.dumps(result))
print('CLUSTRIX_WORKFLOW_SUCCESS')
"
        """

        stdin, stdout, stderr = ssh_client.exec_command(clustrix_workflow, timeout=60)
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()

        if "CLUSTRIX_WORKFLOW_SUCCESS" in output:
            print("✅ Clustrix-like workflow successful")

            # Extract and display result
            try:
                for line in output.split("\\n"):
                    if "CLUSTRIX_WORKFLOW_RESULT:" in line:
                        result_json = line.split("CLUSTRIX_WORKFLOW_RESULT:")[1].strip()
                        import json

                        result = json.loads(result_json)
                        print(
                            f"   Computation result: {result.get('computation_result')}"
                        )
                        print(f"   Venv isolated: {result.get('venv_isolated')}")
                        break
            except Exception as e:
                print(f"   ⚠️  Could not parse result: {e}")
        else:
            print("❌ Clustrix workflow failed")
            print(f"   Output: {output[-200:]}")
            print(f"   Error: {error[-200:]}")
            return False

        # Cleanup
        print("🧹 Cleaning up test environment...")
        stdin, stdout, stderr = ssh_client.exec_command(f"rm -rf {test_dir}")
        print("✅ Cleanup completed")

        ssh_client.close()
        return True

    except Exception as e:
        print(f"❌ Virtual environment test failed: {e}")
        return False
    finally:
        try:
            ssh_client.close()
        except:
            pass


def main():
    """Main validation function."""
    print("🚀 Starting SSH Virtual Environment Validation")
    print("=" * 70)

    # Get credentials
    creds = ValidationCredentials()

    # Test both clusters
    clusters = [
        ("clustrix-ssh-slurm", "SLURM Cluster (ndoli)"),
        ("clustrix-ssh-gpu", "GPU Server (tensor01)"),
    ]

    results = {}

    for cred_name, cluster_description in clusters:
        print(f"\\n🎯 Testing {cluster_description}")
        print("=" * 70)

        # Get cluster credentials
        cluster_creds = creds.cred_manager.get_structured_credential(cred_name)
        if not cluster_creds:
            print(f"❌ No credentials found for {cred_name}")
            continue

        hostname = cluster_creds.get("hostname")
        username = cluster_creds.get("username")
        password = cluster_creds.get("password")
        key_file = cluster_creds.get("key_file")

        if not hostname or not username:
            print(f"❌ Invalid credentials for {cred_name}")
            continue

        print(f"🔗 Target: {username}@{hostname}")

        # Test virtual environment functionality
        venv_result = test_venv_creation(hostname, username, password, key_file)
        results[cluster_description] = venv_result

    # Summary
    print("\\n📊 Virtual Environment Validation Summary")
    print("=" * 70)

    passed = 0
    total = len(results)

    for cluster_name, result in results.items():
        if result:
            print(f"✅ {cluster_name}: PASSED")
            passed += 1
        else:
            print(f"❌ {cluster_name}: FAILED")

    print(
        f"\\n🎯 Overall Result: {passed}/{total} clusters support virtual environments"
    )

    if passed > 0:
        print("🎉 Virtual environment validation successful!")
        print("   Clustrix can create isolated Python environments on remote clusters.")
        return 0
    else:
        print("❌ Virtual environment validation failed.")
        print("   Check Python venv support on target clusters.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
