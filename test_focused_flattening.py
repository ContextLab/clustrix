#!/usr/bin/env python3
"""
Focused test of function flattening without full project analysis.
"""

import sys
import os
sys.path.insert(0, '/Users/jmanning/clustrix')

from clustrix.function_flattening import analyze_function_complexity, auto_flatten_if_needed
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)

def test_basic_nested_function():
    """Test basic nested function detection and flattening."""
    print("🧪 Testing basic nested function...")
    
    def outer_function(x, y):
        """Function with a simple nested function."""
        def inner_add(a, b):
            return a + b
        
        result = inner_add(x, y)
        return result * 2
    
    # Test complexity analysis first
    complexity = analyze_function_complexity(outer_function)
    print(f"Complexity analysis: {complexity}")
    
    # Check if nested functions are detected
    if complexity.get("nested_functions", 0) > 0:
        print(f"✅ Nested functions detected: {complexity['nested_functions']}")
        print(f"✅ Function marked as complex: {complexity['is_complex']}")
        
        # Test auto flattening
        try:
            flattened_func, flattening_info = auto_flatten_if_needed(outer_function)
            
            if flattening_info:
                print(f"Flattening attempted: {flattening_info}")
                
                if flattening_info.get("success"):
                    print("✅ Flattening successful!")
                    
                    # Test execution
                    original_result = outer_function(3, 4)
                    print(f"Original result: {original_result}")
                    
                    try:
                        flattened_result = flattened_func(3, 4)
                        print(f"Flattened result: {flattened_result}")
                        
                        if original_result == flattened_result:
                            print("✅ Results match!")
                            return True
                        else:
                            print("❌ Results don't match")
                            return False
                    except Exception as e:
                        print(f"❌ Flattened function execution failed: {e}")
                        return False
                else:
                    print(f"❌ Flattening failed: {flattening_info.get('error')}")
                    return False
            else:
                print("❌ No flattening attempted")
                return False
                
        except Exception as e:
            print(f"❌ Auto flattening crashed: {e}")
            import traceback
            traceback.print_exc()
            return False
    else:
        print("❌ Nested functions not detected")
        return False

def test_inline_function_pattern():
    """Test the exact pattern from our failing test case."""
    print("\n🧪 Testing inline function pattern...")
    
    def test_simple_gpu_computation():
        """Container function similar to test case."""
        
        def simple_gpu_matrix_mult():
            """Inline function that would fail serialization."""
            # Simulate GPU computation without actually importing torch
            return {
                "success": True,
                "device": "cuda:0",
                "result_shape": [100, 100],
                "result_mean": 0.021056,
                "result_std": 9.934280,
                "cuda_available": True,
                "gpu_count": 8
            }
        
        # Execute the inline function
        result = simple_gpu_matrix_mult()
        return result
    
    # Analyze complexity
    complexity = analyze_function_complexity(test_simple_gpu_computation)
    print(f"Complexity: {complexity}")
    
    if complexity.get("nested_functions", 0) > 0:
        print(f"✅ Nested function detected in inline pattern")
        
        # Test flattening
        try:
            flattened_func, flattening_info = auto_flatten_if_needed(test_simple_gpu_computation)
            
            if flattening_info and flattening_info.get("success"):
                print("✅ Inline function flattened successfully")
                
                # Test execution
                original_result = test_simple_gpu_computation()
                flattened_result = flattened_func()
                
                print(f"Original: {original_result}")
                print(f"Flattened: {flattened_result}")
                
                if original_result == flattened_result:
                    print("✅ Inline function flattening preserves behavior")
                    return True
                else:
                    print("❌ Results don't match")
                    return False
            else:
                print(f"❌ Inline function flattening failed: {flattening_info}")
                return False
                
        except Exception as e:
            print(f"❌ Inline function test crashed: {e}")
            import traceback
            traceback.print_exc()
            return False
    else:
        print("❌ Nested function not detected in inline pattern")
        return False

def test_complexity_threshold():
    """Test that complexity threshold properly triggers flattening."""
    print("\n🧪 Testing complexity threshold...")
    
    # Simple function (should not be flattened)
    def simple_function(x):
        return x * 2
    
    # Complex function with nested function (should be flattened)
    def complex_function(data):
        def process_item(item):
            return item * 2
        
        def filter_item(item):
            return item > 5
        
        results = []
        for item in data:
            processed = process_item(item)
            if filter_item(processed):
                results.append(processed)
        
        return results
    
    # Test simple function
    simple_complexity = analyze_function_complexity(simple_function)
    print(f"Simple function complexity: {simple_complexity}")
    
    simple_flattened, simple_info = auto_flatten_if_needed(simple_function)
    
    if simple_info is None:
        print("✅ Simple function not flattened (correct)")
    else:
        print("❌ Simple function was flattened (incorrect)")
        return False
    
    # Test complex function
    complex_complexity = analyze_function_complexity(complex_function)
    print(f"Complex function complexity: {complex_complexity}")
    
    complex_flattened, complex_info = auto_flatten_if_needed(complex_function)
    
    if complex_info and complex_info.get("success"):
        print("✅ Complex function was flattened (correct)")
        
        # Test behavior preservation
        test_data = [1, 2, 3, 4, 5, 6]
        original_result = complex_function(test_data)
        flattened_result = complex_flattened(test_data)
        
        print(f"Original: {original_result}")
        print(f"Flattened: {flattened_result}")
        
        if original_result == flattened_result:
            print("✅ Complex function flattening preserves behavior")
            return True
        else:
            print("❌ Results don't match")
            return False
    else:
        print(f"❌ Complex function was not flattened: {complex_info}")
        return False

if __name__ == "__main__":
    print("🚀 Focused Function Flattening Tests")
    print("=" * 50)
    
    tests = [
        test_basic_nested_function,
        test_inline_function_pattern,
        test_complexity_threshold,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test {test.__name__} crashed: {e}")
            results.append(False)
    
    print("\n" + "=" * 50)
    print("📊 Test Results:")
    for i, (test, result) in enumerate(zip(tests, results)):
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {i+1}. {test.__name__}: {status}")
    
    passed = sum(results)
    total = len(results)
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All focused flattening tests passed!")
    else:
        print(f"\n⚠️  {total - passed} tests failed - more work needed")