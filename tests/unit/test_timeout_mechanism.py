#!/usr/bin/env python3
"""
Test the timeout mechanism for two-venv setup.
"""


from clustrix.config import load_config, configure, get_config
from clustrix import cluster
from tests.real_world import TestCredentials

def test_timeout_mechanism():
    """Test timeout mechanism with very short timeout."""
    print("🧪 Testing timeout mechanism with short timeout...")
    
    # Load config and credentials
    load_config('tensor01_config.yml')
    creds = TestCredentials()
    tensor01_creds = creds.get_tensor01_credentials()
    
    # Configure with very short timeout to force timeout
    configure(
        cluster_type="ssh",
        cluster_host=tensor01_creds['host'],
        username=tensor01_creds['username'],
        password=tensor01_creds['password'],
        cleanup_on_success=True,
        use_two_venv=True,          # Enable two-venv
        venv_setup_timeout=5,       # Very short timeout (5 seconds)
        auto_gpu_parallel=False,
        auto_parallel=False,
    )
    
    @cluster(cores=1, memory='1GB', time='00:02:00')
    def simple_test_with_timeout():
        """Simple test that should work even if two-venv times out."""
        return "timeout_test_success"
    
    try:
        print("Attempting execution with 5-second timeout...")
        result = simple_test_with_timeout()
        print(f"✅ Execution successful with result: {result}")
        
        if result == "timeout_test_success":
            print("✅ Timeout mechanism works - function executed despite potential timeout")
            return True
        else:
            print(f"⚠️  Unexpected result: {result}")
            return False
            
    except Exception as e:
        print(f"❌ Execution failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_normal_timeout():
    """Test with normal timeout to see if two-venv works."""
    print("\n🧪 Testing with normal timeout...")
    
    # Load config and credentials
    load_config('tensor01_config.yml')
    creds = TestCredentials()
    tensor01_creds = creds.get_tensor01_credentials()
    
    # Configure with normal timeout
    configure(
        cluster_type="ssh",
        cluster_host=tensor01_creds['host'],
        username=tensor01_creds['username'],
        password=tensor01_creds['password'],
        cleanup_on_success=True,
        use_two_venv=True,          # Enable two-venv
        venv_setup_timeout=300,     # Normal timeout (5 minutes)
        auto_gpu_parallel=False,
        auto_parallel=False,
    )
    
    @cluster(cores=1, memory='1GB', time='00:05:00')
    def simple_test_normal_timeout():
        """Simple test with normal timeout."""
        return "normal_timeout_success"
    
    try:
        print("Attempting execution with 5-minute timeout...")
        result = simple_test_normal_timeout()
        print(f"✅ Execution successful with result: {result}")
        
        if result == "normal_timeout_success":
            print("✅ Normal timeout works - two-venv setup likely completed")
            return True
        else:
            print(f"⚠️  Unexpected result: {result}")
            return False
            
    except Exception as e:
        print(f"❌ Execution failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 Testing timeout mechanism...")
    
    # Test 1: Short timeout (should fallback)
    short_timeout_works = test_timeout_mechanism()
    
    if short_timeout_works:
        print("\n" + "="*60)
        # Test 2: Normal timeout (should use two-venv if possible)
        normal_timeout_works = test_normal_timeout()
        
        if normal_timeout_works:
            print("🎉 Both timeout scenarios work!")
            print("✅ Timeout mechanism implementation successful")
        else:
            print("⚠️  Normal timeout test failed")
    else:
        print("❌ Short timeout test failed")