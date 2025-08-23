#!/usr/bin/env python3
"""
Test pytest automatic skipping for Dartmouth network tests.
"""

import pytest

# This should be skipped when not on Dartmouth network
@pytest.mark.dartmouth_network
def test_dartmouth_marker():
    """Test that gets skipped when not on Dartmouth network."""
    print("✅ This test runs because we're on Dartmouth network")
    assert True

def test_tensor01_in_name():
    """Test with tensor01 in name - should be skipped when not on Dartmouth."""
    print("✅ This tensor01 test runs because we're on Dartmouth network")
    assert True

def test_ndoli_in_name():
    """Test with ndoli in name - should be skipped when not on Dartmouth."""
    print("✅ This ndoli test runs because we're on Dartmouth network")
    assert True

def test_regular_test():
    """Regular test that should always run."""
    print("✅ This regular test always runs")
    assert True

if __name__ == "__main__":
    # Run pytest on this file
    import subprocess
    result = subprocess.run(["python", "-m", "pytest", __file__, "-v", "-s"], 
                          capture_output=True, text=True)
    
    print("PYTEST OUTPUT:")
    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)
    
    print(f"Exit code: {result.returncode}")