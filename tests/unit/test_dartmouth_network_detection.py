#!/usr/bin/env python3
"""
Test Dartmouth network detection functionality.
"""

from tests.real_world.conftest import is_dartmouth_network

def test_dartmouth_network_detection():
    """Test the Dartmouth network detection function."""
    
    print("Testing Dartmouth network detection...")
    
    is_dartmouth = is_dartmouth_network()
    
    print(f"🔍 Dartmouth network detection result: {is_dartmouth}")
    
    if is_dartmouth:
        print("✅ Currently on Dartmouth network (VPN or on-campus)")
        print("   - tensor01 and ndoli tests will run")
        print("   - GitHub Actions will skip these tests when not on Dartmouth network")
    else:
        print("❌ Not on Dartmouth network")
        print("   - tensor01 and ndoli tests will be skipped")
        print("   - This prevents GitHub Actions failures")
    
    # Test hostname detection
    import socket
    hostname = socket.getfqdn()
    print(f"📋 Current hostname: {hostname}")
    
    # Test tensor01 resolution
    try:
        socket.gethostbyname('tensor01.dartmouth.edu')
        print("✅ Can resolve tensor01.dartmouth.edu")
    except socket.gaierror as e:
        print(f"❌ Cannot resolve tensor01.dartmouth.edu: {e}")
    
    return is_dartmouth

if __name__ == "__main__":
    result = test_dartmouth_network_detection()
    print(f"\n📊 Network Detection Result: {'Dartmouth' if result else 'External'}")