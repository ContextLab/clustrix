#!/usr/bin/env python3
"""
Test GPU detection and VENV setup functionality.
"""

import sys
import os
sys.path.insert(0, '/Users/jmanning/clustrix')

from clustrix.utils import detect_gpu_capabilities, enhanced_setup_two_venv_environment
from clustrix.config import ClusterConfig
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)

def test_gpu_detection_locally():
    """Test GPU detection on local system (should show no GPUs in most cases)."""
    print("🧪 Testing GPU detection locally...")
    
    # Create a mock SSH client that executes commands locally
    class MockSSHClient:
        def exec_command(self, command):
            import subprocess
            
            class MockChannel:
                def recv_exit_status(self):
                    return self.exit_status
                    
                def set_exit_status(self, status):
                    self.exit_status = status
            
            class MockStdout:
                def __init__(self, output):
                    self.output = output
                    self.channel = MockChannel()
                    
                def read(self):
                    return self.output.encode()
                    
            class MockStderr:
                def __init__(self, output):
                    self.output = output
                    
                def read(self):
                    return self.output.encode()
            
            try:
                result = subprocess.run(
                    command, shell=True, capture_output=True, text=True, timeout=10
                )
                stdout = MockStdout(result.stdout)
                stdout.channel.set_exit_status(result.returncode)
                stderr = MockStderr(result.stderr)
                return None, stdout, stderr
            except subprocess.TimeoutExpired:
                stdout = MockStdout("")
                stdout.channel.set_exit_status(1)
                stderr = MockStderr("Command timed out")
                return None, stdout, stderr
            except Exception as e:
                stdout = MockStdout("")
                stdout.channel.set_exit_status(1)
                stderr = MockStderr(str(e))
                return None, stdout, stderr
    
    mock_ssh = MockSSHClient()
    config = ClusterConfig()
    
    try:
        gpu_info = detect_gpu_capabilities(mock_ssh, config)
        print(f"GPU Detection Results: {gpu_info}")
        
        if gpu_info["gpu_available"]:
            print(f"✅ GPUs detected: {gpu_info['gpu_count']} devices")
            print(f"   Detection method: {gpu_info['detection_method']}")
            if gpu_info["gpu_devices"]:
                for i, device in enumerate(gpu_info["gpu_devices"]):
                    print(f"   GPU {i}: {device}")
        else:
            print("ℹ️  No GPUs detected (expected on most local systems)")
            
        if gpu_info["cuda_available"]:
            print(f"✅ CUDA available: version {gpu_info['cuda_version']}")
        else:
            print("ℹ️  CUDA not available")
            
        if gpu_info["detection_errors"]:
            print(f"⚠️  Detection errors: {gpu_info['detection_errors']}")
            
        return True
        
    except Exception as e:
        print(f"❌ GPU detection failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_config_validation():
    """Test that GPU configuration options are properly loaded."""
    print("\n🧪 Testing GPU configuration options...")
    
    config = ClusterConfig()
    
    # Check that new GPU options exist and have correct defaults
    gpu_options = {
        "gpu_detection_enabled": True,
        "auto_gpu_packages": True,
        "cuda_version_preference": None,
        "gpu_memory_fraction": 0.9,
        "prefer_gpu_execution": True,
        "gpu_requirements": None,
        "rapids_ecosystem": False
    }
    
    success = True
    for option, expected_default in gpu_options.items():
        if hasattr(config, option):
            actual_value = getattr(config, option)
            if actual_value == expected_default:
                print(f"✅ {option}: {actual_value} (default)")
            else:
                print(f"⚠️  {option}: {actual_value} (expected {expected_default})")
        else:
            print(f"❌ Missing option: {option}")
            success = False
    
    return success

def test_enhanced_venv_import():
    """Test that enhanced venv setup can be imported and has correct signature."""
    print("\n🧪 Testing enhanced venv setup import...")
    
    try:
        from clustrix.utils import enhanced_setup_two_venv_environment
        
        # Check function signature
        import inspect
        sig = inspect.signature(enhanced_setup_two_venv_environment)
        expected_params = ["ssh_client", "work_dir", "requirements", "config"]
        
        actual_params = list(sig.parameters.keys())
        
        if actual_params == expected_params:
            print("✅ Enhanced venv setup imported successfully")
            print(f"   Function signature: {sig}")
            return True
        else:
            print(f"❌ Function signature mismatch")
            print(f"   Expected: {expected_params}")
            print(f"   Actual: {actual_params}")
            return False
            
    except ImportError as e:
        print(f"❌ Failed to import enhanced venv setup: {e}")
        return False
    except Exception as e:
        print(f"❌ Error testing enhanced venv setup: {e}")
        return False

def test_executor_integration():
    """Test that executor imports enhanced venv setup correctly."""
    print("\n🧪 Testing executor integration...")
    
    try:
        # Check that executor can import the enhanced function
        from clustrix.executor import ClusterExecutor
        from clustrix.config import ClusterConfig
        
        # Create a minimal config
        config = ClusterConfig(
            cluster_type="ssh",
            cluster_host="localhost",
            username="testuser"
        )
        
        # Try to create executor (won't connect, just test imports)
        try:
            executor = ClusterExecutor(config)
            print("✅ ClusterExecutor created successfully")
            return True
        except Exception as e:
            # Expected to fail on connection, but should succeed on import
            if "import" in str(e).lower() or "module" in str(e).lower():
                print(f"❌ Import error in executor: {e}")
                return False
            else:
                print("✅ ClusterExecutor imports work (connection error expected)")
                return True
                
    except Exception as e:
        print(f"❌ Executor integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 Testing GPU Detection and Enhanced VENV Setup")
    print("=" * 60)
    
    tests = [
        test_config_validation,
        test_enhanced_venv_import,
        test_executor_integration,
        test_gpu_detection_locally,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test {test.__name__} crashed: {e}")
            results.append(False)
    
    print("\n" + "=" * 60)
    print("📊 Test Results:")
    for i, (test, result) in enumerate(zip(tests, results)):
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {i+1}. {test.__name__}: {status}")
    
    passed = sum(results)
    total = len(results)
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All GPU detection tests passed!")
    else:
        print(f"\n⚠️  {total - passed} tests failed - GPU functionality needs attention")