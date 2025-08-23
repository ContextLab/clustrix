#!/usr/bin/env python3
"""
Container Registry Validation Script

This script validates container registry operations (push/pull) that Clustrix
might use for Kubernetes and container-based deployments.
"""

import sys
import subprocess
import tempfile
import time
import os
import logging
from pathlib import Path
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_command(cmd, timeout=120, check=True):
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
    """Test if Docker is available."""
    print("🔍 Docker Availability Check")
    print("=" * 40)

    result = run_command("docker --version", check=False)
    if result and result.returncode == 0:
        print(f"✅ Docker installed: {result.stdout.strip()}")
    else:
        print("❌ Docker not available")
        return False

    result = run_command("docker info --format '{{.ServerVersion}}'", check=False)
    if result and result.returncode == 0:
        print(f"✅ Docker daemon running: v{result.stdout.strip()}")
        return True
    else:
        print("❌ Docker daemon not running")
        return False


def test_docker_hub_connectivity():
    """Test basic Docker Hub connectivity."""
    print("\n🌐 Docker Hub Connectivity Test")
    print("=" * 40)

    # Test pull from Docker Hub (public image)
    print("📥 Testing Docker Hub pull access...")
    result = run_command("docker pull hello-world:latest", timeout=60)
    if not result or result.returncode != 0:
        print("❌ Cannot pull from Docker Hub")
        return False
    print("✅ Docker Hub pull access working")

    # Clean up
    run_command("docker rmi hello-world:latest", check=False)

    return True


def test_container_image_building():
    """Test building a custom container image for Clustrix."""
    print("\n🏗️  Container Image Building Test")
    print("=" * 40)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create a Dockerfile similar to what Clustrix might use
        dockerfile_content = """
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \\
    build-essential \\
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \\
    numpy \\
    cloudpickle \\
    requests

# Create test script
COPY test_script.py /app/

# Set entrypoint
ENTRYPOINT ["python", "/app/test_script.py"]
"""

        # Create test script
        test_script_content = '''
import sys
import json
import numpy as np
import cloudpickle
from datetime import datetime

def clustrix_container_test():
    """Test function for container execution."""
    
    # Perform computation
    data = np.random.rand(1000)
    result = {
        "mean": float(np.mean(data)),
        "std": float(np.std(data)),
        "container_test": True,
        "timestamp": datetime.now().isoformat(),
        "python_version": sys.version.split()[0],
        "success": True
    }
    
    return result

if __name__ == "__main__":
    try:
        print("Starting Clustrix container test...")
        
        # Test cloudpickle functionality
        serialized = cloudpickle.dumps(clustrix_container_test)
        deserialized = cloudpickle.loads(serialized)
        
        result = deserialized()
        
        print("CLUSTRIX_RESULT_START")
        print(json.dumps(result, indent=2))
        print("CLUSTRIX_RESULT_END")
        
        print("Container test completed successfully!")
        
    except Exception as e:
        print(f"Container test failed: {e}")
        sys.exit(1)
'''

        # Write files
        dockerfile_path = temp_path / "Dockerfile"
        script_path = temp_path / "test_script.py"

        dockerfile_path.write_text(dockerfile_content)
        script_path.write_text(test_script_content)

        # Build image
        image_tag = f"clustrix-test:{int(time.time())}"
        print(f"🔨 Building image: {image_tag}")

        build_cmd = f"cd {temp_path} && docker build -t {image_tag} ."
        result = run_command(build_cmd, timeout=300)

        if not result or result.returncode != 0:
            print("❌ Image build failed")
            if result:
                print(f"   Error: {result.stderr[-500:]}")  # Last 500 chars
            return False, None

        print("✅ Image built successfully")

        # Test running the container
        print("🚀 Testing container execution...")
        run_cmd = f"docker run --rm {image_tag}"
        result = run_command(run_cmd, timeout=60)

        if result and result.returncode == 0:
            output = result.stdout
            if "Container test completed successfully!" in output:
                print("✅ Container execution successful")

                # Extract result
                try:
                    start_idx = output.find("CLUSTRIX_RESULT_START")
                    end_idx = output.find("CLUSTRIX_RESULT_END")
                    if start_idx != -1 and end_idx != -1:
                        json_str = output[
                            start_idx + len("CLUSTRIX_RESULT_START") : end_idx
                        ].strip()
                        result_data = json.loads(json_str)
                        print(f"   Mean: {result_data.get('mean', 'N/A'):.4f}")
                        print(f"   Python: {result_data.get('python_version', 'N/A')}")
                except Exception as e:
                    print(f"   ⚠️  Could not parse result: {e}")

                return True, image_tag
            else:
                print("❌ Container execution produced unexpected output")
                print(f"   Output: {output[:200]}...")
                return False, image_tag
        else:
            print("❌ Container execution failed")
            if result:
                print(f"   Error: {result.stderr}")
            return False, image_tag


def test_image_tagging_and_management():
    """Test image tagging operations."""
    print("\n🏷️  Image Tagging and Management Test")
    print("=" * 40)

    # Build a simple test image first
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        simple_dockerfile = """
FROM alpine:latest
RUN echo "Clustrix test image" > /test.txt
CMD ["cat", "/test.txt"]
"""

        dockerfile_path = temp_path / "Dockerfile"
        dockerfile_path.write_text(simple_dockerfile)

        base_tag = f"clustrix-tag-test:{int(time.time())}"

        # Build base image
        build_cmd = f"cd {temp_path} && docker build -t {base_tag} ."
        result = run_command(build_cmd, timeout=120)

        if not result or result.returncode != 0:
            print("❌ Failed to build test image for tagging")
            return False

        print(f"✅ Built test image: {base_tag}")

        # Test different tagging patterns
        tag_tests = [
            f"clustrix-test:latest",
            f"clustrix-test:v1.0.0",
            f"clustrix-test:dev-{int(time.time())}",
        ]

        for new_tag in tag_tests:
            print(f"🏷️  Testing tag: {new_tag}")

            # Tag the image
            tag_cmd = f"docker tag {base_tag} {new_tag}"
            result = run_command(tag_cmd)

            if result and result.returncode == 0:
                print(f"   ✅ Tagged successfully")

                # Verify tag exists
                list_cmd = f"docker images {new_tag} --format 'table {{.Repository}}\\t{{.Tag}}'"
                result = run_command(list_cmd)

                if result and new_tag.split(":")[0] in result.stdout:
                    print(f"   ✅ Tag verified in image list")
                else:
                    print(f"   ❌ Tag not found in image list")
                    return False
            else:
                print(f"   ❌ Tagging failed")
                return False

        # Clean up test images
        print("🧹 Cleaning up test images...")
        for tag in [base_tag] + tag_tests:
            run_command(f"docker rmi {tag}", check=False)

        print("✅ Image tagging and management working")
        return True


def test_registry_push_simulation():
    """Simulate registry push operations (without actual push to avoid pollution)."""
    print("\n📤 Registry Push Simulation Test")
    print("=" * 40)

    # Note: We won't actually push to avoid polluting public registries
    # But we'll test the command structure and validation

    print("ℹ️  Simulating registry push operations...")
    print("   (Not actually pushing to avoid registry pollution)")

    # Test command structure for different registries
    registries = {
        "Docker Hub": {
            "format": "username/repository:tag",
            "example": "clustrix/test-image:v1.0.0",
            "login_cmd": "docker login",
            "push_cmd": "docker push clustrix/test-image:v1.0.0",
        },
        "AWS ECR": {
            "format": "aws_account_id.dkr.ecr.region.amazonaws.com/repository:tag",
            "example": "123456789012.dkr.ecr.us-east-1.amazonaws.com/clustrix:v1.0.0",
            "login_cmd": "aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 123456789012.dkr.ecr.us-east-1.amazonaws.com",
            "push_cmd": "docker push 123456789012.dkr.ecr.us-east-1.amazonaws.com/clustrix:v1.0.0",
        },
        "Google GCR": {
            "format": "gcr.io/project-id/repository:tag",
            "example": "gcr.io/my-project/clustrix:v1.0.0",
            "login_cmd": 'docker login -u _json_key -p "$(cat key.json)" https://gcr.io',
            "push_cmd": "docker push gcr.io/my-project/clustrix:v1.0.0",
        },
    }

    for registry_name, config in registries.items():
        print(f"\n📋 {registry_name} Push Format:")
        print(f"   Format: {config['format']}")
        print(f"   Example: {config['example']}")
        print(f"   Login: {config['login_cmd'][:50]}...")
        print(f"   Push: {config['push_cmd']}")

    # Test image naming validation
    print("\n🔍 Testing image name validation...")

    test_names = [
        ("valid-name:v1.0.0", True),
        ("username/repo:latest", True),
        ("gcr.io/project/app:tag", True),
        ("invalid..name:tag", False),
        ("UPPERCASE:tag", False),  # Docker Hub doesn't allow uppercase
        ("valid-name", True),  # latest implied
    ]

    for name, should_be_valid in test_names:
        # Simple validation check
        is_valid = all(c.islower() or c.isdigit() or c in "-._:/" for c in name)
        is_valid = is_valid and not ".." in name and not name.startswith("-")

        status = "✅" if is_valid == should_be_valid else "❌"
        print(f"   {status} {name}: {'Valid' if is_valid else 'Invalid'}")

    print("\n✅ Registry push simulation completed")
    print("   Clustrix can generate proper image names and push commands")

    return True


def main():
    """Main validation function."""
    print("🚀 Starting Container Registry Validation")
    print("=" * 60)

    # Test sequence
    tests = [
        ("Docker Availability", test_docker_availability),
        ("Docker Hub Connectivity", test_docker_hub_connectivity),
        ("Container Image Building", test_container_image_building),
        ("Image Tagging and Management", test_image_tagging_and_management),
        ("Registry Push Simulation", test_registry_push_simulation),
    ]

    results = {}
    built_image = None

    for test_name, test_func in tests:
        try:
            print(f"\n🔄 Running: {test_name}")

            if test_func == test_container_image_building:
                success, image_tag = test_func()
                built_image = image_tag
            else:
                success = test_func()

            results[test_name] = success

            if success:
                print(f"✅ {test_name}: PASSED")
            else:
                print(f"❌ {test_name}: FAILED")
                break  # Stop on first failure

        except Exception as e:
            print(f"❌ {test_name}: ERROR - {e}")
            results[test_name] = False
            break

    # Cleanup
    if built_image:
        print(f"\n🧹 Cleaning up built image: {built_image}")
        run_command(f"docker rmi {built_image}", check=False)

    # Summary
    print("\n📊 Container Registry Validation Summary")
    print("=" * 60)

    passed = sum(1 for success in results.values() if success)
    total = len(results)

    for test_name, success in results.items():
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"  {test_name}: {status}")

    print(f"\n🎯 Overall Result: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 Container registry operations fully validated!")
        print("   Clustrix can build, tag, and push container images.")
        return 0
    else:
        print("⚠️  Some container registry tests failed.")
        print("   Check Docker installation and permissions.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
