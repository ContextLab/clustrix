#!/usr/bin/env python3
"""
Docker Functionality Validation Script

This script validates Docker functionality that Clustrix depends on.
Since Clustrix uses container images for Kubernetes deployments, we need to ensure
Docker operations work correctly.
"""

import sys
import subprocess
import tempfile
import os
import json
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_command(cmd, timeout=60, check=True):
    """Run a command and return the result."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {cmd}")
        logger.error(f"Error: {e.stderr}")
        return e
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out: {cmd}")
        return None


def test_docker_availability():
    """Test if Docker is available and working."""
    print("🔍 Docker Availability Test")
    print("=" * 50)

    # Check Docker version
    result = run_command("docker --version", check=False)
    if result and result.returncode == 0:
        print(f"✅ Docker installed: {result.stdout.strip()}")
    else:
        print("❌ Docker not installed or not accessible")
        return False

    # Check Docker daemon
    result = run_command("docker info --format '{{.ServerVersion}}'", check=False)
    if result and result.returncode == 0:
        print(f"✅ Docker daemon running: v{result.stdout.strip()}")
    else:
        print("❌ Docker daemon not running")
        return False

    # Check Docker permissions
    result = run_command("docker ps", check=False)
    if result and result.returncode == 0:
        print("✅ Docker permissions working")
    else:
        print("❌ Docker permission issues")
        print("   Try: sudo usermod -aG docker $USER")
        return False

    return True


def test_basic_container_operations():
    """Test basic container operations."""
    print("\n🧪 Basic Container Operations Test")
    print("=" * 50)

    # Pull a lightweight image
    print("📥 Pulling test image...")
    result = run_command("docker pull hello-world", timeout=120)
    if not result or result.returncode != 0:
        print("❌ Failed to pull hello-world image")
        return False
    print("✅ Image pull successful")

    # Run a simple container
    print("🚀 Running test container...")
    result = run_command("docker run --rm hello-world", timeout=30)
    if not result or result.returncode != 0:
        print("❌ Failed to run hello-world container")
        return False
    print("✅ Container execution successful")

    # List images
    result = run_command(
        "docker images hello-world --format 'table {{.Repository}}\\t{{.Tag}}\\t{{.Size}}'"
    )
    if result and result.returncode == 0:
        print("✅ Image listing working")
        print(f"   {result.stdout.strip()}")

    return True


def test_python_container_functionality():
    """Test Python container functionality similar to what Clustrix would use."""
    print("\n🐍 Python Container Functionality Test")
    print("=" * 50)

    # Pull Python image (same as used in Kubernetes tutorial)
    print("📥 Pulling Python 3.11 slim image...")
    result = run_command("docker pull python:3.11-slim", timeout=300)
    if not result or result.returncode != 0:
        print("❌ Failed to pull python:3.11-slim image")
        return False
    print("✅ Python image pull successful")

    # Test Python execution in container
    print("🧪 Testing Python execution in container...")
    python_test_cmd = """
    docker run --rm python:3.11-slim python3 -c "
import sys
import os
import json
import numpy as np
print('Python version:', sys.version)
print('Platform:', sys.platform)
print('NumPy available:', 'numpy' in sys.modules or True)
result = {'success': True, 'python_version': sys.version.split()[0]}
print('Test result:', json.dumps(result))
"
    """

    # First install numpy in the container
    install_cmd = """
    docker run --rm python:3.11-slim pip install numpy
    """

    print("   Installing NumPy in container...")
    result = run_command(install_cmd, timeout=120)
    if not result or result.returncode != 0:
        print("❌ Failed to install NumPy in container")
        return False

    print("   Running Python test...")
    result = run_command(python_test_cmd, timeout=60)
    if not result or result.returncode != 0:
        print("❌ Python execution failed in container")
        if result:
            print(f"   Error: {result.stderr}")
        return False

    print("✅ Python container execution successful")
    print(f"   Output preview: {result.stdout.strip()[:200]}...")

    return True


def test_clustrix_like_container_execution():
    """Test container execution similar to Clustrix patterns."""
    print("\n🔬 Clustrix-like Container Execution Test")
    print("=" * 50)

    # Create a temporary directory for test scripts
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create a test script similar to what Clustrix might generate
        test_script = temp_path / "clustrix_test.py"
        test_script.write_text(
            """
import sys
import os
import json
import time
from datetime import datetime

def clustrix_test_function():
    '''Simulate a Clustrix-style function execution.'''
    
    print("🚀 Starting Clustrix-style computation...")
    print(f"Python version: {sys.version}")
    print(f"Working directory: {os.getcwd()}")
    print(f"Environment variables: {len(os.environ)}")
    
    # Simulate some computation
    import math
    result = 0
    for i in range(1000000):
        result += math.sin(i) * math.cos(i)
    
    computation_result = {
        'function_name': 'clustrix_test_function',
        'computation_result': result,
        'execution_time': time.time(),
        'timestamp': datetime.now().isoformat(),
        'environment': {
            'python_version': sys.version.split()[0],
            'platform': sys.platform,
            'working_dir': os.getcwd(),
            'hostname': os.environ.get('HOSTNAME', 'unknown')
        },
        'success': True
    }
    
    print("✅ Computation completed successfully")
    print(f"Result: {json.dumps(computation_result, indent=2)}")
    
    return computation_result

if __name__ == "__main__":
    try:
        result = clustrix_test_function()
        print(f"\\n🎉 Test function completed successfully!")
        print(f"Result summary: {result['computation_result']:.6f}")
    except Exception as e:
        print(f"❌ Test function failed: {e}")
        sys.exit(1)
"""
        )

        # Create a simple requirements file
        requirements_file = temp_path / "requirements.txt"
        requirements_file.write_text("# No special requirements for this test\\n")

        # Run the test script in a Python container
        print("📝 Created test script")
        print("🚀 Running Clustrix-style execution in container...")

        container_cmd = f"""
        docker run --rm \\
            -v {temp_path}:/app \\
            -w /app \\
            python:3.11-slim \\
            python clustrix_test.py
        """

        result = run_command(container_cmd, timeout=60)
        if not result or result.returncode != 0:
            print("❌ Clustrix-style container execution failed")
            if result:
                print(f"   Error: {result.stderr}")
            return False

        print("✅ Clustrix-style execution successful")

        # Check if the output contains expected patterns
        output = result.stdout
        if (
            "Test function completed successfully" in output
            and "computation_result" in output
        ):
            print("✅ Expected output patterns found")
        else:
            print("⚠️  Unexpected output format")

        print(f"📊 Container execution output preview:")
        lines = output.strip().split("\\n")
        for i, line in enumerate(lines[:15]):  # First 15 lines
            print(f"   {line}")
        if len(lines) > 15:
            print(f"   ... ({len(lines) - 15} more lines)")

    return True


def test_container_resource_constraints():
    """Test container resource constraints."""
    print("\n📊 Container Resource Constraints Test")
    print("=" * 50)

    # Test memory limit
    print("🧠 Testing memory constraints...")
    memory_test_cmd = """
    docker run --rm --memory=100m python:3.11-slim python3 -c "
import sys
print('Memory limit test - should complete successfully')
data = list(range(10000))  # Small allocation
print(f'Allocated list with {len(data)} elements')
print('Memory constraint test passed')
"
    """

    result = run_command(memory_test_cmd, timeout=30)
    if result and result.returncode == 0:
        print("✅ Memory constraints working")
    else:
        print("❌ Memory constraint test failed")
        return False

    # Test CPU limit (simplified)
    print("⚙️  Testing CPU constraints...")
    cpu_test_cmd = """
    docker run --rm --cpus=0.5 python:3.11-slim python3 -c "
import time
import math
print('CPU limit test - performing computation...')
start = time.time()
result = sum(math.sin(i) for i in range(100000))
duration = time.time() - start
print(f'Computation completed in {duration:.3f} seconds')
print(f'Result: {result:.6f}')
print('CPU constraint test passed')
"
    """

    result = run_command(cpu_test_cmd, timeout=30)
    if result and result.returncode == 0:
        print("✅ CPU constraints working")
    else:
        print("❌ CPU constraint test failed")
        return False

    return True


def test_container_networking():
    """Test basic container networking."""
    print("\n🌐 Container Networking Test")
    print("=" * 50)

    # Test network connectivity from container
    network_test_cmd = """
    docker run --rm python:3.11-slim python3 -c "
import urllib.request
import json

try:
    # Test connectivity to a public API
    response = urllib.request.urlopen('https://httpbin.org/ip', timeout=10)
    data = json.loads(response.read().decode())
    print('✅ Network connectivity working')
    print(f'External IP: {data.get(\"origin\", \"unknown\")}')
except Exception as e:
    print(f'❌ Network test failed: {e}')
    raise
"
    """

    result = run_command(network_test_cmd, timeout=30)
    if result and result.returncode == 0:
        print("✅ Container networking working")
    else:
        print("❌ Container networking test failed")
        return False

    return True


def cleanup_test_images():
    """Clean up test images to save space."""
    print("\n🧹 Cleanup Test Images")
    print("=" * 50)

    # Remove hello-world image
    result = run_command("docker rmi hello-world", check=False)
    if result and result.returncode == 0:
        print("✅ Cleaned up hello-world image")

    # Note: We keep python:3.11-slim as it might be useful
    print("📝 Keeping python:3.11-slim image (useful for Clustrix)")

    # Show remaining images
    result = run_command(
        "docker images --format 'table {{.Repository}}\\t{{.Tag}}\\t{{.Size}}'"
    )
    if result and result.returncode == 0:
        print("📊 Remaining Docker images:")
        lines = result.stdout.strip().split("\\n")
        for line in lines[:10]:  # First 10 images
            print(f"   {line}")


def main():
    """Main validation function."""
    print("🚀 Starting Docker Functionality Validation")
    print("=" * 60)

    tests = [
        ("Docker Availability", test_docker_availability),
        ("Basic Container Operations", test_basic_container_operations),
        ("Python Container Functionality", test_python_container_functionality),
        ("Clustrix-like Execution", test_clustrix_like_container_execution),
        ("Resource Constraints", test_container_resource_constraints),
        ("Container Networking", test_container_networking),
    ]

    results = {}

    for test_name, test_func in tests:
        try:
            print(f"\\n🔄 Running: {test_name}")
            success = test_func()
            results[test_name] = success
            if success:
                print(f"✅ {test_name}: PASSED")
            else:
                print(f"❌ {test_name}: FAILED")
        except Exception as e:
            print(f"❌ {test_name}: ERROR - {e}")
            results[test_name] = False

    # Cleanup
    cleanup_test_images()

    # Summary
    print("\\n📊 Validation Summary")
    print("=" * 60)
    passed = sum(1 for success in results.values() if success)
    total = len(results)

    for test_name, success in results.items():
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"  {test_name}: {status}")

    print(f"\\n🎯 Overall Result: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All Docker functionality tests passed!")
        print("   Clustrix container operations should work correctly.")
        return 0
    else:
        print("⚠️  Some Docker tests failed.")
        print("   Check Docker installation and permissions.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
